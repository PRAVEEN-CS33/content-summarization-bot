"""
bot/conversation_handler.py — Multi-turn conversational bot handler.

FIXES:
  1. After adding any source, immediately fetch + summarize it
  2. Google Alerts: use RSS description directly (no article fetch needed)
  3. Full pipeline (fetch → process → send) triggered on add
"""
import logging
import asyncio
import html
from telegram import Update
from telegram.ext import ContextTypes

from database import db
from bot.intent_parser import parse_intent
from bot.conversation_memory import add_message, get_message_dicts, clear_history
from bot.formatter import (
    format_source_list, format_daily_digest_header,
    format_summary_message, format_on_demand_summary,
)
from discovery.youtube import resolve_youtube_channel
from discovery.podcast import resolve_podcast
from discovery.google_alerts import resolve_google_alert
from processing.on_demand import process_on_demand_async, detect_url_type
from rss_manager.feed_monitor import fetch_and_queue_source
from processing.pipeline import run_processing_pipeline
from summarizer.gemini_summarizer import check_gemini_health, summarize
import config
import feedparser

logger = logging.getLogger(__name__)


async def _reply(update: Update, text: str) -> str:
    try:
        await update.message.reply_text(text, disable_web_page_preview=True, parse_mode="HTML")
    except Exception as e:
        logger.error("Reply failed: %s", e)
    return text


async def _reply_and_remember(update, chat_id, user_text, bot_text):
    add_message(chat_id, "user",      user_text)
    add_message(chat_id, "assistant", bot_text)
    await _reply(update, bot_text)


# ── Immediate fetch + summarize after adding a source ─────────────────────────

async def _fetch_and_summarize_now(update: Update, source_id: int, source_name: str, source_type: str) -> str:
    """
    After adding a source, immediately:
      1. Fetch its RSS feed
      2. Queue new items
      3. Run processing pipeline
      4. Send summaries to user right now
    """
    # Get the source record
    sources = db.get_sources()
    source  = next((s for s in sources if s["id"] == source_id), None)
    if not source:
        return "Added but couldn't fetch immediately. Summaries will arrive at your next digest."

    # Step 1: Fetch RSS in thread pool (non-blocking)
    loop  = asyncio.get_event_loop()
    stats = await loop.run_in_executor(None, fetch_and_queue_source, source)

    if stats["errors"] > 0:
        return f"Added {source_name} but had trouble fetching the feed. Will retry automatically."

    if stats["new"] == 0:
        return f"Added {source_name}! No new items in the feed right now — I'll check again in an hour."

    await update.message.reply_text(f"Found {stats['new']} items. Summarizing now...", parse_mode="HTML")

    # Step 2: Process pending items (in thread pool)
    proc_stats = await loop.run_in_executor(None, run_processing_pipeline)

    # Step 3: Send the summaries immediately
    summaries = db.get_unsent_summaries()
    # Filter to only this source
    source_summaries = [s for s in summaries if s["source_id"] == source_id]

    if not source_summaries:
        return f"Added {source_name}! Summaries will arrive in your next digest."

    await update.message.reply_text(
        f"Here are the latest from {html.escape(source_name)} ({len(source_summaries)} items):",
        parse_mode="HTML"
    )
    for s in source_summaries:
        msg = format_summary_message(s)
        await update.message.reply_text(msg, disable_web_page_preview=True, parse_mode="HTML")
        db.mark_summary_sent(s["id"])

    return ""


# ── Action Executors ──────────────────────────────────────────────────────────

async def _do_add_source(update: Update, intent: dict) -> str:
    source_type = intent.get("source_type", "topic")
    query       = intent.get("query", "").strip()

    if not query:
        return "What would you like me to add? Share a name or URL."

    await update.message.reply_text(f"Looking up '{html.escape(query)}'...", parse_mode="HTML")

    if source_type == "youtube":
        result = resolve_youtube_channel(query)
        if not result:
            return f"Couldn't find YouTube channel '{query}'. Try the @handle or full URL."
        _, name, rss_url = result
        source_id = db.add_source("youtube", name, rss_url, {})
        if not source_id:
            return f"Already subscribed to {name}."
        await update.message.reply_text(f"Subscribed to {html.escape(name)}! Fetching latest videos...", parse_mode="HTML")
        return await _fetch_and_summarize_now(update, source_id, name, "youtube")

    elif source_type == "podcast":
        result = resolve_podcast(query)
        if not result:
            return f"Couldn't find podcast '{query}'. Try the exact show name or RSS URL."
        _, name, rss_url = result
        source_id = db.add_source("podcast", name, rss_url, {})
        if not source_id:
            return f"Already subscribed to {name}."
        await update.message.reply_text(f"Subscribed to {html.escape(name)}! Fetching latest episodes...", parse_mode="HTML")
        return await _fetch_and_summarize_now(update, source_id, name, "podcast")

    else:
        # Google Alert / news topic
        _, name, rss_url = resolve_google_alert(query)
        source_id = db.add_source("google_alert", name, rss_url, {"query": query})
        if not source_id:
            return f"Already tracking '{name}'."
        await update.message.reply_text(f"Added alert: {html.escape(name)}! Fetching latest news...", parse_mode="HTML")
        return await _fetch_and_summarize_now(update, source_id, name, "google_alert")


async def _do_remove_source(update: Update, intent: dict) -> str:
    query   = intent.get("query", "").strip()
    sources = db.get_sources()

    if not query:
        return "Which source should I remove? Say its name or 'list' to see all."

    if query.isdigit():
        db.remove_source(int(query))
        return f"Removed source #{query}."

    matches = [s for s in sources if query.lower() in s["name"].lower()]
    if len(matches) == 1:
        db.remove_source(matches[0]["id"])
        return f"Removed: {matches[0]['name']} ✓"
    elif len(matches) > 1:
        names = "\n".join(f"  {s['id']}. {html.escape(s['name'])}" for s in matches)
        return f"Found multiple matches:\n{names}\n\nWhich ID should I remove?"
    else:
        return f"No source found matching '{query}'. Say 'list' to see what I'm tracking."


async def _do_list_sources(update: Update) -> str:
    sources = db.get_sources()
    if not sources:
        return "You haven't added any sources yet.\n\nTry: 'add YouTube @mkbhd' or 'track AI startup news'"
    return format_source_list(sources)


async def _do_get_summary(update: Update, intent: dict) -> str:
    period    = intent.get("period", "unsent")
    summaries = db.get_today_summaries() if period == "today" else db.get_unsent_summaries()

    if not summaries:
        return "No new summaries yet. Say 'fetch now' to check for new content."

    await update.message.reply_text(format_daily_digest_header(len(summaries)), parse_mode="HTML")
    for s in summaries:
        msg = format_summary_message(s)
        await update.message.reply_text(msg, disable_web_page_preview=True, parse_mode="HTML")
        db.mark_summary_sent(s["id"])
    return ""


async def _do_summarize_url(update: Update, intent: dict) -> str:
    url = intent.get("url", "").strip()
    if not url:
        return "Please share the URL you'd like me to summarize."

    url_type   = detect_url_type(url)
    status_msg = await update.message.reply_text(
        "Downloading and transcribing... ☕ (1-3 min)" if url_type in ("youtube", "audio")
        else "Extracting and summarizing...",
        parse_mode="HTML"
    )

    title, summary, content_type = await process_on_demand_async(url, status_msg=status_msg)

    if not summary:
        await status_msg.edit_text("Couldn't extract content from that URL. Is it public?", parse_mode="HTML")
        return "Failed to summarize that URL."

    await status_msg.delete()
    formatted = format_on_demand_summary(title, summary, url, content_type)
    await update.message.reply_text(formatted, disable_web_page_preview=True, parse_mode="HTML")

    # ── Auto-subscribe logic ──────────────────────────────────────────────────
    try:
        if url_type == "youtube":
            res = resolve_youtube_channel(url)
            if res:
                _, name, rss_url = res
                source_id = db.add_source("youtube", name, rss_url, {})
                if source_id:
                    await update.message.reply_text(
                        f"✨ <b>Subscribed!</b> I've added <i>{html.escape(name)}</i> to your list. You'll get its future videos in your daily digest.",
                        parse_mode="HTML"
                    )
        elif url_type == "audio":
            res = resolve_podcast(url)
            if res:
                _, name, rss_url = res
                source_id = db.add_source("podcast", name, rss_url, {})
                if source_id:
                    await update.message.reply_text(
                        f"✨ <b>Subscribed!</b> I've added <i>{html.escape(name)}</i> to your list. You'll get its future episodes in your daily digest.",
                        parse_mode="HTML"
                    )
    except Exception as e:
        logger.error("Auto-subscribe failed for %s: %s", url, e)

    return ""


async def _do_set_schedule(update: Update, intent: dict) -> str:
    hour = intent.get("hour")
    if hour is None or not (0 <= int(hour) <= 23):
        return "What time should I send your daily digest? (e.g. '8am', '9pm')"
    hour = int(hour)
    config.SUMMARY_HOUR = hour
    try:
        from apscheduler.triggers.cron import CronTrigger
        from scheduler.cron_jobs import _scheduler_ref
        if _scheduler_ref:
            _scheduler_ref.reschedule_job("daily_digest", trigger=CronTrigger(hour=hour, minute=0))
    except Exception as e:
        logger.warning("Reschedule failed: %s", e)
    h12 = hour % 12 or 12
    ampm = "AM" if hour < 12 else "PM"
    return f"Done! Daily digest will arrive at {h12} {ampm} every day 🕐"


async def _do_trigger_fetch(update: Update) -> str:
    msg = await update.message.reply_text("Checking all feeds for new content...", parse_mode="HTML")
    loop = asyncio.get_event_loop()
    from rss_manager.feed_monitor import run_feed_monitor
    feed_stats = await loop.run_in_executor(None, run_feed_monitor)
    if feed_stats["new"] == 0:
        await msg.edit_text("All caught up — no new content since last check.", parse_mode="HTML")
        return "No new content."
    await msg.edit_text(f"Found {feed_stats['new']} new items! Summarizing now...", parse_mode="HTML")
    proc_stats = await loop.run_in_executor(None, run_processing_pipeline)
    result = f"Done! {proc_stats['success']} summaries ready.\nSay 'show summaries' to read them."
    await msg.edit_text(result, parse_mode="HTML")
    return result


async def _do_status(update: Update) -> str:
    api_ok  = check_gemini_health()
    sources = db.get_sources()
    pending = len(db.get_pending_items(limit=1000))
    h = config.SUMMARY_HOUR
    h12 = h % 12 or 12
    ampm = "AM" if h < 12 else "PM"
    return (
        f"{'✅' if api_ok else '❌'} Gemini API\n"
        f"📡 {len(sources)} sources tracked\n"
        f"⏳ {pending} items pending\n"
        f"🕐 Daily digest at {h12} {ampm}"
    )


# ── Main Conversation Router ──────────────────────────────────────────────────

async def handle_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    chat_id   = str(update.effective_chat.id)
    user_text = update.message.text.strip()

    if config.TELEGRAM_CHAT_ID and chat_id != str(config.TELEGRAM_CHAT_ID):
        return

    history    = get_message_dicts(chat_id)
    intent     = parse_intent(user_text, history=history)
    itype      = intent.get("type", "chat")
    action     = intent.get("action")
    bot_reply  = intent.get("reply", "")

    logger.info("[%s] '%s' → %s/%s", chat_id, user_text[:60], itype, action)

    try:
        if itype in ("clarify", "chat"):
            await _reply_and_remember(update, chat_id, user_text, bot_reply)
            return

        if bot_reply:
            await update.message.reply_text(bot_reply, parse_mode="HTML")

        result_text = ""

        if action == "add_source":
            result_text = await _do_add_source(update, intent)
        elif action == "remove_source":
            result_text = await _do_remove_source(update, intent)
        elif action == "list_sources":
            result_text = await _do_list_sources(update)
            await update.message.reply_text(result_text, disable_web_page_preview=True, parse_mode="HTML")
            result_text = ""
        elif action == "get_summary":
            result_text = await _do_get_summary(update, intent)
        elif action == "summarize_url":
            result_text = await _do_summarize_url(update, intent)
        elif action == "set_schedule":
            result_text = await _do_set_schedule(update, intent)
        elif action == "trigger_fetch":
            result_text = await _do_trigger_fetch(update)
        elif action == "status":
            result_text = await _do_status(update)
            await update.message.reply_text(result_text, parse_mode="HTML")
            result_text = ""

        if result_text:
            await update.message.reply_text(result_text, disable_web_page_preview=True, parse_mode="HTML")

        full_bot = " | ".join(filter(None, [bot_reply, result_text]))
        add_message(chat_id, "user",      user_text)
        add_message(chat_id, "assistant", full_bot or "Done.")

    except Exception as e:
        logger.exception("Handler error: %s", e)
        err = "Something went wrong. Try again in a moment."
        await _reply_and_remember(update, chat_id, user_text, err)