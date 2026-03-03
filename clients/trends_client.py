"""
clients/trends_client.py — Free Trending Topics Fetcher

Fetches trending topics from FREE, auth-free sources:
  1. HackerNews Top Stories API (official Hacker News Firebase API)
  2. Reddit r/programming + r/MachineLearning top posts (public JSON, no auth)
  3. GitHub Trending (public HTML scrape)

No API keys required. Returns a list of deduplicated topic dicts.
"""

import asyncio
import logging
import re
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Source URLs
# ---------------------------------------------------------------------------
HN_TOP_STORIES    = "https://hacker-news.firebaseio.com/v0/topstories.json"
HN_ITEM_URL       = "https://hacker-news.firebaseio.com/v0/item/{id}.json"
REDDIT_URLS = [
    "https://www.reddit.com/r/programming/top.json?limit=15&t=day",
    "https://www.reddit.com/r/MachineLearning/top.json?limit=10&t=day",
    "https://www.reddit.com/r/webdev/top.json?limit=10&t=day",
]
GITHUB_TRENDING   = "https://github.com/trending"

# Niche map: keywords in title → niche label
_NICHE_MAP = {
    "python":       "python",
    "javascript":   "javascript",
    "typescript":   "javascript",
    "react":        "javascript",
    "rust":         "systems-programming",
    "go ":          "golang",
    "golang":       "golang",
    "ai ":          "artificial-intelligence",
    "machine learn":"artificial-intelligence",
    "llm":          "artificial-intelligence",
    "gpt":          "artificial-intelligence",
    "openai":       "artificial-intelligence",
    "database":     "databases",
    "sql":          "databases",
    "postgres":     "databases",
    "redis":        "databases",
    "docker":       "devops",
    "kubernetes":   "devops",
    "cloud":        "cloud-computing",
    "aws":          "cloud-computing",
    "security":     "cybersecurity",
    "linux":        "linux",
    "web":          "web-development",
    "api":          "web-development",
}

# Stopwords for titles likely not useful as blog topics
_SKIP_PATTERNS = re.compile(
    r"(show hn|ask hn|tell hn|hiring|i made|rant|who is|monthly|weekly|daily|thread)",
    re.IGNORECASE,
)


def _guess_niche(title: str) -> str:
    t = title.lower()
    for keyword, niche in _NICHE_MAP.items():
        if keyword in t:
            return niche
    return "technology"


def _clean_title(raw: str) -> str:
    """Strip common HN/Reddit prefixes and return a clean topic string."""
    raw = re.sub(r"\[.*?\]|\(.*?\)", "", raw).strip()
    raw = re.sub(r"\s+", " ", raw)
    return raw[:120]


class TrendsClient:
    """Fetches trending tech topics from free public APIs — no key needed."""

    def __init__(self, timeout: int = 10):
        self.timeout = timeout

    async def fetch_trending(self, limit: int = 20) -> list[dict]:
        """
        Returns up to `limit` deduplicated trending topics as:
            {"topic": str, "niche": str, "source": str}
        Runs all sources in parallel; gracefully handles failures.
        """
        results = await asyncio.gather(
            self._fetch_hackernews(limit // 2),
            self._fetch_reddit(limit // 3),
            self._fetch_github_trending(5),
            return_exceptions=True,
        )

        combined: list[dict] = []
        for r in results:
            if isinstance(r, Exception):
                logger.warning("[Trends] Source failed: %s", r)
            elif isinstance(r, list):
                combined.extend(r)

        # Deduplicate by normalised first 5 words
        seen: set[str] = set()
        unique: list[dict] = []
        for item in combined:
            key = " ".join(item["topic"].lower().split()[:5])
            if key and key not in seen and not _SKIP_PATTERNS.search(item["topic"]):
                seen.add(key)
                unique.append(item)

        logger.info("[Trends] Fetched %d unique trending topics", len(unique))
        return unique[:limit]

    # ------------------------------------------------------------------
    # Hacker News (official Firebase REST API — completely free)
    # ------------------------------------------------------------------
    async def _fetch_hackernews(self, n: int = 10) -> list[dict]:
        topics = []
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.get(HN_TOP_STORIES)
                r.raise_for_status()
                ids = r.json()[:n * 3]  # over-fetch to allow filtering

                # Fetch items in parallel (max 30 concurrent)
                tasks = [
                    client.get(HN_ITEM_URL.format(id=item_id))
                    for item_id in ids[:30]
                ]
                responses = await asyncio.gather(*tasks, return_exceptions=True)

            for resp in responses:
                if isinstance(resp, Exception):
                    continue
                try:
                    item = resp.json()
                    title = _clean_title(item.get("title", ""))
                    if title and item.get("type") == "story" and len(title.split()) > 3:
                        topics.append({
                            "topic":  title,
                            "niche":  _guess_niche(title),
                            "source": "hackernews",
                            "score":  item.get("score", 0),
                        })
                except Exception:
                    continue

            # Sort by HN score
            topics.sort(key=lambda x: x.get("score", 0), reverse=True)
            logger.info("[Trends] HackerNews: %d topics fetched", len(topics))
        except Exception as exc:
            logger.warning("[Trends] HackerNews failed: %s", exc)
        return topics[:n]

    # ------------------------------------------------------------------
    # Reddit (public JSON, no auth, no API key)
    # ------------------------------------------------------------------
    async def _fetch_reddit(self, n: int = 8) -> list[dict]:
        topics = []
        try:
            headers = {"User-Agent": "BlogEmpire/1.0 (autonomous-blog-bot)"}
            async with httpx.AsyncClient(timeout=self.timeout, headers=headers) as client:
                for url in REDDIT_URLS:
                    try:
                        r = await client.get(url)
                        r.raise_for_status()
                        posts = r.json().get("data", {}).get("children", [])
                        for post in posts:
                            d = post.get("data", {})
                            title = _clean_title(d.get("title", ""))
                            if title and len(title.split()) > 4 and not d.get("is_self", False):
                                topics.append({
                                    "topic":  title,
                                    "niche":  _guess_niche(title),
                                    "source": "reddit",
                                    "score":  d.get("score", 0),
                                })
                    except Exception as e:
                        logger.debug("[Trends] Reddit %s failed: %s", url, e)

            topics.sort(key=lambda x: x.get("score", 0), reverse=True)
            logger.info("[Trends] Reddit: %d topics fetched", len(topics))
        except Exception as exc:
            logger.warning("[Trends] Reddit failed: %s", exc)
        return topics[:n]

    # ------------------------------------------------------------------
    # GitHub Trending (public HTML — no auth)
    # ------------------------------------------------------------------
    async def _fetch_github_trending(self, n: int = 5) -> list[dict]:
        topics = []
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.get(GITHUB_TRENDING, follow_redirects=True)
                r.raise_for_status()

            # Extract repo names and descriptions via regex (no HTML parser needed)
            # Pattern: <h2 class="h3 lh-condensed">...<a ...>owner / repo</a>
            repos = re.findall(
                r'<h2[^>]*class="[^"]*lh-condensed[^"]*"[^>]*>\s*<a[^>]*>([^<]+)</a>',
                r.text,
            )
            descs = re.findall(
                r'<p[^>]*class="[^"]*color-fg-muted[^"]*"[^>]*>\s*([^<]{10,200})',
                r.text,
            )

            for i, repo in enumerate(repos[:n]):
                name = repo.strip().replace("\n", "").replace("  ", " ")
                desc = descs[i].strip() if i < len(descs) else name
                topic = f"{name.split('/')[-1].strip().replace('-', ' ').title()}: {desc[:80]}"
                topics.append({
                    "topic":  _clean_title(topic),
                    "niche":  _guess_niche(name + " " + desc),
                    "source": "github-trending",
                })
            logger.info("[Trends] GitHub Trending: %d topics fetched", len(topics))
        except Exception as exc:
            logger.warning("[Trends] GitHub Trending failed: %s", exc)
        return topics
