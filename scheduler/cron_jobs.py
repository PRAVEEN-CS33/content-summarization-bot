"""
scheduler/cron_jobs.py — APScheduler jobs that drive the automation.

Jobs:
  1. fetch_job      — every N minutes: fetch RSS feeds, detect new items
  2. process_job    — every N minutes + 5: process pending → summarize
  3. push_job       — daily at configured hour: push unsent summaries
"""
import logging
import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from rss_manager.feed_monitor import run_feed_monitor
from processing.pipeline import run_processing_pipeline
import config

logger = logging.getLogger(__name__)

# Global reference so conversation_handler can reschedule jobs at runtime
_scheduler_ref = None


def _run_sync(fn, *args, **kwargs):
    """Run a synchronous function safely (APScheduler calls sync fns directly)."""
    try:
        fn(*args, **kwargs)
    except Exception as e:
        logger.exception("Scheduled job error in %s: %s", fn.__name__, e)


def build_scheduler(app) -> AsyncIOScheduler:
    """
    Build and configure the scheduler.
    `app` is the python-telegram-bot Application instance (for push_job).
    """
    global _scheduler_ref
    scheduler = AsyncIOScheduler(timezone="Asia/Kolkata")
    _scheduler_ref = scheduler

    # Job 1: Fetch feeds every N minutes
    scheduler.add_job(
        func=lambda: _run_sync(run_feed_monitor),
        trigger=IntervalTrigger(minutes=config.FETCH_INTERVAL_MINUTES),
        id="fetch_feeds",
        name="RSS Feed Fetcher",
        replace_existing=True,
        max_instances=1,           # prevent overlapping runs
        misfire_grace_time=120,
    )

    # Job 2: Process pending items and immediately send new summaries
    async def process_and_push():
        from bot.handlers import send_unsent_summaries
        loop = asyncio.get_event_loop()
        stats = await loop.run_in_executor(None, run_processing_pipeline)
        if stats.get("success", 0) > 0:
            try:
                await send_unsent_summaries(app)
            except Exception as e:
                logger.exception("Immediate push failed: %s", e)

    scheduler.add_job(
        func=process_and_push,
        trigger=IntervalTrigger(minutes=config.FETCH_INTERVAL_MINUTES, start_date=None),
        id="process_items",
        name="Content Processor",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=300,
    )

    # Job 3: Daily push of all unsent summaries at configured hour
    async def daily_push():
        from bot.handlers import send_unsent_summaries
        try:
            await send_unsent_summaries(app)
        except Exception as e:
            logger.exception("Daily push failed: %s", e)

    scheduler.add_job(
        func=daily_push,
        trigger=CronTrigger(hour=config.SUMMARY_HOUR, minute=0),
        id="daily_digest",
        name="Daily Digest Pusher",
        replace_existing=True,
        max_instances=1,
    )

    logger.info(
        "Scheduler configured: fetch every %dm, daily digest at %d:00 IST",
        config.FETCH_INTERVAL_MINUTES, config.SUMMARY_HOUR,
    )
    return scheduler