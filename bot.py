"""
bot.py — Telegram Bot Command Center (aiogram 3.x)
Uses HTML parse mode throughout for maximum reliability.

SLASH COMMANDS:
  /start            — Full help menu
  /list             — List all blogs with platform links
  /view <slug>      — View a single blog's details & links
  /generate t n     — Generate blog (with duplicate check)
  /generate_force t n — Force generate (bypass duplicate check)
  /optimize         — Run self-healing SEO optimizer
  /stats            — Top 5 posts by traffic
  /syndicate <slug> — Re-publish a post to Dev.to & Hashnode
  /delete <slug>    — Delete a post from DB (with confirmation)
  /schedule         — Daily scheduler status
  /trending         — Today's trending topics

AI AGENT MODE:
  Just send any plain text — Qwen3-32B understands intent automatically.
  Examples:
    "write a blog about Rust performance"
    "show me all blogs"
    "delete the post about vectorless databases"
    "optimize my posts"
    "what's trending today?"
"""

import asyncio
import html as html_mod
import json
import logging
from typing import Optional

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, BotCommand
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from config import get_settings
from database import (
    fetch_top_blogs, list_all_blogs, delete_blog,
    get_blog_by_slug, get_blog_count,
)

logger   = logging.getLogger(__name__)
settings = get_settings()

# ---------------------------------------------------------------------------
# Globals
# ---------------------------------------------------------------------------
_bot: Optional[Bot] = None
_dp:  Optional[Dispatcher] = None

router = Router()

# Slugs awaiting /delete confirmation  { user_id: slug }
_pending_delete: dict[int, str] = {}


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------
def _h(text) -> str:
    return html_mod.escape(str(text or ""))


def _platform_links(b: dict) -> str:
    """Build clickable platform link pills for a blog dict."""
    parts = []
    main = b.get("main_url") or f"{settings.base_url}/blog/{b.get('slug','')}"
    parts.append(f'<a href="{_h(main)}">🌐 Website</a>')
    devto_url = b.get("devto_url") or ""
    hashnode_url = b.get("hashnode_url") or ""
    if devto_url:
        parts.append(f'<a href="{_h(devto_url)}">📝 Dev.to</a>')
    if hashnode_url:
        parts.append(f'<a href="{_h(hashnode_url)}">📰 Hashnode</a>')
    return "  •  ".join(parts)


def _fmt_blog_card(b: dict, idx: int | None = None) -> str:
    title  = _h(b.get("title") or b.get("topic") or "Untitled")
    slug   = _h(b.get("slug", ""))
    views  = int(b.get("views") or 0)
    seo    = float(b.get("seo_score") or 0.0)
    status = _h(b.get("status", ""))
    niche  = _h(b.get("niche") or "")
    date   = _h((b.get("publish_date") or "")[:10])

    prefix = f"{idx}. " if idx is not None else ""
    return (
        f"{prefix}<b>{title}</b>\n"
        f"   🏷 {niche}  •  📅 {date}  •  <i>{status}</i>\n"
        f"   👁 {views} views  •  SEO {seo:.0f}/100\n"
        f"   {_platform_links(b)}\n"
        f"   🔑 slug: <code>{slug}</code>\n"
    )


def _fmt_stats(blogs: list[dict]) -> str:
    if not blogs:
        return (
            "📭 <b>No published blogs yet.</b>\n\n"
            "Use <code>/generate topic niche</code> to create your first post!"
        )
    lines = ["📊 <b>Top Blog Posts by Views</b>\n"]
    for i, b in enumerate(blogs, 1):
        lines.append(_fmt_blog_card(b, i))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# /start — help
# ---------------------------------------------------------------------------
@router.message(CommandStart())
async def cmd_start(msg: Message):
    await msg.answer(
        "⚡ <b>Blog Empire Command Center</b>\n\n"
        "<b>📋 Listings:</b>\n"
        "• <code>/list</code> — All published blogs + platform links\n"
        "• <code>/view &lt;slug&gt;</code> — View a post's full details\n"
        "• <code>/stats</code> — Top 5 posts by traffic\n\n"
        "<b>✍️ Content:</b>\n"
        "• <code>/generate &lt;topic&gt; [niche]</code> — Create new AI blog\n"
        "• <code>/generate_force &lt;topic&gt; [niche]</code> — Force-generate\n"
        "• <code>/optimize</code> — Run SEO self-healing optimizer\n\n"
        "<b>🔗 Syndication:</b>\n"
        "• <code>/syndicate &lt;slug&gt;</code> — Push post to Dev.to &amp; Hashnode\n\n"
        "<b>🗑 Management:</b>\n"
        "• <code>/delete &lt;slug&gt;</code> — Delete a post (asks for confirm)\n\n"
        "<b>📡 Autopilot:</b>\n"
        "• <code>/schedule</code> — Daily scheduler status\n"
        "• <code>/trending</code> — Today's trending topics\n\n"
        "💬 <i>Or just chat naturally — the AI agent understands everything!</i>",
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# /list — all blogs
# ---------------------------------------------------------------------------
@router.message(Command("list"))
async def cmd_list(msg: Message):
    await msg.answer("📋 <b>Fetching your blog list…</b>", parse_mode=ParseMode.HTML)
    try:
        blogs = await list_all_blogs(limit=15)
        total = await get_blog_count()
        if not blogs:
            await msg.answer(
                "📭 <b>No blogs yet!</b>\n"
                "Use <code>/generate topic niche</code> to start.",
                parse_mode=ParseMode.HTML,
            )
            return

        header = f"📚 <b>All Blogs ({total} total)</b>\n\n"
        chunks = [header]
        for i, b in enumerate(blogs, 1):
            card = _fmt_blog_card(b, i)
            # Telegram msg limit is 4096 chars — split if needed
            if sum(len(c) for c in chunks) + len(card) > 3800:
                await msg.answer("".join(chunks), parse_mode=ParseMode.HTML,
                                 disable_web_page_preview=True)
                chunks = []
            chunks.append(card + "\n")

        if chunks:
            await msg.answer("".join(chunks), parse_mode=ParseMode.HTML,
                             disable_web_page_preview=True)

        if total > 15:
            await msg.answer(
                f"<i>Showing 15 of {total} posts. More pagination coming soon!</i>",
                parse_mode=ParseMode.HTML,
            )
    except Exception as exc:
        logger.exception("list command error")
        await msg.answer(f"❌ Error: <code>{_h(str(exc)[:200])}</code>",
                         parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# /view <slug>
# ---------------------------------------------------------------------------
@router.message(Command("view"))
async def cmd_view(msg: Message):
    parts = (msg.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await msg.answer(
            "Usage: <code>/view &lt;slug&gt;</code>\n"
            "Get the slug from <code>/list</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    slug = parts[1].strip()
    try:
        b = await get_blog_by_slug(slug)
        if not b:
            await msg.answer(f"❌ No blog found with slug <code>{_h(slug)}</code>",
                             parse_mode=ParseMode.HTML)
            return
        tags = b.get("tags") or []
        if isinstance(tags, str):
            try: tags = json.loads(tags)
            except: tags = []
        tags_str = "  ".join(f"#{_h(t)}" for t in tags) or "—"
        teaser = _h((b.get("teaser") or "")[:200])

        text = (
            f"📄 <b>{_h(b.get('title') or 'Untitled')}</b>\n\n"
            f"🏷 Niche: <code>{_h(b.get('niche',''))}</code>\n"
            f"📅 Published: <code>{(b.get('publish_date') or '')[:10]}</code>\n"
            f"📌 Status: <i>{_h(b.get('status',''))}</i>\n"
            f"🔑 Slug: <code>{_h(slug)}</code>\n\n"
            f"📝 <i>{teaser}</i>\n\n"
            f"🏷 Tags: {tags_str}\n\n"
            f"<b>Platform Links:</b>\n"
            f"{_platform_links(b)}\n\n"
            f"🗑 To delete: <code>/delete {_h(slug)}</code>\n"
            f"🔗 To syndicate: <code>/syndicate {_h(slug)}</code>"
        )
        await msg.answer(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    except Exception as exc:
        logger.exception("view command error")
        await msg.answer(f"❌ Error: <code>{_h(str(exc)[:200])}</code>",
                         parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# /stats
# ---------------------------------------------------------------------------
@router.message(Command("stats"))
async def cmd_stats(msg: Message):
    await msg.answer("🔍 <b>Fetching stats…</b>", parse_mode=ParseMode.HTML)
    try:
        blogs = await fetch_top_blogs(5)
        await msg.answer(_fmt_stats(blogs), parse_mode=ParseMode.HTML,
                         disable_web_page_preview=True)
    except Exception as exc:
        logger.exception("stats command error")
        await msg.answer(f"❌ Error: <code>{_h(str(exc))}</code>",
                         parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# /generate  and  /generate_force
# ---------------------------------------------------------------------------
@router.message(Command("generate"))
async def cmd_generate(msg: Message):
    parts = (msg.text or "").split(maxsplit=2)
    if len(parts) < 2:
        await msg.answer(
            "Usage: <code>/generate &lt;topic&gt; [niche]</code>\n"
            "Example: <code>/generate LangGraph Agents AI</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    topic   = parts[1]
    niche   = parts[2] if len(parts) > 2 else "technology"
    chat_id = msg.chat.id
    await msg.answer(
        f"🔍 <b>Checking for duplicates…</b>\n"
        f"📌 Topic: <code>{_h(topic)}</code>  •  Niche: <code>{_h(niche)}</code>\n"
        f"<i>Researching → Writing → Fact-checking → Publishing…</i>\n"
        f"You'll get a notification when it's live ✅",
        parse_mode=ParseMode.HTML,
    )
    asyncio.create_task(_run_generation(topic, niche, chat_id, force=False))


@router.message(Command("generate_force"))
async def cmd_generate_force(msg: Message):
    parts = (msg.text or "").split(maxsplit=2)
    if len(parts) < 2:
        await msg.answer(
            "Usage: <code>/generate_force &lt;topic&gt; [niche]</code>\n"
            "Bypasses the duplicate check and generates anyway.",
            parse_mode=ParseMode.HTML,
        )
        return
    topic   = parts[1]
    niche   = parts[2] if len(parts) > 2 else "technology"
    chat_id = msg.chat.id
    await msg.answer(
        f"⚡ <b>Force-generating (duplicate check bypassed)</b>\n"
        f"📌 Topic: <code>{_h(topic)}</code>  •  Niche: <code>{_h(niche)}</code>\n"
        f"<i>Researching → Writing → Fact-checking → Publishing…</i>\n"
        f"You'll get a notification when it's live ✅",
        parse_mode=ParseMode.HTML,
    )
    asyncio.create_task(_run_generation(topic, niche, chat_id, force=True))


# ---------------------------------------------------------------------------
# /syndicate <slug> — re-publish to Dev.to + Hashnode
# ---------------------------------------------------------------------------
@router.message(Command("syndicate"))
async def cmd_syndicate(msg: Message):
    parts = (msg.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await msg.answer(
            "Usage: <code>/syndicate &lt;slug&gt;</code>\n"
            "Publishes the post to Dev.to and Hashnode.\n"
            "Get slugs from <code>/list</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    slug    = parts[1].strip()
    chat_id = msg.chat.id
    await msg.answer(
        f"🔗 <b>Syndicating <code>{_h(slug)}</code>…</b>\n"
        "<i>Pushing to Dev.to and Hashnode now…</i>",
        parse_mode=ParseMode.HTML,
    )
    asyncio.create_task(_run_syndicate(slug, chat_id))


# ---------------------------------------------------------------------------
# /delete <slug>
# ---------------------------------------------------------------------------
@router.message(Command("delete"))
async def cmd_delete(msg: Message):
    parts = (msg.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await msg.answer(
            "Usage: <code>/delete &lt;slug&gt;</code>\n"
            "Get slugs from <code>/list</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    slug    = parts[1].strip()
    user_id = msg.from_user.id if msg.from_user else 0

    # Check it exists first
    b = await get_blog_by_slug(slug)
    if not b:
        await msg.answer(f"❌ No blog found with slug <code>{_h(slug)}</code>",
                         parse_mode=ParseMode.HTML)
        return

    _pending_delete[user_id] = slug
    await msg.answer(
        f"⚠️ <b>Are you sure?</b>\n\n"
        f"This will permanently delete:\n"
        f"📝 <b>{_h(b.get('title',''))}</b>\n"
        f"🔑 <code>{_h(slug)}</code>\n\n"
        f"Reply <code>yes</code> to confirm, or anything else to cancel.",
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# /optimize
# ---------------------------------------------------------------------------
@router.message(Command("optimize"))
async def cmd_optimize(msg: Message):
    chat_id = msg.chat.id
    await msg.answer(
        "🔧 <b>SEO Optimization pipeline triggered!</b>\n\n"
        "<i>Scanning for low-performing posts…</i>\n"
        "You'll receive a report when complete ✅",
        parse_mode=ParseMode.HTML,
    )
    asyncio.create_task(_run_optimization(chat_id))


# ---------------------------------------------------------------------------
# /schedule
# ---------------------------------------------------------------------------
@router.message(Command("schedule"))
async def cmd_schedule(msg: Message):
    try:
        from scheduler import scheduler
        s        = scheduler.status()
        next_run = s["next_run"] or "Not scheduled yet"
        last_run = s["last_run"] or "Never"
        await msg.answer(
            f"📅 <b>Daily Auto-Blog Scheduler</b>\n\n"
            f"Status:       <code>{'🟢 Running' if s['running'] else '🔴 Stopped'}</code>\n"
            f"Daily Target: <code>{s['daily_target']} blogs/day</code>\n"
            f"Last Run:     <code>{_h(last_run)}</code>\n"
            f"Last Count:   <code>{s['last_count']} blogs published</code>\n"
            f"Next Run:     <code>{_h(next_run)}</code>\n\n"
            f"💡 Use <code>/trending</code> to preview today's topic queue",
            parse_mode=ParseMode.HTML,
        )
    except Exception as exc:
        await msg.answer(f"❌ Scheduler error: <code>{_h(str(exc))}</code>",
                         parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# /trending
# ---------------------------------------------------------------------------
@router.message(Command("trending"))
async def cmd_trending(msg: Message):
    await msg.answer("🌐 <b>Fetching trending topics…</b>", parse_mode=ParseMode.HTML)
    await _fetch_and_send_trending(msg)


# ---------------------------------------------------------------------------
# AI Agent — ALL plain text messages
# ---------------------------------------------------------------------------
@router.message()
async def cmd_agent(msg: Message):
    """
    Handles ALL unmatched messages via Qwen3-32B intent detection.
    Supports: generate, generate_force, optimize, stats, list, trending,
              schedule, view <slug>, delete <slug>, syndicate <slug>, chat.
    Also handles delete confirmations ("yes" / "no").
    """
    from agent import process_message

    text    = (msg.text or "").strip()
    user_id = msg.from_user.id if msg.from_user else 0
    name    = msg.from_user.first_name if msg.from_user else "there"
    chat_id = msg.chat.id

    if not text:
        return

    # ── Delete confirmation flow ─────────────────────────────────────────
    if user_id in _pending_delete:
        slug = _pending_delete.pop(user_id)
        if text.lower() in ("yes", "y", "confirm", "haan", "ha"):
            try:
                deleted = await delete_blog(slug)
                if deleted:
                    await msg.answer(
                        f"✅ <b>Deleted!</b>\n<code>{_h(slug)}</code> removed from the database.",
                        parse_mode=ParseMode.HTML,
                    )
                else:
                    await msg.answer(f"⚠️ Post <code>{_h(slug)}</code> not found — may already be deleted.",
                                     parse_mode=ParseMode.HTML)
            except Exception as e:
                await msg.answer(f"❌ Delete failed: <code>{_h(str(e)[:200])}</code>",
                                 parse_mode=ParseMode.HTML)
        else:
            await msg.answer("❌ <b>Deletion cancelled.</b>", parse_mode=ParseMode.HTML)
        return

    await msg.bot.send_chat_action(chat_id, "typing")

    try:
        result = await process_message(text, user_id, username=name)
    except Exception as exc:
        logger.exception("Agent error")
        await msg.answer(
            f"❌ Agent error: <code>{_h(str(exc)[:200])}</code>\n"
            "Try /start to see available commands.",
            parse_mode=ParseMode.HTML,
        )
        return

    intent = result.intent
    topic  = result.topic
    niche  = result.niche or "technology"
    reply  = result.reply

    # ── Intent → action dispatch ─────────────────────────────────────────
    if intent == "help":
        await msg.answer(
            f"💬 {_h(reply)}\n\n"
            "⚡ <b>What I can do:</b>\n"
            "• <i>\"write about Python async\"</i> → /generate\n"
            "• <i>\"show all blogs\"</i> → /list\n"
            "• <i>\"show stats\"</i> → /stats\n"
            "• <i>\"delete vectorless-databases\"</i> → /delete\n"
            "• <i>\"syndicate vectorless-databases\"</i> → /syndicate\n"
            "• <i>\"optimize my posts\"</i> → /optimize\n"
            "• <i>\"what's trending?\"</i> → /trending\n"
            "• <i>\"how's the scheduler?\"</i> → /schedule\n",
            parse_mode=ParseMode.HTML,
        )

    elif intent in ("generate", "generate_force") and topic:
        force = (intent == "generate_force")
        await msg.answer(
            f"{'⚡' if force else '🔍'} <b>{'Force-generating' if force else 'Generating'}…</b>\n"
            f"📌 Topic: <code>{_h(topic)}</code>  •  Niche: <code>{_h(niche)}</code>\n"
            f"<i>{_h(reply)}</i>\n"
            f"You'll get a notification when it's live ✅",
            parse_mode=ParseMode.HTML,
        )
        asyncio.create_task(_run_generation(topic, niche, chat_id, force=force))

    elif intent == "list":
        await cmd_list(msg)

    elif intent == "stats":
        try:
            blogs = await fetch_top_blogs(5)
            await msg.answer(
                f"<i>{_h(reply)}</i>\n\n" + _fmt_stats(blogs),
                parse_mode=ParseMode.HTML, disable_web_page_preview=True,
            )
        except Exception as exc:
            await msg.answer(f"❌ Stats error: <code>{_h(str(exc))}</code>",
                             parse_mode=ParseMode.HTML)

    elif intent == "optimize":
        await msg.answer(
            f"🔧 <b>Optimization triggered!</b>\n<i>{_h(reply)}</i>\n"
            "Scanning for low-performing posts… report coming ✅",
            parse_mode=ParseMode.HTML,
        )
        asyncio.create_task(_run_optimization(chat_id))

    elif intent == "trending":
        await msg.answer(f"🌐 <i>{_h(reply)}</i>\n\n<b>Fetching trending topics…</b>",
                         parse_mode=ParseMode.HTML)
        asyncio.create_task(_fetch_and_send_trending(msg))

    elif intent == "schedule":
        try:
            from scheduler import scheduler
            s = scheduler.status()
            await msg.answer(
                f"<i>{_h(reply)}</i>\n\n"
                f"📅 <b>Scheduler Status</b>\n"
                f"Running: {'🟢 Yes' if s['running'] else '🔴 No'}\n"
                f"Daily Target: <code>{s['daily_target']} blogs/day</code>\n"
                f"Next Run: <code>{_h(s['next_run'] or 'Not scheduled')}</code>\n"
                f"Last Batch: <code>{s['last_count']} blogs</code>",
                parse_mode=ParseMode.HTML,
            )
        except Exception as exc:
            await msg.answer(f"❌ Scheduler error: <code>{_h(str(exc))}</code>",
                             parse_mode=ParseMode.HTML)

    else:
        # Pure chat or unknown intent — just reply
        await msg.answer(
            _h(reply) if reply else "🤔 I'm not sure how to help. Use /start to see all commands!",
            parse_mode=ParseMode.HTML,
        )


# ---------------------------------------------------------------------------
# Background task helpers
# ---------------------------------------------------------------------------
async def _fetch_and_send_trending(msg: Message) -> None:
    try:
        from clients.trends_client import TrendsClient
        topics = await TrendsClient().fetch_trending(limit=8)
        if not topics:
            await msg.answer("⚠️ No trending topics found right now. Try again later.",
                             parse_mode=ParseMode.HTML)
            return
        lines = ["🔥 <b>Trending Topics Right Now</b>\n"]
        for i, t in enumerate(topics, 1):
            lines.append(
                f"{i}. <b>{_h(t['topic'])}</b>\n"
                f"   Niche: <code>{_h(t['niche'])}</code>  •  <i>{_h(t['source'])}</i>\n"
            )
        lines.append("\n💡 Just say: <i>\"write about &lt;topic&gt;\"</i> and I'll do it!")
        await msg.answer("\n".join(lines), parse_mode=ParseMode.HTML)
    except Exception as exc:
        await msg.answer(f"❌ Trending fetch failed: <code>{_h(str(exc))}</code>",
                         parse_mode=ParseMode.HTML)


async def _run_generation(topic: str, niche: str, chat_id: int, force: bool = False):
    try:
        from graph_system1 import run_generation_graph
        result = await run_generation_graph(topic, niche, chat_id=chat_id, force=force)

        if result.get("duplicate"):
            dup_title = _h(result.get("duplicate_title", "Unknown"))
            dup_url   = _h(result.get("duplicate_url", settings.base_url))
            await push_notification(
                f"⚠️ <b>Duplicate Detected!</b>\n\n"
                f"A similar post already exists:\n"
                f"📝 <b>{dup_title}</b>\n"
                f"🔗 <a href=\"{dup_url}\">Read existing post</a>\n\n"
                f"💡 To write anyway: <code>/generate_force {_h(topic)} {_h(niche)}</code>",
                chat_id,
            )
            return

        if not result.get("publish_success"):
            err = _h(result.get("error_message", "Unknown error"))
            await push_notification(
                f"❌ <b>Generation failed</b> for <code>{_h(topic)}</code>\n"
                f"<code>{err[:300]}</code>",
                chat_id,
            )
    except Exception as exc:
        logger.exception("Generation graph error")
        await push_notification(
            f"❌ <b>Generation crashed</b> for <code>{_h(topic)}</code>\n"
            f"<code>{_h(str(exc)[:300])}</code>",
            chat_id,
        )


async def _run_optimization(chat_id: int):
    try:
        from graph_system2 import run_optimization_graph
        await run_optimization_graph(chat_id=chat_id)
    except Exception as exc:
        logger.exception("Optimization graph error")
        await push_notification(
            f"❌ <b>Optimization failed</b>\n<code>{_h(str(exc)[:300])}</code>",
            chat_id,
        )


async def _run_syndicate(slug: str, chat_id: int):
    """Re-publish a post to Dev.to and Hashnode."""
    try:
        from database import get_blog_by_slug, update_blog_urls
        from clients.devto_client import DevtoClient
        from clients.hashnode_client import HashnodeClient

        b = await get_blog_by_slug(slug)
        if not b:
            await push_notification(
                f"❌ Syndication failed: slug <code>{_h(slug)}</code> not found.",
                chat_id,
            )
            return

        title   = b.get("title", "")
        content = b.get("markdown_content", "")
        teaser  = b.get("teaser", "")
        tags    = b.get("tags") or []
        if isinstance(tags, str):
            try: tags = json.loads(tags)
            except: tags = []
        main_url = b.get("main_url") or f"{settings.base_url}/blog/{slug}"

        devto    = DevtoClient()
        hashnode = HashnodeClient()

        devto_url    = ""
        hashnode_url = ""

        if settings.base_url and "localhost" not in settings.base_url:
            r = await devto.publish(title, content, tags, main_url, teaser)
            devto_url = r.get("url", "")

            r2 = await hashnode.publish(title, content, tags, main_url, teaser)
            hashnode_url = r2.get("url", "")

            await update_blog_urls(
                b["id"],
                devto_url=devto_url,
                hashnode_url=hashnode_url,
                status="published",
            )
            lines = [f"✅ <b>Syndicated: <code>{_h(slug)}</code></b>\n"]
            if devto_url:
                lines.append(f"📝 Dev.to: <a href=\"{_h(devto_url)}\">View</a>")
            else:
                lines.append("📝 Dev.to: skipped (no URL returned)")
            if hashnode_url:
                lines.append(f"📰 Hashnode: <a href=\"{_h(hashnode_url)}\">View</a>")
            else:
                lines.append("📰 Hashnode: skipped (no URL returned)")
            await push_notification("\n".join(lines), chat_id)
        else:
            await push_notification(
                f"⚠️ <b>Syndication skipped</b> — BASE_URL is still localhost.\n"
                f"Set <code>BASE_URL=https://your-domain.com</code> in .env to enable syndication.",
                chat_id,
            )
    except Exception as exc:
        logger.exception("Syndicate error for slug=%s", slug)
        await push_notification(
            f"❌ <b>Syndication failed</b> for <code>{_h(slug)}</code>\n"
            f"<code>{_h(str(exc)[:300])}</code>",
            chat_id,
        )


# ---------------------------------------------------------------------------
# Push notification helper (called by graph nodes and scheduler)
# ---------------------------------------------------------------------------
async def push_notification(html_text: str, chat_id: int | None = None):
    """Send an HTML-formatted message to a Telegram chat."""
    global _bot
    if _bot is None:
        logger.warning("Bot not initialised — cannot push notification")
        return
    target = chat_id or settings.telegram_admin_chat_id
    try:
        await _bot.send_message(
            chat_id=target,
            text=html_text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception as exc:
        logger.warning("push_notification failed (chat=%s): %s", target, exc)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------
async def start_bot():
    """Start aiogram polling loop (runs until stop_bot() is called)."""
    global _bot, _dp

    _bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    _dp = Dispatcher()
    _dp.include_router(router)

    await _bot.set_my_commands([
        BotCommand(command="start",          description="Show help & all commands"),
        BotCommand(command="list",           description="List all published blogs"),
        BotCommand(command="view",           description="View a post: /view <slug>"),
        BotCommand(command="generate",       description="Generate a post: /generate topic niche"),
        BotCommand(command="generate_force", description="Force-generate (skip duplicate check)"),
        BotCommand(command="syndicate",      description="Push to Dev.to & Hashnode: /syndicate <slug>"),
        BotCommand(command="delete",         description="Delete a post: /delete <slug>"),
        BotCommand(command="optimize",       description="Run SEO self-healing optimizer"),
        BotCommand(command="stats",          description="Top 5 posts by traffic"),
        BotCommand(command="schedule",       description="Daily scheduler status"),
        BotCommand(command="trending",       description="Preview trending topics"),
    ])

    logger.info("Telegram bot polling started")
    await _dp.start_polling(_bot, allowed_updates=["message"], skip_updates=True)


async def stop_bot():
    """Gracefully stop polling and close the Bot session."""
    global _bot, _dp
    if _dp:
        await _dp.stop_polling()
    if _bot:
        await _bot.session.close()
    logger.info("Telegram bot stopped")
