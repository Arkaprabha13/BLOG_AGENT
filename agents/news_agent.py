"""
agents/news_agent.py — LLM-Powered News Curator

Fetches fresh news from NewsClient (all 3 sources), then uses Groq
to select and craft 5–10 catchy, SEO-ready blog topic suggestions.

Each suggestion:
    {
        "title"  : str   — catchy, click-worthy blog title
        "niche"  : str   — content niche slug
        "reason" : str   — why this is timely / shareable
        "hook"   : str   — suggested opening sentence  
        "raw_url": str   — original news URL for reference
    }

Duplicate check is run against the DB so we never suggest a topic
that has already been published.
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from config import get_settings

logger   = logging.getLogger(__name__)
settings = get_settings()

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class BlogSuggestion:
    title:   str
    niche:   str
    reason:  str
    hook:    str
    raw_url: str = ""


# ---------------------------------------------------------------------------
# NewsAgent
# ---------------------------------------------------------------------------

_CURATION_SYSTEM = """\
You are a senior tech content strategist who turns raw news headlines into \
irresistible, SEO-optimised blog topic ideas. Your audience is developers, \
data scientists, and tech-savvy professionals.

Given a list of news articles, select the BEST 5 to 10 and for each one craft:
1. A catchy, click-worthy blog title (max 12 words). Do NOT include the source name.
2. The most relevant niche (use one of: artificial-intelligence, data-centres, \
tech-companies, geopolitics, education, cybersecurity, web-development, technology).
3. A one-sentence reason why this article makes a great blog post right now.
4. A punchy opening hook sentence for the blog (max 20 words).

Prioritise topics that are:
- Currently hot in the developer / AI community
- Actionable or educational (tutorials, deep-dives, explainers)
- Not generic fluff — specific, timely, substantive

Respond ONLY with a valid JSON array. Example format:
[
  {
    "title":   "Why GPT-5 Will Change Software Engineering Forever",
    "niche":   "artificial-intelligence",
    "reason":  "OpenAI's latest model benchmarks show a 40% jump in code quality.",
    "hook":    "Software engineers just got a new co-pilot — and it writes better code than most seniors.",
    "raw_url": "https://..."
  }
]
Do not add any text before or after the JSON array.
"""


class NewsAgent:
    """
    Curates fresh news into blog topic suggestions using Groq LLM.
    """

    def __init__(self, max_news_articles: int = 40):
        self.max_news_articles = max_news_articles

    async def get_suggestions(
        self,
        count: int = 8,
        filter_existing: bool = True,
    ) -> list[BlogSuggestion]:
        """
        Main entry point.

        Args:
            count:           Target number of suggestions (5–10).
            filter_existing: If True, remove topics similar to existing DB blogs.

        Returns:
            list[BlogSuggestion] — ready to display or auto-generate from.
        """
        # 1. Fetch news
        articles = await self._fetch_news()
        if not articles:
            logger.warning("[NewsAgent] No news articles fetched — falling back to free sources")
            articles = await self._fallback_free_sources()

        if not articles:
            logger.error("[NewsAgent] All news sources returned empty")
            return []

        # 2. LLM curation
        raw_suggestions = await self._curate_with_llm(articles, target=count)

        # 3. Duplicate filter
        if filter_existing:
            raw_suggestions = await self._filter_duplicates(raw_suggestions)

        logger.info("[NewsAgent] Final suggestions: %d", len(raw_suggestions))
        return raw_suggestions[:count]

    # ------------------------------------------------------------------
    # Fetch news
    # ------------------------------------------------------------------

    async def _fetch_news(self) -> list[dict]:
        try:
            from clients.news_client import NewsClient
            client = NewsClient()
            articles = await client.fetch_all(max_per_source=self.max_news_articles // 3)
            return articles
        except Exception as exc:
            logger.warning("[NewsAgent] NewsClient failed: %s", exc)
            return []

    async def _fallback_free_sources(self) -> list[dict]:
        """Use TrendsClient (HN/Reddit/GitHub) as a last resort."""
        try:
            from clients.trends_client import TrendsClient
            topics = await TrendsClient().fetch_trending(limit=20)
            # Convert TrendsClient format to news article format
            return [
                {
                    "title":        t["topic"],
                    "description":  "",
                    "url":          "",
                    "source":       t["source"],
                    "published_at": "",
                    "niche":        t["niche"],
                }
                for t in topics
            ]
        except Exception as exc:
            logger.warning("[NewsAgent] Free fallback also failed: %s", exc)
            return []

    # ------------------------------------------------------------------
    # LLM curation
    # ------------------------------------------------------------------

    async def _curate_with_llm(
        self,
        articles: list[dict],
        target: int = 8,
    ) -> list[BlogSuggestion]:
        """Send articles to Groq and parse the returned JSON suggestions."""

        # Build a compact article list for the prompt
        article_lines = []
        for i, art in enumerate(articles[: self.max_news_articles], 1):
            niche = art.get("niche", "")
            title = art.get("title", "")
            desc  = art.get("description", "")
            url   = art.get("url", "")
            article_lines.append(
                f"{i}. [{niche}] {title}"
                + (f" — {desc[:150]}" if desc else "")
                + (f" ({url})" if url else "")
            )
        articles_text = "\n".join(article_lines)

        user_prompt = (
            f"Here are today's {len(article_lines)} news articles. "
            f"Select the best {target} and return JSON suggestions:\n\n"
            f"{articles_text}"
        )

        try:
            from clients.groq_client import GroqClient
            groq = GroqClient()
            response = await groq.chat(
                system_prompt=_CURATION_SYSTEM,
                user_prompt=user_prompt,
                temperature=0.7,
                max_tokens=2048,
            )
            raw_text = response.strip()

            # Strip markdown code fences if present
            raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
            raw_text = re.sub(r"\s*```$", "", raw_text)

            parsed = json.loads(raw_text)
            suggestions = []
            for item in parsed:
                suggestions.append(BlogSuggestion(
                    title   = str(item.get("title", "")).strip(),
                    niche   = str(item.get("niche", "technology")).strip(),
                    reason  = str(item.get("reason", "")).strip(),
                    hook    = str(item.get("hook", "")).strip(),
                    raw_url = str(item.get("raw_url", "")).strip(),
                ))
            logger.info("[NewsAgent] LLM returned %d suggestions", len(suggestions))
            return suggestions

        except json.JSONDecodeError as exc:
            logger.error("[NewsAgent] JSON parse failed: %s", exc)
            return []
        except Exception as exc:
            logger.error("[NewsAgent] LLM curation failed: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Duplicate filter
    # ------------------------------------------------------------------

    async def _filter_duplicates(
        self,
        suggestions: list[BlogSuggestion],
        threshold: int = 3,
    ) -> list[BlogSuggestion]:
        """Remove suggestions too similar to existing published blogs."""
        try:
            from database import find_similar_blogs
            filtered = []
            for s in suggestions:
                similar = await find_similar_blogs(s.title, threshold=threshold)
                if similar:
                    logger.debug(
                        "[NewsAgent] Filtered duplicate: %r matches %r",
                        s.title, similar[0].get("title"),
                    )
                else:
                    filtered.append(s)
            return filtered
        except Exception as exc:
            logger.warning("[NewsAgent] Duplicate filter failed: %s", exc)
            return suggestions   # Return unfiltered on error
