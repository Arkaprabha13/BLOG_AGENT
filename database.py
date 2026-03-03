"""
database.py — PostgreSQL Schema Setup & Async DB Helper
Closed-Loop Autonomous Blog Empire

Powered by asyncpg (async PostgreSQL driver).
Nhost PostgreSQL connection via DATABASE_URL in .env

IMPORTANT: Always use 'async with get_db() as conn:' (not 'await get_db()').
"""

import json
import logging
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncIterator, Optional

import asyncpg

from config import get_settings

logger    = logging.getLogger(__name__)
settings  = get_settings()

# Global connection pool (created in init_db, closed in close_db)
_pool: Optional[asyncpg.Pool] = None


# ---------------------------------------------------------------------------
# DDL  — PostgreSQL syntax
# ---------------------------------------------------------------------------
_CREATE_PUBLISHED_BLOGS = """
CREATE TABLE IF NOT EXISTS published_blogs (
    id               SERIAL PRIMARY KEY,
    slug             TEXT    NOT NULL UNIQUE,
    topic            TEXT    NOT NULL,
    niche            TEXT    NOT NULL DEFAULT '',
    title            TEXT    NOT NULL DEFAULT '',
    markdown_content TEXT    NOT NULL DEFAULT '',
    teaser           TEXT    NOT NULL DEFAULT '',
    main_url         TEXT    NOT NULL DEFAULT '',
    devto_url        TEXT    NOT NULL DEFAULT '',
    hashnode_url     TEXT    NOT NULL DEFAULT '',
    tags             TEXT    NOT NULL DEFAULT '[]',
    status           TEXT    NOT NULL DEFAULT 'draft'
                     CHECK (status IN ('draft','published','optimized','failed')),
    publish_date     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

_CREATE_ANALYTICS_LOG = """
CREATE TABLE IF NOT EXISTS analytics_log (
    id             SERIAL PRIMARY KEY,
    blog_id        INTEGER NOT NULL REFERENCES published_blogs(id) ON DELETE CASCADE,
    views          INTEGER NOT NULL DEFAULT 0,
    seo_score      DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    last_optimized TIMESTAMPTZ,
    fix_history    TEXT    NOT NULL DEFAULT '[]',
    recorded_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(blog_id)
);
"""

_CREATE_CONTENT_TREE = """
CREATE TABLE IF NOT EXISTS content_tree (
    id         SERIAL PRIMARY KEY,
    blog_id    INTEGER REFERENCES published_blogs(id) ON DELETE CASCADE,
    node_type  TEXT    NOT NULL,
    node_key   TEXT    NOT NULL,
    content    TEXT    NOT NULL,
    parent_id  INTEGER REFERENCES content_tree(id),
    verified   BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_blogs_slug     ON published_blogs(slug);",
    "CREATE INDEX IF NOT EXISTS idx_blogs_status   ON published_blogs(status);",
    "CREATE INDEX IF NOT EXISTS idx_analytics_blog ON analytics_log(blog_id);",
    "CREATE INDEX IF NOT EXISTS idx_tree_blog      ON content_tree(blog_id);",
]


# ---------------------------------------------------------------------------
# Helper: asyncpg.Record → dict
# ---------------------------------------------------------------------------
def _row(r: asyncpg.Record) -> dict:
    """Convert an asyncpg.Record to a plain dict."""
    return dict(r)


# ---------------------------------------------------------------------------
# Pool management
# ---------------------------------------------------------------------------
async def init_db() -> None:
    """Create the connection pool and bootstrap the schema."""
    global _pool
    from urllib.parse import urlparse, urlunparse, parse_qs, urlencode, unquote
    import ssl as ssl_mod

    raw_url = settings.database_url

    # asyncpg does NOT support ?sslmode= in the DSN — strip it and use ssl= kwarg
    parsed   = urlparse(raw_url)
    qs       = {k: v for k, v in parse_qs(parsed.query).items() if k != "sslmode"}
    clean_qs = urlencode({k: v[0] for k, v in qs.items()})

    # Decode password — if the password was URL-encoded (e.g. @ → %40),
    # urllib parses it correctly; asyncpg needs the raw password string.
    pw   = unquote(parsed.password or "")
    user = unquote(parsed.username or "")
    host = parsed.hostname or ""
    port = parsed.port or 5432
    db   = (parsed.path or "").lstrip("/") or "nhost"

    # SSL: Nhost always requires SSL, even if sslmode is absent from the URL
    need_ssl = (
        "sslmode=require" in raw_url
        or "sslmode=verify-full" in raw_url
        or "nhost.run" in host          # Nhost always requires SSL
    )

    logger.info("Connecting to PostgreSQL at %s:%s/%s  ssl=%s …",
                host, port, db, need_ssl)

    _pool = await asyncpg.create_pool(
        host    = host,
        port    = port,
        user    = user,
        password= pw,
        database= db,
        ssl     = True if need_ssl else None,
        min_size= 2,
        max_size= 10,
        command_timeout=30,
    )
    async with _pool.acquire() as conn:
        await conn.execute(_CREATE_PUBLISHED_BLOGS)
        await conn.execute(_CREATE_ANALYTICS_LOG)
        await conn.execute(_CREATE_CONTENT_TREE)
        for idx in _INDEXES:
            await conn.execute(idx)
    logger.info("PostgreSQL database initialised (Nhost)")


async def close_db() -> None:
    """Close the connection pool gracefully."""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("PostgreSQL connection pool closed")


# ---------------------------------------------------------------------------
# DB Context Manager  — THE ONLY CORRECT WAY TO ACCESS THE DB
# ---------------------------------------------------------------------------
@asynccontextmanager
async def get_db() -> AsyncIterator[asyncpg.Connection]:
    """
    Acquire a connection from the pool, yield it, then release.

    Usage:
        async with get_db() as conn:
            rows = await conn.fetch("SELECT ...")

    Never hold a connection across await sleeps or long operations.
    """
    if _pool is None:
        raise RuntimeError("Database pool not initialised — call init_db() first")
    async with _pool.acquire() as conn:
        yield conn


# ---------------------------------------------------------------------------
# CRUD helpers
# ---------------------------------------------------------------------------

async def fetch_top_blogs(limit: int = 5) -> list[dict]:
    sql = """
        SELECT b.id, b.slug, b.title, b.topic, b.niche,
               b.main_url, b.devto_url, b.hashnode_url, b.tags,
               b.status,
               b.publish_date::TEXT AS publish_date,
               COALESCE(a.views, 0)       AS views,
               COALESCE(a.seo_score, 0.0) AS seo_score,
               a.last_optimized::TEXT     AS last_optimized
          FROM published_blogs b
          LEFT JOIN analytics_log a ON a.blog_id = b.id
         WHERE b.status IN ('published', 'optimized')
         ORDER BY views DESC
         LIMIT $1
    """
    async with get_db() as conn:
        rows = await conn.fetch(sql, limit)
    result = []
    for r in rows:
        d = _row(r)
        try:
            d["tags"] = json.loads(d.get("tags") or "[]")
        except (json.JSONDecodeError, TypeError):
            d["tags"] = []
        result.append(d)
    return result


async def get_blog_by_slug(slug: str) -> dict | None:
    async with get_db() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM published_blogs WHERE slug = $1", slug
        )
    if row is None:
        return None
    d = _row(row)
    try:
        d["tags"] = json.loads(d.get("tags") or "[]")
    except (json.JSONDecodeError, TypeError):
        d["tags"] = []
    # Normalise timestamps to string for templates
    for k in ("publish_date", "updated_at"):
        if d.get(k) and not isinstance(d[k], str):
            d[k] = d[k].isoformat()
    return d


async def save_blog(
    *,
    slug: str,
    topic: str,
    niche: str,
    title: str,
    markdown_content: str,
    teaser: str = "",
    main_url: str = "",
    devto_url: str = "",
    hashnode_url: str = "",
    tags: list[str] | None = None,
    status: str = "published",
) -> int:
    tags_json = json.dumps(tags or [])
    async with get_db() as conn:
        blog_id = await conn.fetchval(
            """
            INSERT INTO published_blogs
                (slug, topic, niche, title, markdown_content, teaser,
                 main_url, devto_url, hashnode_url, tags, status)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            RETURNING id
            """,
            slug, topic, niche, title, markdown_content, teaser,
            main_url, devto_url, hashnode_url, tags_json, status,
        )
    return int(blog_id)


async def update_blog_urls(
    blog_id: int,
    *,
    devto_url: str = "",
    hashnode_url: str = "",
    status: str = "published",
) -> None:
    async with get_db() as conn:
        await conn.execute(
            "UPDATE published_blogs SET devto_url=$1, hashnode_url=$2, status=$3, updated_at=NOW() WHERE id=$4",
            devto_url, hashnode_url, status, blog_id,
        )


async def find_similar_blogs(topic: str, threshold: int = 2) -> list[dict]:
    """
    Return existing blogs that are likely duplicates of `topic`.
    Matching: if `threshold`+ significant words (>3 chars) from the new topic
    appear in an existing title or topic field → duplicate.
    """
    words = [w.lower() for w in re.sub(r"[^a-z0-9 ]", " ", topic.lower()).split()
             if len(w) > 3]
    if not words:
        return []

    async with get_db() as conn:
        rows = await conn.fetch(
            """
            SELECT id, slug, title, topic, main_url,
                   publish_date::TEXT AS publish_date
              FROM published_blogs
             WHERE status IN ('published', 'optimized', 'draft')
            """
        )

    matches: list[dict] = []
    for r in rows:
        haystack = (r["title"] + " " + r["topic"]).lower()
        hits = sum(1 for w in words if w in haystack)
        if hits >= threshold:
            matches.append(_row(r))
    return matches


async def upsert_analytics(
    blog_id: int,
    *,
    views: int = 0,
    seo_score: float = 0.0,
    fix_note: str | None = None,
) -> None:
    """
    INSERT or UPDATE analytics for a blog post.
    Uses PostgreSQL ON CONFLICT for atomic upsert.
    """
    now = datetime.now(timezone.utc)
    fix_history_json = "[]"

    if fix_note:
        # Fetch existing history first
        async with get_db() as conn:
            row = await conn.fetchrow(
                "SELECT fix_history FROM analytics_log WHERE blog_id = $1", blog_id
            )
        existing = json.loads(row["fix_history"] if row else "[]")
        existing.append({"ts": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "note": fix_note})
        fix_history_json = json.dumps(existing)

    async with get_db() as conn:
        await conn.execute(
            """
            INSERT INTO analytics_log (blog_id, views, seo_score, last_optimized, fix_history)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (blog_id) DO UPDATE SET
                views          = EXCLUDED.views,
                seo_score      = EXCLUDED.seo_score,
                last_optimized = EXCLUDED.last_optimized,
                fix_history    = EXCLUDED.fix_history
            """,
            blog_id, views, seo_score,
            now if fix_note else None,
            fix_history_json,
        )



# ---------------------------------------------------------------------------
# Additional helpers for Telegram bot commands
# ---------------------------------------------------------------------------

async def list_all_blogs(limit: int = 20, offset: int = 0) -> list[dict]:
    """List all blogs, newest first, with platform URLs and stats."""
    async with get_db() as conn:
        rows = await conn.fetch(
            """
            SELECT b.id, b.slug, b.title, b.niche, b.status,
                   b.main_url, b.devto_url, b.hashnode_url,
                   b.publish_date::TEXT AS publish_date,
                   COALESCE(a.views, 0)       AS views,
                   COALESCE(a.seo_score, 0.0) AS seo_score
              FROM published_blogs b
              LEFT JOIN analytics_log a ON a.blog_id = b.id
             ORDER BY b.publish_date DESC
             LIMIT $1 OFFSET $2
            """,
            limit, offset,
        )
    return [_row(r) for r in rows]


async def delete_blog(slug: str) -> bool:
    """Delete a blog post by slug. Returns True if a row was deleted."""
    async with get_db() as conn:
        row = await conn.fetchrow(
            "SELECT id FROM published_blogs WHERE slug = $1", slug
        )
        if row is None:
            return False
        blog_id = row["id"]
        await conn.execute("DELETE FROM analytics_log WHERE blog_id = $1", blog_id)
        await conn.execute("DELETE FROM published_blogs WHERE id = $1", blog_id)
    return True


async def get_blog_count() -> int:
    """Return total number of published blogs."""
    async with get_db() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM published_blogs WHERE status IN ('published','optimized')"
        ) or 0


# ---------------------------------------------------------------------------
# CLI — bootstrap schema
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import asyncio
    logging.basicConfig(level=logging.INFO)

    async def _main():
        await init_db()
        print("✅  PostgreSQL schema ready (Nhost)")
        await close_db()

    asyncio.run(_main())
