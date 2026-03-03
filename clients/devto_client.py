"""
clients/devto_client.py — Dev.to API wrapper
Publishes articles with rel="canonical" pointing to our FastAPI site.

Tag rules (Dev.to):
  - Max 4 tags per article
  - Each tag: lowercase letters and digits ONLY (no underscores, hyphens, spaces)
  - Max 20 chars per tag
"""

import logging
import re
import httpx
from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

DEVTO_API_BASE = "https://dev.to/api"


def _clean_tag(raw: str) -> str:
    """Return a Dev.to-compatible tag: lowercase alphanumeric only, max 20 chars."""
    return re.sub(r"[^a-z0-9]", "", raw.lower())[:20]


class DevtoClient:
    def __init__(self):
        self.api_key = settings.devto_api_key
        self.headers = {
            "api-key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/vnd.forem.api-v1+json",
        }

    async def publish(
        self,
        title: str,
        content: str,
        tags: list[str],
        canonical_url: str,
        teaser: str = "",
    ) -> dict:
        """
        Creates a new article on Dev.to.
        The canonical_url points back to our FastAPI site for SEO credit.
        Returns the API response dict (includes 'url' key on success).

        NOTE: Dev.to only accepts public canonical URLs. Publishing from
        localhost will be skipped with a warning. Set BASE_URL in .env to
        your deployed domain to enable syndication.
        """
        if not self.api_key:
            logger.warning("Dev.to API key not set — skipping publish")
            return {"url": ""}

        # Dev.to rejects localhost / 127.0.0.1 canonical URLs
        if "localhost" in canonical_url or "127.0.0.1" in canonical_url:
            logger.warning(
                "Dev.to: skipping — canonical_url is localhost (%s). "
                "Set BASE_URL to a public domain in .env to enable syndication.",
                canonical_url,
            )
            return {"url": ""}

        # Clean tags: alphanumeric only, no underscores/hyphens, max 4, non-empty
        clean_tags = [t for t in (_clean_tag(tag) for tag in tags[:4]) if t]
        if not clean_tags:
            clean_tags = ["blog"]

        teaser_section = f"> *Originally published at [{canonical_url}]({canonical_url})*\n\n"
        if teaser:
            teaser_section += f"{teaser}\n\n"

        payload = {
            "article": {
                "title":         title,
                "body_markdown": teaser_section + content,
                "published":     True,
                "canonical_url": canonical_url,
                "tags":          clean_tags,
                "description":   teaser[:160] if teaser else title,
            }
        }

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{DEVTO_API_BASE}/articles",
                    json=payload,
                    headers=self.headers,
                )
            if resp.status_code in (200, 201):
                data = resp.json()
                url = data.get("url", "")
                logger.info("Dev.to published: %s", url)
                return data
            else:
                logger.error("Dev.to publish failed %d: %s", resp.status_code, resp.text[:300])
                return {"url": ""}
        except Exception as exc:
            logger.error("Dev.to publish exception: %s", exc)
            return {"url": ""}

    async def update(
        self,
        article_id: int,
        title: str,
        content: str,
        tags: list[str],
        canonical_url: str,
    ) -> dict:
        """Update an existing Dev.to article (for the optimizer)."""
        if not self.api_key:
            return {}
        clean_tags = [t for t in (_clean_tag(tag) for tag in tags[:4]) if t] or ["blog"]
        payload = {
            "article": {
                "title":         title,
                "body_markdown": f"> *Originally published at [{canonical_url}]({canonical_url})*\n\n" + content,
                "canonical_url": canonical_url,
                "tags":          clean_tags,
            }
        }
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.put(
                    f"{DEVTO_API_BASE}/articles/{article_id}",
                    json=payload,
                    headers=self.headers,
                )
            if resp.status_code == 200:
                return resp.json()
            logger.error("Dev.to update failed %d: %s", resp.status_code, resp.text[:300])
            return {}
        except Exception as exc:
            logger.error("Dev.to update exception: %s", exc)
            return {}
