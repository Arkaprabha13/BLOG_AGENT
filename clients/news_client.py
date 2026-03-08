"""
clients/news_client.py — Multi-Source News Fetcher

Fetches news from three paid (but limited-free) sources:
  1. NewsData.io     — great for AI/tech/geopolitics
  2. NewsAPI.org     — broad category coverage
  3. The News API   — additional tech + business layer

Target niches:
  • Artificial Intelligence / ML / NLP
  • Data Centres & Cloud Infrastructure
  • Big Tech Companies
  • Geopolitics & International Affairs
  • Education & EdTech

FIX (v2): query configs are copied per-request — the module-level dicts
are never mutated, so repeated calls always work correctly.
"""

import asyncio
import logging
import time
from typing import Optional

import httpx

from config import get_settings

logger   = logging.getLogger(__name__)
settings = get_settings()

# ---------------------------------------------------------------------------
# Target query configuration per source
# Each dict has an "_niche" key that is popped from a COPY at call time —
# the module-level list is NEVER mutated.
# ---------------------------------------------------------------------------

# NewsData.io — https://newsdata.io/documentation
NEWSDATA_BASE = "https://newsdata.io/api/1/latest"
NEWSDATA_QUERIES = [
    {"q": "artificial intelligence OR machine learning OR NLP OR large language model", "category": "technology", "_niche": "artificial-intelligence"},
    {"q": "data centre OR data center OR cloud computing OR GPU cluster",               "category": "technology", "_niche": "data-centres"},
    {"q": "geopolitics OR international relations OR trade war OR sanctions OR war",     "category": "world",      "_niche": "geopolitics"},
    {"q": "education technology OR edtech OR online learning OR university",             "category": "education",  "_niche": "education"},
    {"q": "OpenAI OR Google DeepMind OR Meta AI OR Nvidia OR Anthropic OR Microsoft AI","category": "technology", "_niche": "tech-companies"},
]

# NewsAPI.org — https://newsapi.org/docs
NEWSAPI_BASE = "https://newsapi.org/v2/everything"
NEWSAPI_QUERIES = [
    {"q": "\"artificial intelligence\" OR \"machine learning\" OR LLM OR \"large language model\"", "sortBy": "publishedAt", "language": "en", "_niche": "artificial-intelligence"},
    {"q": "geopolitics OR \"global economy\" OR \"international trade\" OR war OR conflict",          "sortBy": "publishedAt", "language": "en", "_niche": "geopolitics"},
    {"q": "education OR \"higher education\" OR EdTech OR university OR students",                   "sortBy": "relevancy",   "language": "en", "_niche": "education"},
    {"q": "Nvidia OR \"data center\" OR \"AI chip\" OR semiconductor OR GPU",                        "sortBy": "publishedAt", "language": "en", "_niche": "data-centres"},
]

# The News API — https://www.thenewsapi.com/documentation
THENEWSAPI_BASE = "https://api.thenewsapi.com/v1/news/all"
THENEWSAPI_QUERIES = [
    {"search": "artificial intelligence machine learning",  "categories": "tech",             "language": "en", "_niche": "artificial-intelligence"},
    {"search": "data centers cloud infrastructure GPU",     "categories": "tech,business",    "language": "en", "_niche": "data-centres"},
    {"search": "OpenAI Google Microsoft Meta Nvidia",       "categories": "tech,business",    "language": "en", "_niche": "tech-companies"},
    {"search": "geopolitics international relations world", "categories": "general,politics", "language": "en", "_niche": "geopolitics"},
    {"search": "education edtech learning university",      "categories": "general",          "language": "en", "_niche": "education"},
]

# Also expose the NewsData.io query for ad-hoc topic lookups
NEWSDATA_TOPIC_BASE = "https://newsdata.io/api/1/latest"

# ---------------------------------------------------------------------------
# In-memory cache — keyed by source name
# ---------------------------------------------------------------------------
_CACHE_TTL = 1800   # 30 minutes

_cache: dict[str, tuple[float, list[dict]]] = {}


def _cache_get(key: str) -> Optional[list[dict]]:
    if key in _cache:
        ts, data = _cache[key]
        if time.time() - ts < _CACHE_TTL:
            return data
    return None


def _cache_set(key: str, data: list[dict]) -> None:
    _cache[key] = (time.time(), data)


def _clear_cache() -> None:
    """Force-clear cache (e.g. after a manual /recommend)."""
    _cache.clear()


# ---------------------------------------------------------------------------
# NewsClient
# ---------------------------------------------------------------------------

class NewsClient:
    """
    Async multi-source news client.

    Usage:
        client = NewsClient()
        articles = await client.fetch_all(max_per_source=15)
        articles = await client.fetch_topic("Iran Israel war", niche="geopolitics")
    """

    def __init__(self, timeout: int = 15):
        self.timeout = timeout

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def fetch_all(self, max_per_source: int = 15) -> list[dict]:
        """
        Fetch news from all 3 sources in parallel.
        Returns a deduplicated, combined list of article dicts.
        """
        cache_key = f"all_{max_per_source}"
        cached = _cache_get(cache_key)
        if cached is not None:
            logger.info("[NewsClient] Returning %d articles from cache", len(cached))
            return cached

        results = await asyncio.gather(
            self._fetch_newsdata(max_per_source),
            self._fetch_newsapi(max_per_source),
            self._fetch_thenewsapi(max_per_source),
            return_exceptions=True,
        )

        combined: list[dict] = []
        source_names = ["NewsData.io", "NewsAPI.org", "TheNewsAPI"]
        for name, r in zip(source_names, results):
            if isinstance(r, Exception):
                logger.warning("[NewsClient] %s failed: %s", name, r)
            elif isinstance(r, list):
                combined.extend(r)
                logger.info("[NewsClient] %s returned %d articles", name, len(r))

        # Deduplicate by normalised title (first 6 words)
        seen: set[str] = set()
        unique: list[dict] = []
        for art in combined:
            key = " ".join(art["title"].lower().split()[:6])
            if key and key not in seen:
                seen.add(key)
                unique.append(art)

        logger.info("[NewsClient] Total unique articles: %d", len(unique))
        _cache_set(cache_key, unique)
        return unique

    async def fetch_topic(self, topic: str, niche: str = "general") -> list[dict]:
        """
        Ad-hoc search for a specific topic/keyword using NewsData.io.
        Used when the user explicitly asks for news about something.
        Does NOT use cache — always fetches fresh results.
        """
        key = settings.newsdata_api_key
        if not key:
            logger.debug("[NewsClient] NewsData.io key not set — skipping topic fetch")
            return []

        params = {
            "apikey":   key,
            "q":        topic,
            "language": "en",
        }

        articles: list[dict] = []
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                r = await client.get(NEWSDATA_TOPIC_BASE, params=params)
                r.raise_for_status()
                data = r.json()
                for item in (data.get("results") or [])[:10]:
                    title = (item.get("title") or "").strip()
                    desc  = (item.get("description") or item.get("content") or "").strip()
                    url   = item.get("link") or item.get("url") or ""
                    pub   = item.get("pubDate") or ""
                    src   = item.get("source_name") or item.get("source_id") or "newsdata.io"
                    if title and len(title.split()) > 3:
                        articles.append({
                            "title":        title,
                            "description":  desc[:400],
                            "url":          url,
                            "source":       src,
                            "published_at": pub,
                            "niche":        niche,
                        })
                logger.info("[NewsClient] Topic '%s': %d articles", topic, len(articles))
            except Exception as e:
                logger.warning("[NewsClient] Topic fetch failed for '%s': %s", topic, e)

        return articles

    # ------------------------------------------------------------------
    # NewsData.io
    # ------------------------------------------------------------------

    async def _fetch_newsdata(self, max_total: int = 15) -> list[dict]:
        key = settings.newsdata_api_key
        if not key:
            logger.debug("[NewsClient] NewsData.io key not set — skipping")
            return []

        cache_key = "newsdata"
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

        articles: list[dict] = []
        per_query = max(3, max_total // len(NEWSDATA_QUERIES))

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for query_cfg in NEWSDATA_QUERIES:
                if len(articles) >= max_total:
                    break

                # ✅ FIX: Copy the dict — never mutate the module-level list
                cfg   = dict(query_cfg)
                niche = cfg.pop("_niche", "technology")
                params = {
                    "apikey":   key,
                    "language": "en",
                    **cfg,
                }
                try:
                    r = await client.get(NEWSDATA_BASE, params=params)
                    r.raise_for_status()
                    data = r.json()
                    for item in (data.get("results") or [])[:per_query]:
                        title = (item.get("title") or "").strip()
                        desc  = (item.get("description") or item.get("content") or "").strip()
                        url   = item.get("link") or item.get("url") or ""
                        pub   = item.get("pubDate") or ""
                        src   = item.get("source_name") or item.get("source_id") or "newsdata.io"
                        if title and len(title.split()) > 4:
                            articles.append({
                                "title":        title,
                                "description":  desc[:300],
                                "url":          url,
                                "source":       src,
                                "published_at": pub,
                                "niche":        niche,
                            })
                except Exception as e:
                    logger.warning("[NewsClient] NewsData.io query '%s' failed: %s",
                                   cfg.get("q", "?"), e)

        _cache_set(cache_key, articles[:max_total])
        logger.info("[NewsClient] NewsData.io: %d articles", len(articles[:max_total]))
        return articles[:max_total]

    # ------------------------------------------------------------------
    # NewsAPI.org
    # ------------------------------------------------------------------

    async def _fetch_newsapi(self, max_total: int = 15) -> list[dict]:
        key = settings.newsapi_org_key
        if not key:
            logger.debug("[NewsClient] NewsAPI.org key not set — skipping")
            return []

        cache_key = "newsapi"
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

        articles: list[dict] = []
        per_query = max(3, max_total // len(NEWSAPI_QUERIES))

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for query_cfg in NEWSAPI_QUERIES:
                if len(articles) >= max_total:
                    break

                # ✅ FIX: Copy the dict — never mutate the module-level list
                cfg   = dict(query_cfg)
                niche = cfg.pop("_niche", "technology")
                params = {
                    "apiKey":   key,
                    "pageSize": per_query,
                    **cfg,
                }
                try:
                    r = await client.get(NEWSAPI_BASE, params=params)
                    r.raise_for_status()
                    data = r.json()
                    for item in (data.get("articles") or [])[:per_query]:
                        title = (item.get("title") or "").strip()
                        if not title or title == "[Removed]" or len(title.split()) < 5:
                            continue
                        desc     = (item.get("description") or "").strip()
                        url      = item.get("url") or ""
                        pub      = item.get("publishedAt") or ""
                        src_name = (item.get("source") or {}).get("name") or "newsapi.org"
                        articles.append({
                            "title":        title,
                            "description":  desc[:300],
                            "url":          url,
                            "source":       src_name,
                            "published_at": pub,
                            "niche":        niche,
                        })
                except Exception as e:
                    logger.warning("[NewsClient] NewsAPI.org query failed: %s", e)

        _cache_set(cache_key, articles[:max_total])
        logger.info("[NewsClient] NewsAPI.org: %d articles", len(articles[:max_total]))
        return articles[:max_total]

    # ------------------------------------------------------------------
    # The News API
    # ------------------------------------------------------------------

    async def _fetch_thenewsapi(self, max_total: int = 15) -> list[dict]:
        key = settings.the_news_api_key
        if not key:
            logger.debug("[NewsClient] The News API key not set — skipping")
            return []

        cache_key = "thenewsapi"
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

        articles: list[dict] = []
        per_query = max(3, max_total // len(THENEWSAPI_QUERIES))

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for query_cfg in THENEWSAPI_QUERIES:
                if len(articles) >= max_total:
                    break

                # ✅ FIX: Copy the dict — never mutate the module-level list
                cfg   = dict(query_cfg)
                niche = cfg.pop("_niche", "technology")
                params = {
                    "api_token": key,
                    "limit":     per_query,
                    **cfg,
                }
                try:
                    r = await client.get(THENEWSAPI_BASE, params=params)
                    r.raise_for_status()
                    data = r.json()
                    for item in (data.get("data") or [])[:per_query]:
                        title = (item.get("title") or "").strip()
                        if not title or len(title.split()) < 5:
                            continue
                        desc  = (item.get("description") or item.get("snippet") or "").strip()
                        url   = item.get("url") or ""
                        pub   = item.get("published_at") or ""
                        src   = (item.get("source") or "thenewsapi.com")
                        articles.append({
                            "title":        title,
                            "description":  desc[:300],
                            "url":          url,
                            "source":       src,
                            "published_at": pub,
                            "niche":        niche,
                        })
                except Exception as e:
                    logger.warning("[NewsClient] TheNewsAPI query failed: %s", e)

        _cache_set(cache_key, articles[:max_total])
        logger.info("[NewsClient] TheNewsAPI: %d articles", len(articles[:max_total]))
        return articles[:max_total]
