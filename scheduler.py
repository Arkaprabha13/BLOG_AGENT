"""
scheduler.py — Autonomous Daily Blog Scheduler

Runs as a background asyncio task inside FastAPI lifespan.
Every day at 09:00 (local server time), it:
  1. Fetches trending topics from free public sources (HN, Reddit, GitHub)
  2. Filters out topics already covered in the DB (duplicate check)
  3. Generates up to DAILY_TARGET new blog posts via System 1
  4. Sends a Telegram summary when the batch is done

Uses only stdlib asyncio — no extra scheduler dependency required.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

DAILY_TARGET    = 5     # number of blogs to auto-generate per day
RUN_HOUR_LOCAL  = 9     # 09:00 local server time
DUPLICATE_WORDS = 2     # min overlapping words to consider topic a duplicate


class DailyScheduler:
    """Background scheduler that auto-publishes DAILY_TARGET blogs per day."""

    def __init__(self):
        self._task: asyncio.Task | None = None
        self._running = False
        self._last_run: datetime | None = None
        self._last_count = 0
        self._next_run: datetime | None = None

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
        logger.info("[Scheduler] Started — will post %d blogs/day at %02d:00",
                    DAILY_TARGET, RUN_HOUR_LOCAL)

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
            "running":    self._running,
            "last_run":   self._last_run.isoformat() if self._last_run else None,
            "last_count": self._last_count,
            "next_run":   self._next_run.isoformat() if self._next_run else None,
            "daily_target": DAILY_TARGET,
        }

    # ------------------------------------------------------------------ #
    # Internal loop
    # ------------------------------------------------------------------ #

    async def _loop(self) -> None:
        """Sleep until next 09:00, run batch, repeat."""
        while self._running:
            try:
                self._next_run = self._calc_next_run()
                delay = (self._next_run - datetime.now()).total_seconds()
                logger.info("[Scheduler] Next batch in %.0f minutes at %s",
                            delay / 60, self._next_run.strftime("%H:%M"))
                await asyncio.sleep(max(delay, 0))

                if self._running:
                    await self.daily_batch()

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("[Scheduler] Loop error: %s", exc)
                await asyncio.sleep(60)   # back-off 1 min on unexpected error

    def _calc_next_run(self) -> datetime:
        """Return the next 09:00 datetime (today if before 09:00, tomorrow otherwise)."""
        now  = datetime.now()
        target = now.replace(hour=RUN_HOUR_LOCAL, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        return target

    # ------------------------------------------------------------------ #
    # Daily batch
    # ------------------------------------------------------------------ #

    async def daily_batch(self, manual: bool = False) -> dict:
        """
        Fetch trending topics, filter duplicates, and generate up to DAILY_TARGET blogs.
        Returns summary dict: {"generated": int, "skipped_duplicates": int, "errors": int}
        """
        logger.info("[Scheduler] %s daily batch — target=%d",
                    "Manual" if manual else "Automatic", DAILY_TARGET)
        self._last_run = datetime.now()

        from clients.trends_client import TrendsClient
        from database               import find_similar_blogs
        from graph_system1          import run_generation_graph

        trends  = TrendsClient()
        topics  = await trends.fetch_trending(limit=DAILY_TARGET * 3)

        generated   = 0
        skipped_dup = 0
        errors      = 0

        for item in topics:
            if generated >= DAILY_TARGET:
                break

            topic = item["topic"]
            niche = item["niche"]

            # Duplicate check —skip if already written about this
            similar = await find_similar_blogs(topic, threshold=DUPLICATE_WORDS)
            if similar:
                logger.info("[Scheduler] Skipping duplicate: %r (matches %r)",
                            topic, similar[0]["title"])
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
            "generated":           generated,
            "skipped_duplicates":  skipped_dup,
            "errors":              errors,
            "topics_fetched":      len(topics),
        }
        logger.info("[Scheduler] Batch done: %s", summary)

        # Telegram notification
        await self._notify(summary, manual)
        return summary

    async def _notify(self, summary: dict, manual: bool) -> None:
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
            logger.warning("[Scheduler] Telegram notify failed: %s", exc)


# Singleton instance used by main.py and bot.py
scheduler = DailyScheduler()
