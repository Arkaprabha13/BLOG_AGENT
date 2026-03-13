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

# Discussion sessions  { user_id: {"topic": str, "history": list[dict]} }
_discussions: dict[int, dict] = {}

_DISCUSS_SYSTEM = """\
You are a knowledgeable expert and engaging conversationalist helping the user explore
a topic deeply so it can later be turned into a well-structured blog post.
Ask thought-provoking follow-up questions, share interesting facts, and highlight
angles the user might not have considered. Keep replies concise (3-5 sentences max)
so the conversation stays dynamic. Do NOT write the blog yet — that happens only
when the user explicitly asks with /writeblog.
"""


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------
def _h(text) -> str:
    return html_mod.escape(str(text or ""))


def _split_html_safe(text: str, limit: int = 3800) -> list[str]:
    """Split *text* into chunks that respect newline boundaries.

    Naively slicing at every *limit* characters can cut an HTML tag in half
    (e.g. ``<a href="…">``), which causes Telegram to reject the message with
    ``TelegramBadRequest: can't parse entities: Unclosed start tag``.

    This helper splits on ``\\n`` boundaries so every chunk contains only
    complete lines — and therefore complete HTML tags, since we always build
    messages line-by-line.
    """
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for line in text.split("\n"):
        # +1 accounts for the newline character we'll re-join with
        line_len = len(line) + 1
        if current and current_len + line_len > limit:
            chunks.append("\n".join(current))
            current = []
            current_len = 0
        current.append(line)
        current_len += line_len

    if current:
        chunks.append("\n".join(current))

    return chunks


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


async def _fetch_comprehensive_stats() -> str:
    """
    Fetch stats from DB + Hashnode + Dev.to in parallel and compose
    a rich stats dashboard message.
    """
    from database import list_all_blogs, get_blog_count
    from clients.devto_client import DevtoClient
    from clients.hashnode_client import HashnodeClient

    # Fetch everything in parallel
    db_blogs, hn_posts, dt_articles, total = await asyncio.gather(
        list_all_blogs(limit=30),
        HashnodeClient().get_my_posts(first=30),
        DevtoClient().get_my_articles(per_page=30),
        get_blog_count(),
        return_exceptions=True,
    )

    # Safely default on error
    if isinstance(db_blogs, Exception):    db_blogs = []
    if isinstance(hn_posts, Exception):    hn_posts = []
    if isinstance(dt_articles, Exception): dt_articles = []
    if isinstance(total, Exception):       total = len(db_blogs)

    # Build lookup maps by URL fragment (slug)
    def _slug_from_url(url: str) -> str:
        return (url or "").rstrip("/").split("/")[-1].lower()

    hn_map  = {_slug_from_url(p["url"]): p for p in hn_posts}
    dt_map  = {_slug_from_url(a["url"]): a for a in dt_articles}

    # Aggregate platform totals
    hn_total_views     = sum(p.get("views", 0) for p in hn_posts)
    hn_total_reactions = sum(p.get("reactions", 0) for p in hn_posts)
    hn_total_comments  = sum(p.get("comments", 0) for p in hn_posts)
    dt_total_views     = sum(a.get("views", 0) for a in dt_articles)
    dt_total_reactions = sum(a.get("reactions", 0) for a in dt_articles)
    dt_total_comments  = sum(a.get("comments", 0) for a in dt_articles)
    db_total_views     = sum(int(b.get("views") or 0) for b in db_blogs)

    lines = [
        "📊 <b>Blog Empire — Comprehensive Stats</b>\n",
        f"📚 Total posts: <b>{total}</b>\n",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "<b>🌐 Platform Totals</b>",
        f"   🌍 Website views:      <b>{db_total_views}</b>",
    ]
    if hn_posts:
        lines.append(
            f"   📰 Hashnode:  👁 <b>{hn_total_views}</b>  ❤️ <b>{hn_total_reactions}</b>  💬 <b>{hn_total_comments}</b>"
        )
    else:
        lines.append("   📰 Hashnode:  <i>not connected</i>")
    if dt_articles:
        lines.append(
            f"   📝 Dev.to:    👁 <b>{dt_total_views}</b>  ❤️ <b>{dt_total_reactions}</b>  💬 <b>{dt_total_comments}</b>"
        )
    else:
        lines.append("   📝 Dev.to:    <i>not connected</i>")

    lines.append("\n━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("<b>📄 Per-Post Breakdown</b>\n")

    # Sort DB blogs by website views desc
    db_blogs_sorted = sorted(db_blogs, key=lambda b: int(b.get("views") or 0), reverse=True)

    for i, b in enumerate(db_blogs_sorted[:10], 1):
        slug      = b.get("slug", "")
        title     = _h(b.get("title") or b.get("topic") or "Untitled")
        db_views  = int(b.get("views") or 0)
        seo       = float(b.get("seo_score") or 0.0)
        status    = _h(b.get("status", ""))
        date      = (b.get("publish_date") or "")[:10]
        hn_url    = _h(b.get("hashnode_url") or "")
        dt_url    = _h(b.get("devto_url") or "")
        main_url  = _h(b.get("main_url") or f"{settings.base_url}/blog/{slug}")

        # Match platform stats
        hn_data = hn_map.get(slug) or hn_map.get(_slug_from_url(hn_url)) or {}
        dt_data = dt_map.get(slug) or dt_map.get(_slug_from_url(dt_url)) or {}

        hn_views = hn_data.get("views", 0)
        hn_reac  = hn_data.get("reactions", 0)
        hn_comm  = hn_data.get("comments", 0)
        dt_views = dt_data.get("views", 0)
        dt_reac  = dt_data.get("reactions", 0)
        dt_comm  = dt_data.get("comments", 0)

        block = [
            f"{i}. <b>{title}</b>",
            f"   📅 {date}  •  <i>{status}</i>  •  SEO {seo:.0f}/100",
            f"   🌍 Website: 👁 <b>{db_views}</b>",
        ]
        if hn_url:
            block.append(
                f"   📰 <a href=\"{hn_url}\">Hashnode</a>: "
                f"👁 <b>{hn_views}</b>  ❤️ {hn_reac}  💬 {hn_comm}"
            )
        if dt_url:
            block.append(
                f"   📝 <a href=\"{dt_url}\">Dev.to</a>: "
                f"👁 <b>{dt_views}</b>  ❤️ {dt_reac}  💬 {dt_comm}"
            )
        block.append(f"   <a href=\"{main_url}\">🔗 Read</a>  •  🔑 <code>{_h(slug)}</code>")
        lines.append("\n".join(block) + "\n")

    if not db_blogs:
        lines.append("📭 <i>No blogs yet. Use /generate to create one!</i>")

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
        "<b>📡 News &amp; Recommendations:</b>\n"
        "• <code>/recommend</code> — Get 5-10 AI-curated blog topic ideas from today's news\n\n"
        "<b>💬 Discussion → Blog:</b>\n"
        "• <code>/discuss &lt;topic&gt;</code> — Start an AI conversation on any topic\n"
        "• <code>/writeblog</code> — Convert your discussion into a published blog\n"
        "• <code>/enddiscuss</code> — End discussion without publishing\n\n"
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
    await msg.answer(
        "📡 <b>Fetching stats from all platforms…</b>\n"
        "<i>Pulling Website, Hashnode &amp; Dev.to data…</i>",
        parse_mode=ParseMode.HTML,
    )
    try:
        text = await _fetch_comprehensive_stats()
        # Split if too long for one Telegram message (safe on newline boundaries)
        for chunk in _split_html_safe(text):
            await msg.answer(chunk, parse_mode=ParseMode.HTML,
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
# /recommend — AI-curated news-based topic suggestions
# ---------------------------------------------------------------------------
@router.message(Command("recommend"))
async def cmd_recommend(msg: Message):
    await msg.answer(
        "📡 <b>Fetching today's news and curating blog topics…</b>\n"
        "<i>This may take 10-20 seconds while I read the news for you.</i>",
        parse_mode=ParseMode.HTML,
    )
    try:
        from scheduler import scheduler
        suggestions = await scheduler.recommendation_push(manual=True)
        if not suggestions:
            await msg.answer(
                "⚠️ No suggestions generated right now.\n"
                "Try <code>/trending</code> for free trending topics instead.",
                parse_mode=ParseMode.HTML,
            )
    except Exception as exc:
        logger.exception("recommend command error")
        await msg.answer(
            f"❌ Recommend error: <code>{_h(str(exc)[:200])}</code>",
            parse_mode=ParseMode.HTML,
        )


# ---------------------------------------------------------------------------
# /discuss <topic> — Start a multi-turn AI discussion
# ---------------------------------------------------------------------------
@router.message(Command("discuss"))
async def cmd_discuss(msg: Message):
    parts = (msg.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await msg.answer(
            "Usage: <code>/discuss &lt;topic&gt;</code>\n"
            "Example: <code>/discuss quantum computing in education</code>\n\n"
            "I'll chat with you about it and when ready, use <code>/writeblog</code> "
            "to turn our discussion into a published blog post!",
            parse_mode=ParseMode.HTML,
        )
        return

    topic   = parts[1].strip()
    user_id = msg.from_user.id if msg.from_user else 0
    name    = msg.from_user.first_name if msg.from_user else "friend"

    # Initialise session
    _discussions[user_id] = {"topic": topic, "history": []}

    # Get first AI message
    reply = await _discuss_turn(user_id, topic, f"Let's discuss: {topic}")
    await msg.answer(
        f"💬 <b>Discussion started: {_h(topic)}</b>\n\n"
        f"{_h(reply)}\n\n"
        f"<i>Reply to continue the conversation. Use /writeblog when ready to publish!</i>",
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# /writeblog — Convert active discussion into a published blog
# ---------------------------------------------------------------------------
@router.message(Command("writeblog"))
async def cmd_writeblog(msg: Message):
    user_id = msg.from_user.id if msg.from_user else 0
    session = _discussions.get(user_id)

    if not session:
        await msg.answer(
            "⚠️ No active discussion found.\n"
            "Start one with <code>/discuss &lt;topic&gt;</code> first!",
            parse_mode=ParseMode.HTML,
        )
        return

    topic   = session["topic"]
    history = session["history"]
    chat_id = msg.chat.id

    await msg.answer(
        f"✍️ <b>Converting our discussion into a blog post…</b>\n"
        f"📌 Topic: <code>{_h(topic)}</code>\n"
        f"<i>Researching → Writing → Fact-checking → Publishing…</i>\n"
        f"You'll get a notification when it's live ✅",
        parse_mode=ParseMode.HTML,
    )

    # Build discussion context for the generation graph
    context_lines = [f"DISCUSSION TOPIC: {topic}\n"]
    for turn in history:
        role = "USER" if turn["role"] == "user" else "AI EXPERT"
        context_lines.append(f"{role}: {turn['content']}")
    discussion_context = "\n".join(context_lines)

    # Clear session
    _discussions.pop(user_id, None)

    asyncio.create_task(_run_generation_with_context(topic, "technology", chat_id, discussion_context))


# ---------------------------------------------------------------------------
# /enddiscuss — End discussion session
# ---------------------------------------------------------------------------
@router.message(Command("enddiscuss"))
async def cmd_enddiscuss(msg: Message):
    user_id = msg.from_user.id if msg.from_user else 0
    session = _discussions.pop(user_id, None)
    if session:
        await msg.answer(
            f"🚫 <b>Discussion ended.</b>\n"
            f"Topic: <i>{_h(session['topic'])}</i>\n\n"
            f"No blog was written. Use <code>/discuss</code> to start a new conversation.",
            parse_mode=ParseMode.HTML,
        )
    else:
        await msg.answer(
            "ℹ️ No active discussion to end. Use <code>/discuss &lt;topic&gt;</code> to start one.",
            parse_mode=ParseMode.HTML,
        )


# ---------------------------------------------------------------------------
# AI Agent — ALL plain text messages
# ---------------------------------------------------------------------------
@router.message()
async def cmd_agent(msg: Message):
    """
    Handles ALL unmatched messages.
    Routes to discussion mode if user has an active session,
    otherwise uses Qwen3-32B intent detection.
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

    # ── Active discussion routing ──────────────────────────────────────
    if user_id in _discussions:
        await msg.bot.send_chat_action(chat_id, "typing")
        reply = await _discuss_turn(user_id, _discussions[user_id]["topic"], text)
        await msg.answer(
            f"{_h(reply)}\n\n"
            f"<i>Continue talking or use /writeblog to publish • /enddiscuss to stop</i>",
            parse_mode=ParseMode.HTML,
        )
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
        await msg.answer(
            f"<i>{_h(reply)}</i>\n\n"
            "📡 <b>Fetching stats from all platforms…</b>",
            parse_mode=ParseMode.HTML,
        )
        try:
            text = await _fetch_comprehensive_stats()
            for chunk in _split_html_safe(text):
                await msg.answer(chunk, parse_mode=ParseMode.HTML,
                                 disable_web_page_preview=True)
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

    elif intent == "news":
        # Fetch live news from real APIs for the requested topic
        query = topic or "artificial intelligence technology geopolitics"
        await msg.answer(
            f"📡 <b>Fetching live news on: {_h(query)}</b>\n"
            f"<i>Pulling fresh articles from NewsData.io and news APIs…</i>",
            parse_mode=ParseMode.HTML,
        )
        asyncio.create_task(_fetch_and_send_news(msg, query))

    elif intent == "recommend":
        await msg.answer(
            "📡 <b>Fetching today's news and curating blog topics…</b>\n"
            "<i>This may take 10-20 seconds while I read the news for you.</i>",
            parse_mode=ParseMode.HTML,
        )
        try:
            from scheduler import scheduler
            suggestions = await scheduler.recommendation_push(manual=True)
            if not suggestions:
                await msg.answer(
                    "⚠️ No suggestions right now.\n"
                    "Try <code>/trending</code> for free trending topics instead.",
                    parse_mode=ParseMode.HTML,
                )
        except Exception as exc:
            logger.exception("recommend (agent) error")
            await msg.answer(
                f"❌ Recommend error: <code>{_h(str(exc)[:200])}</code>",
                parse_mode=ParseMode.HTML,
            )

    elif intent == "schedule":
        try:
            from scheduler import scheduler
            s = scheduler.status()
            await msg.answer(
                f"<i>{_h(reply)}</i>\n\n"
                f"📅 <b>Scheduler Status</b>\n"
                f"Running: {'🟢 Yes' if s['running'] else '🔴 No'}\n"
                f"Daily Target: <code>{s['daily_target']} blogs/day</code>\n"
                f"Next Run: <code>{_h(str(s['next_run'] or 'Not scheduled'))}</code>\n"
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


async def _fetch_and_send_news(msg: Message, query: str) -> None:
    """Fetch live news for a specific topic and format for Telegram."""
    try:
        from clients.news_client import NewsClient
        articles = await NewsClient().fetch_topic(query)

        if not articles:
            await msg.answer(
                f"⚠️ <b>No news found for:</b> <code>{_h(query)}</code>\n"
                "Try a different search term or use <code>/trending</code> for general tech trends.",
                parse_mode=ParseMode.HTML,
            )
            return

        lines = [f"📰 <b>Live News: {_h(query)}</b>\n"]
        for i, art in enumerate(articles[:8], 1):
            title = _h(art["title"])
            source = _h(art.get("source", ""))
            pub = art.get("published_at", "")[:10]   # YYYY-MM-DD
            url = art.get("url", "")
            desc = _h((art.get("description") or "")[:120])
            if desc:
                desc = f"\n   <i>{desc}…</i>"
            lines.append(
                f"{i}. <b><a href=\"{url}\">{title}</a></b>{desc}\n"
                f"   📡 {source}  •  🗓 {pub}\n"
            )

        lines.append(
            "\n💡 Like a topic? Use <code>/generate_force &lt;title&gt;</code> to write a blog about it!"
        )

        text = "\n".join(lines)
        # Telegram max is 4096 chars; split if needed
        if len(text) > 3800:
            text = text[:3800] + "\n…"

        await msg.answer(text, parse_mode=ParseMode.HTML,
                         disable_web_page_preview=True)

    except Exception as exc:
        logger.exception("_fetch_and_send_news error")
        await msg.answer(
            f"❌ News fetch failed: <code>{_h(str(exc)[:200])}</code>",
            parse_mode=ParseMode.HTML,
        )



async def _discuss_turn(
    user_id: int,
    topic: str,
    user_message: str,
) -> str:
    """
    Handle one turn of a discussion session.
    Appends to history and returns the AI reply.
    """
    session = _discussions.get(user_id)
    if session is None:
        return "Session not found. Use /discuss <topic> to start."

    history = session["history"]
    # Add user message to history
    history.append({"role": "user", "content": user_message})

    # Build messages list for Groq
    try:
        from groq import AsyncGroq
        from config import get_settings as _gs
        _s = _gs()
        client = AsyncGroq(api_key=_s.groq_api_key)

        messages = [
            {"role": "system", "content": _DISCUSS_SYSTEM + f"\n\nTOPIC: {topic}"},
        ]
        # Append history (keep last 20 turns to stay within context limits)
        messages.extend(history[-20:])

        resp = await client.chat.completions.create(
            model=_s.groq_model,
            messages=messages,
            temperature=0.75,
            max_tokens=512,
        )
        reply = (resp.choices[0].message.content or "").strip()
    except Exception as exc:
        logger.warning("[Discuss] Groq call failed: %s", exc)
        reply = "Sorry, I had trouble thinking of a response. Please try again!"

    # Add AI reply to history
    history.append({"role": "assistant", "content": reply})
    return reply


async def _run_generation_with_context(
    topic: str,
    niche: str,
    chat_id: int,
    discussion_context: str,
) -> None:
    """
    Like _run_generation but injects a discussion context string
    into the generation graph so the blog reflects the conversation.
    """
    try:
        from graph_system1 import run_generation_graph
        result = await run_generation_graph(
            topic,
            niche,
            chat_id=chat_id,
            force=True,
            discussion_context=discussion_context,
        )

        if not result.get("publish_success"):
            err = _h(result.get("error_message", "Unknown error"))
            await push_notification(
                f"❌ <b>Blog from discussion failed</b> for <code>{_h(topic)}</code>\n"
                f"<code>{err[:300]}</code>",
                chat_id,
            )
    except Exception as exc:
        logger.exception("Generation-with-context error")
        await push_notification(
            f"❌ <b>Blog from discussion crashed</b> for <code>{_h(topic)}</code>\n"
            f"<code>{_h(str(exc)[:300])}</code>",
            chat_id,
        )



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
        BotCommand(command="recommend",      description="Get AI-curated topic suggestions from news"),
        BotCommand(command="discuss",        description="Start an AI discussion: /discuss <topic>"),
        BotCommand(command="writeblog",      description="Convert current discussion into a blog"),
        BotCommand(command="enddiscuss",     description="End discussion without publishing"),
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
