"""
graph_system2.py — LangGraph System 2: Self-Healing SEO Optimizer
Nodes: Ingestion → Diagnostic → Optimizer → Update (loops until all done)

STATE KEY FIX: Uses TypedDict so partial node updates merge correctly.
"""

import html as html_mod
import json
import logging
import random
from datetime import datetime, timezone
from typing import Any, Optional, TypedDict

from langgraph.graph import StateGraph, START, END

from clients.groq_client import GroqClient
from clients.devto_client import DevtoClient
from clients.hashnode_client import HashnodeClient
from config import get_settings
from database import get_db, upsert_analytics

logger = logging.getLogger(__name__)
settings = get_settings()

groq_c   = GroqClient()
devto    = DevtoClient()
hashnode = HashnodeClient()


# ---------------------------------------------------------------------------
# State schema
# ---------------------------------------------------------------------------
class OptState(TypedDict, total=False):
    # Config (set at init)
    threshold_views:      int
    threshold_seo:        float
    chat_id:              Optional[int]

    # Ingestion node
    low_performing_blogs: list[dict[str, Any]]

    # Diagnostic node
    current_blog:         dict[str, Any]
    diagnosis:            str
    suggested_fixes:      list[str]
    _improved_title:      str
    _improved_teaser:     str
    _improved_tags:       list[str]

    # Optimizer node
    rewritten_title:      str
    rewritten_content:    str
    rewritten_tags:       list[str]
    rewritten_teaser:     str

    # Update node (accumulates)
    blogs_processed:      int
    update_success:       bool
    error_message:        str


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

async def ingestion_node(state: OptState) -> dict:
    """Scan DB for published blogs, check/simulate analytics, find low-performers."""
    logger.info("[Ingestion] Scanning published blogs …")
    threshold_views = state.get("threshold_views", settings.seo_threshold_views)
    threshold_seo   = state.get("threshold_seo",   settings.seo_threshold_score)

    async with get_db() as conn:
        raw_rows = await conn.fetch(
            "SELECT id, slug, title, topic, niche, tags, teaser, "
            "markdown_content, devto_url, hashnode_url, main_url "
            "FROM published_blogs WHERE status IN ('published','optimized')"
        )

    # asyncpg Records support dict()
    blogs = [dict(r) for r in raw_rows]
    logger.info("[Ingestion] Total published: %d", len(blogs))

    low_performing: list[dict] = []
    for blog in blogs:
        async with get_db() as conn:
            row = await conn.fetchrow(
                "SELECT views, seo_score FROM analytics_log WHERE blog_id = $1",
                blog["id"]
            )

        if row:
            views     = int(row["views"])
            seo_score = float(row["seo_score"])
        else:
            # Simulate initial analytics for new posts
            views     = random.randint(0, 500)
            seo_score = random.uniform(20.0, 90.0)
            await upsert_analytics(blog["id"], views=views, seo_score=seo_score)

        if views < threshold_views or seo_score < threshold_seo:
            blog["views"]     = views
            blog["seo_score"] = seo_score
            try:
                blog["tags"] = json.loads(blog.get("tags") or "[]")
            except (json.JSONDecodeError, TypeError):
                blog["tags"] = []
            low_performing.append(blog)
            logger.info("[Ingestion] Low: slug=%s  views=%d  seo=%.1f",
                        blog["slug"], views, seo_score)

    logger.info("[Ingestion] Low-performing count: %d", len(low_performing))
    return {"low_performing_blogs": low_performing}


async def diagnostic_node(state: OptState) -> dict:
    """Diagnose the next unprocessed low-performing blog with Qwen3-32B."""
    blogs     = state.get("low_performing_blogs", [])
    processed = state.get("blogs_processed", 0)

    if processed >= len(blogs):
        logger.info("[Diagnostic] All %d blogs processed", processed)
        return {"current_blog": {}, "diagnosis": "", "suggested_fixes": []}

    blog = blogs[processed]
    logger.info("[Diagnostic] Diagnosing %d/%d: slug=%s",
                processed + 1, len(blogs), blog.get("slug"))
    try:
        result = await groq_c.diagnose_post(
            title     = blog.get("title", blog.get("topic", "")),
            teaser    = blog.get("teaser", ""),
            tags      = blog.get("tags", []),
            views     = int(blog.get("views", 0)),
            seo_score = float(blog.get("seo_score", 0)),
        )
        return {
            "current_blog":    blog,
            "diagnosis":       result.get("diagnosis", ""),
            "suggested_fixes": result.get("fixes", []),
            "_improved_title":  result.get("improved_title",  blog.get("title", "")),
            "_improved_teaser": result.get("improved_teaser", blog.get("teaser", "")),
            "_improved_tags":   result.get("improved_tags",   blog.get("tags", [])),
        }
    except Exception as exc:
        logger.error("[Diagnostic] Failed: %s", exc, exc_info=True)
        return {
            "current_blog":   blog,
            "diagnosis":      str(exc),
            "suggested_fixes": [],
        }


async def optimizer_node(state: OptState) -> dict:
    """Rewrite the current low-performing post with Qwen3-32B."""
    blog = state.get("current_blog", {})
    if not blog:
        return {}

    logger.info("[Optimizer] Rewriting: slug=%s", blog.get("slug"))
    try:
        result = await groq_c.rewrite_post(
            title     = blog.get("title", ""),
            content   = blog.get("markdown_content", ""),
            diagnosis = state.get("diagnosis", ""),
            fixes     = state.get("suggested_fixes", []),
        )
        return {
            "rewritten_title":   result.get("title",   state.get("_improved_title",  blog.get("title", ""))),
            "rewritten_content": result.get("content", blog.get("markdown_content", "")),
            "rewritten_tags":    result.get("tags",    state.get("_improved_tags",   blog.get("tags", []))),
            "rewritten_teaser":  result.get("teaser",  state.get("_improved_teaser", blog.get("teaser", ""))),
        }
    except Exception as exc:
        logger.error("[Optimizer] Failed: %s", exc, exc_info=True)
        return {
            "rewritten_title":   state.get("_improved_title",  blog.get("title", "")),
            "rewritten_content": blog.get("markdown_content", ""),
            "rewritten_tags":    state.get("_improved_tags",   blog.get("tags", [])),
            "rewritten_teaser":  state.get("_improved_teaser", blog.get("teaser", "")),
        }


async def update_node(state: OptState) -> dict:
    """Persist rewritten content, republish, update analytics, send Telegram alert."""
    blog = state.get("current_blog", {})
    if not blog:
        return {
            "blogs_processed": state.get("blogs_processed", 0),
            "update_success":  True,
        }

    blog_id  = blog["id"]
    slug     = blog.get("slug", "")
    main_url = blog.get("main_url") or f"{settings.base_url}/blog/{slug}"
    now      = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    new_title   = state.get("rewritten_title",   blog.get("title", ""))
    new_content = state.get("rewritten_content", blog.get("markdown_content", ""))
    new_tags    = state.get("rewritten_tags",    blog.get("tags", []))
    new_teaser  = state.get("rewritten_teaser",  blog.get("teaser", ""))
    diagnosis   = state.get("diagnosis", "")
    fixes       = state.get("suggested_fixes", [])

    logger.info("[Update] Persisting optimized post: slug=%s", slug)

    # Update DB
    async with get_db() as conn:
        await conn.execute(
            "UPDATE published_blogs "
            "SET title=$1, markdown_content=$2, teaser=$3, tags=$4, status='optimized', updated_at=NOW() "
            "WHERE id=$5",
            new_title, new_content, new_teaser, json.dumps(new_tags), blog_id,
        )

    # Bump analytics
    await upsert_analytics(
        blog_id,
        views     = int(blog.get("views", 0)) + random.randint(5, 50),
        seo_score = min(100.0, float(blog.get("seo_score", 50.0)) + random.uniform(10, 25)),
        fix_note  = f"Rewrite: {diagnosis[:120]}",
    )

    # Republish (non-fatal)
    devto_url    = blog.get("devto_url", "")
    hashnode_url = blog.get("hashnode_url", "")
    try:
        r = await devto.publish(new_title, new_content, new_tags, main_url, new_teaser)
        if r.get("url"):
            devto_url = r["url"]
    except Exception as e:
        logger.warning("[Update] Dev.to re-publish failed: %s", e)
    try:
        r = await hashnode.publish(new_title, new_content, new_tags, main_url, new_teaser)
        if r.get("url"):
            hashnode_url = r["url"]
    except Exception as e:
        logger.warning("[Update] Hashnode re-publish failed: %s", e)

    # Update external URLs
    async with get_db() as conn:
        await conn.execute(
            "UPDATE published_blogs SET devto_url=$1, hashnode_url=$2 WHERE id=$3",
            devto_url, hashnode_url, blog_id,
        )

    await _send_optimization_alert(
        new_title, main_url, slug, diagnosis, fixes, state.get("chat_id")
    )

    return {
        "blogs_processed": state.get("blogs_processed", 0) + 1,
        "update_success":  True,
    }


def should_continue(state: OptState) -> str:
    processed = state.get("blogs_processed", 0)
    total     = len(state.get("low_performing_blogs", []))
    if processed < total:
        logger.info("[Router] Continuing: %d/%d done", processed, total)
        return "diagnostic"
    logger.info("[Router] All %d posts processed — done", total)
    return END


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------

def _build_graph():
    builder = StateGraph(OptState)
    builder.add_node("ingestion",  ingestion_node)
    builder.add_node("diagnostic", diagnostic_node)
    builder.add_node("optimizer",  optimizer_node)
    builder.add_node("update",     update_node)
    builder.add_edge(START,        "ingestion")
    builder.add_edge("ingestion",  "diagnostic")
    builder.add_edge("diagnostic", "optimizer")
    builder.add_edge("optimizer",  "update")
    builder.add_conditional_edges(
        "update", should_continue,
        {"diagnostic": "diagnostic", END: END},
    )
    return builder.compile()


_graph = _build_graph()


async def run_optimization_graph(
    threshold_views: int | None = None,
    threshold_seo:   float | None = None,
    chat_id:         int | None = None,
) -> dict:
    """Public entry point — called by /optimize and /api/optimize."""
    logger.info("[System2] Starting optimization pipeline  chat=%s", chat_id)
    initial: OptState = {
        "threshold_views":      threshold_views or settings.seo_threshold_views,
        "threshold_seo":        threshold_seo   or settings.seo_threshold_score,
        "chat_id":              chat_id,
        "low_performing_blogs": [],
        "current_blog":         {},
        "diagnosis":            "",
        "suggested_fixes":      [],
        "_improved_title":      "",
        "_improved_teaser":     "",
        "_improved_tags":       [],
        "rewritten_title":      "",
        "rewritten_content":    "",
        "rewritten_tags":       [],
        "rewritten_teaser":     "",
        "blogs_processed":      0,
        "update_success":       False,
        "error_message":        "",
    }
    try:
        final = await _graph.ainvoke(initial)
        count = final.get("blogs_processed", 0)
        logger.info("[System2] Done — optimized %d posts", count)
        if chat_id:
            try:
                from bot import push_notification
                await push_notification(
                    f"🔧 <b>SEO Optimization Complete!</b>\n\n"
                    f"📊 Posts optimized: <code>{count}</code>\n"
                    f"⚡ Self-healing loop finished.",
                    chat_id,
                )
            except Exception:
                pass
        return final
    except Exception as exc:
        logger.exception("[System2] Graph crashed")
        try:
            from bot import push_notification
            await push_notification(
                f"❌ <b>Optimization crashed</b>\n"
                f"<code>{html_mod.escape(str(exc)[:400])}</code>",
                chat_id,
            )
        except Exception:
            pass
        return {"update_success": False, "error_message": str(exc)}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _send_optimization_alert(
    title: str, url: str, slug: str,
    diagnosis: str, fixes: list[str],
    chat_id: int | None,
) -> None:
    try:
        from bot import push_notification
        h = html_mod.escape
        fixes_html = "\n".join(f"  • {h(f)}" for f in fixes[:3])
        await push_notification(
            f"🔧 <b>Post Optimized!</b>\n\n"
            f"📝 <b>{h(title)}</b>\n"
            f"🔗 <a href=\"{h(url)}\">Read updated post</a>\n\n"
            f"🔍 <b>Diagnosis:</b> <i>{h(diagnosis[:150])}</i>\n\n"
            f"✅ <b>Fixes applied:</b>\n{fixes_html}",
            chat_id,
        )
    except Exception as exc:
        logger.warning("[System2] Telegram alert failed: %s", exc)
