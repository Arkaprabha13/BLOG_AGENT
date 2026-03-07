"""
clients/hashnode_client.py — Hashnode GraphQL API wrapper

NOTES:
- Tags on Hashnode must match slugs of tags that exist in your publication.
  Sending arbitrary tag objects causes 400 errors.
  Solution: omit tags from the mutation; add them manually in Hashnode's UI.
- The `originalArticleURL` field sets the canonical URL for SEO.
- Requires HASHNODE_API_TOKEN and HASHNODE_PUBLICATION_ID in .env
"""

import logging
import httpx
from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

HASHNODE_API_URL = "https://gql.hashnode.com/"


class HashnodeClient:
    def __init__(self):
        self.token          = settings.hashnode_api_token
        self.publication_id = settings.hashnode_publication_id
        self.headers        = {
            "Authorization": self.token,
            "Content-Type":  "application/json",
        }

    async def _gql(self, query: str, variables: dict) -> dict:
        """Execute a GraphQL request and return the response dict."""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    HASHNODE_API_URL,
                    json={"query": query, "variables": variables},
                    headers=self.headers,
                )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            logger.error("Hashnode HTTP error %d: %s", e.response.status_code, e.response.text[:400])
            raise
        except Exception as exc:
            logger.error("Hashnode request error: %s", exc)
            raise

    async def publish(
        self,
        title: str,
        content: str,
        tags: list[str],   # kept in signature for compatibility; NOT sent to Hashnode
        canonical_url: str,
        teaser: str = "",
    ) -> dict:
        """
        Creates a new Hashnode post.
        Tags are intentionally omitted — Hashnode requires pre-existing publication
        tag slugs which vary per account. Add tags manually in Hashnode's dashboard.
        Sets originalArticleURL so Hashnode passes SEO credit back to our site.
        """
        if not self.token or not self.publication_id:
            logger.warning("Hashnode credentials not set (HASHNODE_API_TOKEN / HASHNODE_PUBLICATION_ID) — skipping")
            return {"url": ""}

        # Hashnode rejects localhost canonical URLs
        if "localhost" in canonical_url or "127.0.0.1" in canonical_url:
            logger.warning("Hashnode: skipping — canonical_url is localhost. "
                           "Set BASE_URL to a public domain to enable syndication.")
            return {"url": ""}

        teaser_header = f"> *Originally published at [{canonical_url}]({canonical_url})*\n\n"
        body = teaser_header + content

        # NOTE: 'tags' field is omitted to avoid 400 from non-existent publication tags
        mutation = """
        mutation PublishPost($input: PublishPostInput!) {
          publishPost(input: $input) {
            post {
              id
              url
              slug
              title
            }
          }
        }
        """
        variables = {
            "input": {
                "title":              title,
                "publicationId":      self.publication_id,
                "contentMarkdown":    body,
                "originalArticleURL": canonical_url,
                "metaTags": {
                    "title":       title,
                    "description": teaser[:160] if teaser else title,
                },
            }
        }

        try:
            data   = await self._gql(mutation, variables)
            errors = data.get("errors")
            if errors:
                logger.error("Hashnode GraphQL errors: %s", errors)
                return {"url": ""}
            post = data.get("data", {}).get("publishPost", {}).get("post", {})
            url  = post.get("url", "")
            logger.info("Hashnode published: %s", url)
            return post
        except Exception:
            return {"url": ""}

    async def update(
        self,
        post_id: str,
        title: str,
        content: str,
        tags: list[str],
        canonical_url: str,
    ) -> dict:
        """Update an existing Hashnode post (used by optimizer)."""
        if not self.token:
            return {}

        teaser_header = f"> *Originally published at [{canonical_url}]({canonical_url})*\n\n"
        mutation = """
        mutation UpdatePost($input: UpdatePostInput!) {
          updatePost(input: $input) {
            post { id url }
          }
        }
        """
        variables = {
            "input": {
                "id":              post_id,
                "title":           title,
                "contentMarkdown": teaser_header + content,
                "originalArticleURL": canonical_url,
            }
        }
        try:
            data = await self._gql(mutation, variables)
            return data.get("data", {}).get("updatePost", {}).get("post", {})
        except Exception as exc:
            logger.error("Hashnode update error: %s", exc)
            return {}

    async def get_my_posts(self, first: int = 20) -> list[dict]:
        """Fetch posts from the publication with engagement stats."""
        if not self.token or not self.publication_id:
            return []
        query = """
        query GetPosts($pubId: ObjectId!, $first: Int!) {
          publication(id: $pubId) {
            posts(first: $first) {
              edges {
                node {
                  id
                  title
                  url
                  views
                  reactionCount
                  responseCount
                  publishedAt
                }
              }
            }
          }
        }
        """
        try:
            data = await self._gql(query, {"pubId": self.publication_id, "first": first})
            edges = (
                data.get("data", {})
                    .get("publication", {})
                    .get("posts", {})
                    .get("edges", [])
            )
            return [
                {
                    "id":        e["node"].get("id", ""),
                    "title":     e["node"].get("title", ""),
                    "url":       e["node"].get("url", ""),
                    "views":     e["node"].get("views", 0) or 0,
                    "reactions": e["node"].get("reactionCount", 0) or 0,
                    "comments":  e["node"].get("responseCount", 0) or 0,
                    "published": (e["node"].get("publishedAt") or "")[:10],
                }
                for e in edges
            ]
        except Exception as exc:
            logger.error("Hashnode get_my_posts error: %s", exc)
            return []
