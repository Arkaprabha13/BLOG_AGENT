"""
scheduler.py — Autonomous Daily Blog Scheduler (v2)

Runs two daily jobs as background asyncio tasks inside FastAPI lifespan:

  08:00  — News Recommendation Push
           · Fetches news from NewsClient (3 APIs) → NewsAgent curates 5-10 topics
           · Sends formatted suggestion list to Telegram admin
           · Each suggestion includes a /generate_force shortcut

  09:00  — Auto-Blog Batch
           · Uses NewsAgent topics first; falls back to TrendsClient (HN/Reddit)
           · Filters duplicates, generates DAILY_TARGET blogs via System 1
           · Sends Telegram summary when batch completes

Uses only stdlib asyncio — no extra scheduler dependency required.
"""

import asyncio
import logging
from datetime import datetime, timedelta

from config import get_settings

logger   = logging.getLogger(__name__)
settings = get_settings()

DAILY_TARGET       = 5   # number of blogs to auto-generate per day
RECOMMEND_HOUR     = 8   # 08:00 — send topic suggestions
GENERATE_HOUR      = 9   # 09:00 — auto-generate blogs
DUPLICATE_WORDS    = 2   # min overlapping words to consider a duplicate


class DailyScheduler:
    """Background scheduler that runs two daily jobs (08:00 recommend, 09:00 generate)."""

    def __init__(self):
        self._task: asyncio.Task | None = None
        self._running = False
        self._last_run: datetime | None = None
        self._last_count = 0
        self._next_run: datetime | None = None
        self._last_recommend_run: datetime | None = None
        self._next_recommend: datetime | None = None

    # ------------------------------------------------------------------ #
    # Public interface
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        """Start the background scheduler loop."""
        if self._task and not self._task.done():
            logger.warning("[Scheduler] Already running")
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="daily-scheduler")
        logger.info(
            "[Scheduler] Started — recommend@%02d:00, generate@%02d:00, %d blogs/day",
            RECOMMEND_HOUR, GENERATE_HOUR, DAILY_TARGET,
        )

    async def stop(self) -> None:
        """Gracefully stop the scheduler."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("[Scheduler] Stopped")

    def status(self) -> dict:
        """Return current scheduler status (for API endpoint)."""
        return {
            "running":              self._running,
            "last_run":             self._last_run.isoformat() if self._last_run else None,
            "last_count":           self._last_count,
            "next_run":             self._next_run.isoformat() if self._next_run else None,
            "next_recommend":       self._next_recommend.isoformat() if self._next_recommend else None,
            "last_recommend_run":   self._last_recommend_run.isoformat() if self._last_recommend_run else None,
            "daily_target":         DAILY_TARGET,
        }

    # ------------------------------------------------------------------ #
    # Internal loop — checks every minute which job is due
    # ------------------------------------------------------------------ #

    async def _loop(self) -> None:
        """Sleep-loop: wakes every minute to check if a job should run."""
        while self._running:
            try:
                now  = datetime.now()
                hour = now.hour

                # Update next-run display times
                self._next_recommend = self._calc_next(RECOMMEND_HOUR)
                self._next_run       = self._calc_next(GENERATE_HOUR)

                # 08:00 — recommendation push (run once per day)
                if hour == RECOMMEND_HOUR and now.minute == 0:
                    if (
                        self._last_recommend_run is None
                        or self._last_recommend_run.date() < now.date()
                    ):
                        logger.info("[Scheduler] Firing recommendation push")
                        asyncio.create_task(self.recommendation_push())

                # 09:00 — blog generation batch (run once per day)
                if hour == GENERATE_HOUR and now.minute == 0:
                    if (
                        self._last_run is None
                        or self._last_run.date() < now.date()
                    ):
                        logger.info("[Scheduler] Firing daily blog batch")
                        asyncio.create_task(self.daily_batch())

                await asyncio.sleep(60)   # check every minute

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("[Scheduler] Loop error: %s", exc)
                await asyncio.sleep(60)

    def _calc_next(self, hour: int) -> datetime:
        """Return next occurrence of ``hour:00`` (today or tomorrow)."""
        now    = datetime.now()
        target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        return target

    # ------------------------------------------------------------------ #
    # 08:00 — News Recommendation Push
    # ------------------------------------------------------------------ #

    async def recommendation_push(self, manual: bool = False) -> list:
        """
        Fetch news, curate suggestions, send to Telegram.
        Returns list of BlogSuggestion objects (for bot /recommend command).
        """
        logger.info("[Scheduler] %s recommendation push", "Manual" if manual else "Automatic")
        self._last_recommend_run = datetime.now()

        try:
            from agents.news_agent import NewsAgent
            agent = NewsAgent()
            suggestions = await agent.get_suggestions(count=8)
        except Exception as exc:
            logger.error("[Scheduler] NewsAgent failed: %s", exc)
            suggestions = []

        await self._notify_recommendations(suggestions, manual)
        return suggestions

    async def _notify_recommendations(self, suggestions: list, manual: bool) -> None:
        if not suggestions:
            try:
                from bot import push_notification
                await push_notification(
                    "📰 <b>Daily Topic Suggestions</b>\n\n"
                    "<i>No suggestions available right now — news APIs may be cooling down. "
                    "Try /trending for free trending topics.</i>",
                    chat_id=None,
                )
            except Exception as exc:
                logger.warning("[Scheduler] Empty recommend notify failed: %s", exc)
            return

        label = "🔧 Manual" if manual else "📰 Daily"
        lines = [f"{label} <b>Blog Topic Suggestions</b>\n"]
        lines.append(f"<i>Curated from today's news — {len(suggestions)} ideas ready:</i>\n")

        for i, s in enumerate(suggestions, 1):
            niche = s.niche.replace("-", " ").title()
            # Escape HTML-sensitive chars in title
            import html
            safe_title = html.escape(s.title)
            safe_reason = html.escape(s.reason[:120])
            safe_hook   = html.escape(s.hook[:100]) if s.hook else ""
            # Build safe generate command (strip non-alphanumeric for the slug shortcut)
            import re
            slug_hint = re.sub(r"[^\w\s-]", "", s.title)[:50].strip()
            lines.append(
                f"{i}. <b>{safe_title}</b>\n"
                f"   🏷 {niche}\n"
                f"   💡 {safe_reason}\n"
                + (f"   ✍️ <i>{safe_hook}</i>\n" if safe_hook else "")
                + f"   ▶️ <code>/generate_force {slug_hint} {s.niche}</code>\n"
            )

        lines.append("\n💬 <i>Just send a /generate_force command above to publish!</i>")

        try:
            from bot import push_notification
            msg = "\n".join(lines)
            # Split if too long for Telegram (4096 char limit)
            if len(msg) > 3800:
                chunks = [msg[i:i+3800] for i in range(0, len(msg), 3800)]
                for chunk in chunks:
                    await push_notification(chunk, chat_id=None)
            else:
                await push_notification(msg, chat_id=None)
        except Exception as exc:
            logger.warning("[Scheduler] Recommendation notify failed: %s", exc)

    # ------------------------------------------------------------------ #
    # 09:00 — Daily Blog Auto-Generation Batch
    # ------------------------------------------------------------------ #

    async def daily_batch(self, manual: bool = False) -> dict:
        """
        Fetch topics (NewsAgent → TrendsClient fallback), filter duplicates,
        and generate up to DAILY_TARGET blogs.
        Returns summary dict.
        """
        logger.info("[Scheduler] %s daily batch — target=%d",
                    "Manual" if manual else "Automatic", DAILY_TARGET)
        self._last_run = datetime.now()

        # --- Topic acquisition ---
        topics = await self._acquire_topics()

        from database               import find_similar_blogs
        from graph_system1          import run_generation_graph

        generated   = 0
        skipped_dup = 0
        errors      = 0

        for item in topics:
            if generated >= DAILY_TARGET:
                break

            topic = item["topic"]
            niche = item["niche"]

            similar = await find_similar_blogs(topic, threshold=DUPLICATE_WORDS)
            if similar:
                logger.info("[Scheduler] Skipping duplicate: %r", topic)
                skipped_dup += 1
                continue

            logger.info("[Scheduler] Generating [%d/%d]: %r  niche=%r",
                        generated + 1, DAILY_TARGET, topic, niche)
            try:
                result = await run_generation_graph(topic, niche, chat_id=None)
                if result.get("publish_success"):
                    generated += 1
                    logger.info("[Scheduler] Published: slug=%s", result.get("slug"))
                else:
                    errors += 1
                    logger.warning("[Scheduler] Failed: %s", result.get("error_message"))
            except Exception as exc:
                errors += 1
                logger.error("[Scheduler] Error generating %r: %s", topic, exc)

        self._last_count = generated
        summary = {
            "generated":          generated,
            "skipped_duplicates": skipped_dup,
            "errors":             errors,
            "topics_fetched":     len(topics),
        }
        logger.info("[Scheduler] Batch done: %s", summary)
        await self._notify_batch(summary, manual)
        return summary

    async def _acquire_topics(self) -> list[dict]:
        """Try NewsAgent first; fall back to TrendsClient."""
        # Attempt 1: NewsAgent (news APIs)
        try:
            from agents.news_agent import NewsAgent
            agent = NewsAgent()
            suggestions = await agent.get_suggestions(count=DAILY_TARGET * 3, filter_existing=False)
            if suggestions:
                logger.info("[Scheduler] Using %d NewsAgent topics", len(suggestions))
                return [{"topic": s.title, "niche": s.niche} for s in suggestions]
        except Exception as exc:
            logger.warning("[Scheduler] NewsAgent acquire failed: %s", exc)

        # Fallback: free sources
        try:
            from clients.trends_client import TrendsClient
            topics = await TrendsClient().fetch_trending(limit=DAILY_TARGET * 3)
            logger.info("[Scheduler] Fallback TrendsClient: %d topics", len(topics))
            return topics
        except Exception as exc:
            logger.error("[Scheduler] TrendsClient fallback failed: %s", exc)
            return []

    async def _notify_batch(self, summary: dict, manual: bool) -> None:
        try:
            from bot import push_notification
            label = "🔧 Manual" if manual else "📅 Daily"
            g, s, e = summary["generated"], summary["skipped_duplicates"], summary["errors"]
            msg = (
                f"{label} <b>Auto-Blog Batch Complete!</b>\n\n"
                f"✅ Published:  <code>{g}</code> new blogs\n"
                f"♻️ Skipped:   <code>{s}</code> duplicates\n"
                f"❌ Errors:    <code>{e}</code>\n\n"
                f"🌐 View them at <a href=\"{settings.base_url}\">{settings.app_name}</a>"
            )
            await push_notification(msg, chat_id=None)
        except Exception as exc:
            logger.warning("[Scheduler] Batch notify failed: %s", exc)


# Singleton instance used by main.py and bot.py
scheduler = DailyScheduler()
