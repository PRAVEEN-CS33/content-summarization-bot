"""
main.py — Entry point. Boots DB, scheduler, and Telegram bot together.
"""
import asyncio
import logging
import os
import signal
import sys

from utils.logger import setup_logging
setup_logging("INFO")

logger = logging.getLogger(__name__)

# ── PID lock: prevent two bot instances from fighting over the Telegram token ──
# Telegram only allows one getUpdates polling session per token. A second
# instance causes: "Conflict: terminated by other getUpdates request".
_PID_FILE = os.path.join(os.path.dirname(__file__), "data", "bot.pid")

def _acquire_pid_lock():
    """Write our PID to a lock file. Exit immediately if another instance is running."""
    os.makedirs(os.path.dirname(_PID_FILE), exist_ok=True)
    if os.path.exists(_PID_FILE):
        try:
            old_pid = int(open(_PID_FILE).read().strip())
            # Check if that process is actually alive
            os.kill(old_pid, 0)
            # It's alive — refuse to start
            print(
                f"\n❌ Another bot instance is already running (PID {old_pid}).\n"
                f"   Kill it first:  kill {old_pid}\n"
                f"   Or force-clear: rm {_PID_FILE}\n",
                file=sys.stderr,
            )
            sys.exit(1)
        except (ValueError, ProcessLookupError):
            # Stale PID file — previous run crashed without cleanup
            logger.warning("Stale PID file found (process gone). Removing and continuing.")

    with open(_PID_FILE, "w") as f:
        f.write(str(os.getpid()))

def _release_pid_lock():
    """Remove the PID file on clean shutdown."""
    try:
        os.remove(_PID_FILE)
    except FileNotFoundError:
        pass


def check_prerequisites():
    """Validate config before starting."""
    import config
    errors = []
    if not config.TELEGRAM_BOT_TOKEN:
        errors.append("TELEGRAM_BOT_TOKEN not set in .env")
    if not config.TELEGRAM_CHAT_ID:
        logger.warning("TELEGRAM_CHAT_ID not set — bot will respond to anyone")

    from summarizer.gemini_summarizer import check_gemini_health
    if not check_gemini_health():
        logger.warning(
            "⚠️  Gemini API key invalid or unreachable.\n"
            "   Check GEMINI_API_KEY in your .env file.\n"
            "   Get a key at: https://aistudio.google.com/app/apikey"
        )

    if errors:
        for e in errors:
            logger.error("Config error: %s", e)
        sys.exit(1)


async def main():
    _acquire_pid_lock()
    check_prerequisites()

    # Initialise database
    from database.db import init_db
    init_db()

    # Build Telegram bot
    from bot.handlers import build_application
    app = build_application()

    # Build + start scheduler
    from scheduler.cron_jobs import build_scheduler
    scheduler = build_scheduler(app)
    scheduler.start()
    logger.info("✅ Scheduler started")

    # Run an immediate first fetch on startup
    from rss_manager.feed_monitor import run_feed_monitor
    logger.info("Running initial feed fetch...")
    run_feed_monitor()

    # Start Telegram bot (polling)
    logger.info("🤖 Starting Telegram bot (polling mode)...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    logger.info("✅ Bot is running. Press Ctrl+C to stop.")

    # Keep alive
    stop_event = asyncio.Event()

    def _shutdown(sig, frame):
        logger.info("Shutdown signal received (%s)", sig)
        stop_event.set()

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    await stop_event.wait()

    # Graceful shutdown
    logger.info("Shutting down...")
    scheduler.shutdown(wait=False)
    await app.updater.stop()
    await app.stop()
    await app.shutdown()
    _release_pid_lock()
    logger.info("Goodbye.")


if __name__ == "__main__":
    asyncio.run(main())
