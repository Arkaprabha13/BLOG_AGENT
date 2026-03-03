"""
graph_system1.py — LangGraph System 1: Content Generation Pipeline
Nodes: Scout → Writer → Revisor (loop) → Publisher

STATE KEY FIX: Uses TypedDict so LangGraph can merge partial node updates
correctly — plain `dict` state causes nodes to lose keys from prior nodes.
"""

import html as html_mod
import json
import logging
import re
import time
from typing import Any, Optional, TypedDict

from langgraph.graph import StateGraph, START, END
from slugify import slugify

from clients.groq_client import GroqClient
from clients.devto_client import DevtoClient
from clients.hashnode_client import HashnodeClient
from config import get_settings
from database import get_db, save_blog, update_blog_urls

logger = logging.getLogger(__name__)
settings = get_settings()

groq_c   = GroqClient()
devto    = DevtoClient()
hashnode = HashnodeClient()


# ---------------------------------------------------------------------------
# State schema — TypedDict is REQUIRED for LangGraph field-level merging.
# With plain 'dict', a node that returns {"raw_context": "..."} would DROP
# all other keys (topic, niche, …) from the state.
# ---------------------------------------------------------------------------
class GenState(TypedDict, total=False):
    # Inputs (set by run_generation_graph)
    topic:                str
    niche:                str
    chat_id:              Optional[int]
    max_revisions:        int

    # Scout node
    raw_context:          str

    # Writer node
    draft_markdown:       str
    draft_title:          str
    draft_tags:           list[str]

    # Revisor node
    revision_notes:       str
    hallucination_detected: bool
    revision_count:       int

    # Publisher node
    blog_id:              Optional[int]
    slug:                 str
    main_url:             str
    devto_url:            str
    hashnode_url:         str
    publish_success:      bool
    error_message:        str


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

async def scout_node(state: GenState) -> dict:
    """Research context for the topic using Qwen3-32B."""
    topic = state["topic"]
    niche = state["niche"]
    logger.info("[Scout] Researching: topic=%r niche=%r", topic, niche)
    try:
        raw_context = await groq_c.scout_context(topic, niche)
        logger.info("[Scout] Context length: %d chars", len(raw_context))
        return {"raw_context": raw_context}
    except Exception as exc:
        logger.error("[Scout] Failed: %s", exc, exc_info=True)
        return {"raw_context": f"Research unavailable: {exc}", "error_message": str(exc)}


async def writer_node(state: GenState) -> dict:
    """Draft a full markdown blog post using Qwen3-32B."""
    topic  = state["topic"]
    niche  = state["niche"]
    rev    = state.get("revision_count", 0)
    logger.info("[Writer] Writing post rev#%d for topic=%r", rev, topic)

    context = state.get("raw_context", "")
    if state.get("revision_notes"):
        context += (
            f"\n\n---\n**REVISION NOTES FROM FACT-CHECKER:**\n{state['revision_notes']}\n"
            "Fix all identified issues in this rewrite. Do NOT repeat the same errors."
        )
    try:
        result = await groq_c.write_blog(topic, niche, context)
        logger.info("[Writer] Draft ready: %r (%d chars)",
                    result.get("title", ""), len(result.get("content", "")))
        return {
            "draft_markdown": result.get("content", ""),
            "draft_title":    result.get("title",   topic),
            "draft_tags":     result.get("tags",    [niche, topic.lower()]),
            "revision_notes": "",   # reset after applying
        }
    except Exception as exc:
        logger.error("[Writer] Failed: %s", exc, exc_info=True)
        return {"error_message": str(exc)}


async def revisor_node(state: GenState) -> dict:
    """Fact-check the draft; loop back to writer if hallucinations found."""
    logger.info("[Revisor] Fact-checking …")
    try:
        result = await groq_c.verify_facts(
            state.get("draft_markdown", ""),
            state.get("raw_context", ""),
        )
        detected = bool(result.get("hallucination_detected", False))
        notes    = result.get("revision_notes", "")
        count    = state.get("revision_count", 0)
        logger.info("[Revisor] hallucination=%s  revision_count=%d", detected, count)
        return {
            "hallucination_detected": detected,
            "revision_notes":         notes,
            "revision_count":         count + (1 if detected else 0),
        }
    except Exception as exc:
        logger.error("[Revisor] Failed: %s", exc, exc_info=True)
        # Treat failure as no hallucination — proceed to publish
        return {
            "hallucination_detected": False,
            "revision_notes":         "",
            "revision_count":         state.get("revision_count", 0),
        }


def should_revise(state: GenState) -> str:
    """Conditional edge: loop back to writer or advance to publisher."""
    if (
        state.get("hallucination_detected")
        and state.get("revision_count", 0) < state.get("max_revisions", settings.max_revisions)
    ):
        logger.info("[Router] Revision #%d — looping back", state["revision_count"])
        return "writer"
    logger.info("[Router] Sending to publisher")
    return "publisher"


async def publisher_node(state: GenState) -> dict:
    """Save to DB, push to Dev.to + Hashnode, send Telegram alert."""
    title   = state.get("draft_title") or state["topic"]
    content = state.get("draft_markdown", "")
    tags    = state.get("draft_tags", [state["niche"], state["topic"]])
    topic   = state["topic"]
    niche   = state["niche"]
    logger.info("[Publisher] Publishing %r", title)

    # Teaser
    teaser = _extract_teaser(content)

    # Slug (with collision protection)
    base_slug = slugify(title[:80])
    slug      = base_slug
    try:
        blog_id = await save_blog(
            slug=slug, topic=topic, niche=niche, title=title,
            markdown_content=content, teaser=teaser,
            main_url=f"{settings.base_url}/blog/{slug}",
            status="draft", tags=tags,
        )
    except Exception:
        slug    = f"{base_slug}-{int(time.time()) % 10000}"
        blog_id = await save_blog(
            slug=slug, topic=topic, niche=niche, title=title,
            markdown_content=content, teaser=teaser,
            main_url=f"{settings.base_url}/blog/{slug}",
            status="draft", tags=tags,
        )

    main_url = f"{settings.base_url}/blog/{slug}"
    logger.info("[Publisher] DB save OK  id=%d  slug=%s", blog_id, slug)

    # Syndication (errors are non-fatal)
    devto_url = hashnode_url = ""
    try:
        r = await devto.publish(title, content, tags, main_url, teaser)
        devto_url = r.get("url", "")
        logger.info("[Publisher] Dev.to: %s", devto_url or "no URL")
    except Exception as e:
        logger.warning("[Publisher] Dev.to failed: %s", e)

    try:
        r = await hashnode.publish(title, content, tags, main_url, teaser)
        hashnode_url = r.get("url", "")
        logger.info("[Publisher] Hashnode: %s", hashnode_url or "no URL")
    except Exception as e:
        logger.warning("[Publisher] Hashnode failed: %s", e)

    # Update DB with final URLs
    await update_blog_urls(blog_id, devto_url=devto_url, hashnode_url=hashnode_url, status="published")

    # Telegram alert
    revisions = state.get("revision_count", 0)
    await _send_publish_alert(
        title, main_url, devto_url, hashnode_url,
        revisions, state.get("chat_id"),
    )

    return {
        "blog_id":        blog_id,
        "slug":           slug,
        "main_url":       main_url,
        "devto_url":      devto_url,
        "hashnode_url":   hashnode_url,
        "publish_success": True,
    }


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------

def _build_graph():
    builder = StateGraph(GenState)
    builder.add_node("scout",     scout_node)
    builder.add_node("writer",    writer_node)
    builder.add_node("revisor",   revisor_node)
    builder.add_node("publisher", publisher_node)
    builder.add_edge(START,       "scout")
    builder.add_edge("scout",     "writer")
    builder.add_edge("writer",    "revisor")
    builder.add_conditional_edges(
        "revisor", should_revise,
        {"writer": "writer", "publisher": "publisher"},
    )
    builder.add_edge("publisher", END)
    return builder.compile()


_graph = _build_graph()


async def run_generation_graph(
    topic: str,
    niche: str,
    chat_id: int | None = None,
    force: bool = False,
) -> dict:
    """
    Public entry point — called by Telegram /generate, /generate_force, FastAPI, and scheduler.

    Args:
        topic:    Blog topic to write about
        niche:    Content category / niche
        chat_id:  Optional Telegram chat ID for push notifications
        force:    If True, skip duplicate detection and generate anyway
    """
    logger.info("[System1] Starting pipeline  topic=%r  niche=%r  chat=%s  force=%s",
                topic, niche, chat_id, force)

    # ── Duplicate check (skip if force=True) ────────────────────────────
    if not force:
        from database import find_similar_blogs
        similar = await find_similar_blogs(topic, threshold=2)
        if similar:
            existing = similar[0]
            main_url = existing.get("main_url") or f"{settings.base_url}/blog/{existing['slug']}"
            logger.info("[System1] Duplicate detected: %r matches existing %r",
                        topic, existing["title"])
            return {
                "publish_success": False,
                "duplicate":       True,
                "duplicate_title": existing["title"],
                "duplicate_url":   main_url,
                "duplicate_slug":  existing["slug"],
                "error_message":   f"Similar post already exists: {existing['title']}",
            }
    # ────────────────────────────────────────────────────────────────────

    initial: GenState = {
        "topic":                topic,
        "niche":                niche,
        "chat_id":              chat_id,
        "max_revisions":        settings.max_revisions,
        "raw_context":          "",
        "draft_markdown":       "",
        "draft_title":          "",
        "draft_tags":           [],
        "revision_notes":       "",
        "hallucination_detected": False,
        "revision_count":       0,
        "blog_id":              None,
        "slug":                 "",
        "main_url":             "",
        "devto_url":            "",
        "hashnode_url":         "",
        "publish_success":      False,
        "error_message":        "",
    }
    try:
        final = await _graph.ainvoke(initial)
        logger.info("[System1] Done  slug=%s  success=%s",
                    final.get("slug"), final.get("publish_success"))
        return final
    except Exception as exc:
        logger.exception("[System1] Graph crashed")
        try:
            from bot import push_notification
            await push_notification(
                f"❌ <b>Generation graph crashed</b>\n"
                f"<code>{html_mod.escape(str(exc)[:400])}</code>",
                chat_id,
            )
        except Exception:
            pass
        return {"publish_success": False, "error_message": str(exc)}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_teaser(text: str) -> str:
    """
    Extract a clean plain-text teaser from markdown content.
    Skips headings/fenced-code lines and strips inline markdown syntax.
    """
    import re
    _MD_STRIP = re.compile(
        r"(\*{1,3}|_{1,3}|`{1,3}|\[|\](\([^)]*\))?|~~|>\s?|^[-=]+$)",
        re.MULTILINE,
    )
    for line in text.split("\n"):
        line = line.strip()
        # Skip headings, fenced-code blocks, horizontal-rules, and blank lines
        if not line:
            continue
        if line.startswith(("#", "```", "   ", "\t", "|", "---", "===")):
            continue
        # Strip inline markdown markers
        clean = _MD_STRIP.sub("", line).strip()
        if len(clean) > 40:
            return clean[:300]
    # Fallback: strip whole text
    return _MD_STRIP.sub("", text[:300]).strip()


async def _send_publish_alert(
    title: str,
    main_url: str,
    devto_url: str,
    hashnode_url: str,
    revisions: int,
    chat_id: int | None,
) -> None:
    try:
        from bot import push_notification
        h = html_mod.escape
        lines = [
            "✅ <b>New Blog Published!</b>\n",
            f"📝 <b>{h(title)}</b>",
            f"🔗 <a href=\"{h(main_url)}\">Read on Blog Empire</a>",
        ]
        if devto_url:
            lines.append(f"📰 <a href=\"{h(devto_url)}\">Dev.to</a>")
        if hashnode_url:
            lines.append(f"🟢 <a href=\"{h(hashnode_url)}\">Hashnode</a>")
        if revisions > 0:
            lines.append(f"\n🔁 Revised {revisions}× by fact-checker")
        await push_notification("\n".join(lines), chat_id)
    except Exception as exc:
        logger.warning("[System1] Telegram alert failed: %s", exc)
