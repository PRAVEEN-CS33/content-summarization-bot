"""
bot/handlers.py — All Telegram command + message handlers.

NEW features:
  - URL message handler: paste any YouTube/podcast/article URL → instant summary
  - Google Alerts: shows setup guide + feed preview on /add topic
"""
import logging
import re
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, ContextTypes, MessageHandler, filters
)
from telegram.constants import ParseMode

import feedparser
from database import db
from discovery.youtube import resolve_youtube_channel
from discovery.podcast import resolve_podcast
from discovery.spotify import resolve_spotify, is_spotify_url
from discovery.google_alerts import resolve_google_alert, GOOGLE_ALERT_SETUP_GUIDE, preview_alert_feed
from processing.pipeline import run_processing_pipeline
from bot.conversation_handler import handle_conversation
from bot.conversation_memory import clear_history
from rss_manager.feed_monitor import run_feed_monitor
from summarizer.gemini_summarizer import check_gemini_health
from bot.formatter import (
    format_summary_message, format_source_list,
    format_daily_digest_header, format_help_message,
    format_status_message, format_on_demand_summary,
)
import config

logger = logging.getLogger(__name__)

URL_REGEX = re.compile(r"https?://[^\s]+")


import html

# ── Auth guard ────────────────────────────────────────────────────────────────

def _is_authorised(update: Update) -> bool:
    if not config.TELEGRAM_CHAT_ID:
        return True
    return str(update.effective_chat.id) == str(config.TELEGRAM_CHAT_ID)


async def _send(update: Update, text: str, parse_mode="HTML"):
    try:
        await update.message.reply_text(
            text, parse_mode=parse_mode, disable_web_page_preview=True
        )
    except Exception as e:
        logger.warning("Send failed (%s) — retrying plain text", e)
        try:
            await update.message.reply_text(text, parse_mode=None)
        except Exception as e2:
            logger.error("Plain text send also failed: %s", e2)


# ── /start  /help ─────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorised(update): return
    clear_history(str(update.effective_chat.id))
    await _send(update, format_help_message())


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorised(update): return
    await _send(update, format_help_message())


# ── /add ──────────────────────────────────────────────────────────────────────

async def add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorised(update): return

    args = context.args
    if len(args) < 2:
        await _send(update,
            "Usage:\n"
            "/add youtube <channel name, @handle, video URL, or shorts URL>\n"
            "/add podcast <name or RSS URL>\n"
            "/add spotify <Spotify show/podcast/audiobook URL or name>\n"
            "/add topic   <keyword or Google Alerts RSS URL>"
        )
        return

    sub_cmd = args[0].lower()
    query   = " ".join(args[1:])

    await update.message.reply_text(f"Resolving '{html.escape(query)}'...", parse_mode="HTML")

    if sub_cmd == "youtube":
        result = resolve_youtube_channel(query)
        if not result:
            await _send(update, f"Could not resolve YouTube channel for: {query}")
            return
        channel_id, name, rss_url = result
        source_id = db.add_source("youtube", name, rss_url, {"channel_id": channel_id})
        if source_id:
            await _send(update,
                f"✅ Added YouTube: *{name}*\n"
                f"RSS: {rss_url}\n"
                f"_Shorts are automatically skipped. New videos will be summarized on next schedule._"
            )
        else:
            await _send(update, f"Already subscribed to: {name}")

    elif sub_cmd == "podcast":
        result = resolve_podcast(query)
        if not result:
            await _send(update, f"Could not find podcast: {query}")
            return
        pid, name, rss_url = result
        source_id = db.add_source("podcast", name, rss_url, {"podcast_id": pid})
        if source_id:
            await _send(update, f"Added podcast: {name}\nRSS: {rss_url}")
        else:
            await _send(update, f"Already subscribed to: {name}")

    elif sub_cmd == "topic":
        pid, name, rss_url = resolve_google_alert(query)
        source_id = db.add_source("google_alert", name, rss_url, {"query": query})

        if not source_id:
            await _send(update, f"Already tracking: {name}")
            return

        await _send(update, f"Added alert: {name}\nFetching and summarizing latest articles...")

        try:
            import re
            import urllib.parse
            import requests as req
            from summarizer.gemini_summarizer import summarize

            # Fetch raw XML with requests (handles Google redirects)
            resp    = req.get(rss_url, timeout=30, headers={
                "User-Agent": "Mozilla/5.0 (compatible; NaradaAI/1.0)",
                "Accept": "application/atom+xml,application/xml,*/*",
            }, allow_redirects=True)
            feed    = feedparser.parse(resp.content)
            entries = feed.entries[:5]

            logger.info("Google Alert feed '%s': %d entries", name, len(entries))

            if not entries:
                await _send(update, "Feed added! No articles in feed yet — I'll summarize when new ones arrive.")
                return

            await _send(update, f"Found {len(entries)} articles. Summarizing with Gemini...")

            def _unwrap(url):
                if "google.com/url" in url:
                    params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
                    return params.get("url", [url])[0]
                return url

            def _get_content(entry):
                # Google Alerts uses <content> tag not <summary>
                content_list = getattr(entry, "content", [])
                if content_list:
                    text = content_list[0].get("value", "")
                else:
                    text = getattr(entry, "summary", "") or getattr(entry, "description", "")
                # Strip HTML tags
                text = re.sub(r"<[^>]+>", " ", text)
                return re.sub(r"\s+", " ", text).strip()

            from datetime import datetime, timezone

            for entry in entries:
                title   = getattr(entry, "title", "Untitled")
                # Strip HTML bold tags from title
                title   = re.sub(r"<[^>]+>", "", title).strip()
                raw_link = getattr(entry, "link", "")
                link    = _unwrap(raw_link)
                content = _get_content(entry)

                if not content or len(content) < 20:
                    content = title  # at minimum use title

                summary_text = summarize(
                    content=content,
                    title=title,
                    source_name=name,
                    source_type="google_alert",
                )

                if summary_text:
                    # Replace [link] placeholder with actual URL, then prepend source header
                    body = summary_text.replace("[link]", html.escape(link))
                    msg = f"🔔 <b>[Alert]</b> {html.escape(name)}\n\n{body}"
                    await _send(update, msg)

                    # Save to DB
                    entry_id = getattr(entry, "id", link)
                    pub      = getattr(entry, "published_parsed", None)
                    pub_str  = datetime(*pub[:6], tzinfo=timezone.utc).isoformat() if pub else datetime.now(timezone.utc).isoformat()
                    item_id  = db.add_item(source_id, entry_id, title, link, pub_str, content)
                    if item_id:
                        db.save_summary(item_id, source_id, title, summary_text, "gpt-4o-mini")
                        db.update_item_status(item_id, "done")

        except Exception as e:
            logger.exception("Alert summarization failed: %s", e)
            await _send(update, f"Feed added but summarization failed: {str(e)[:200]}")
    elif sub_cmd == "spotify":
        await update.message.reply_text(f"Resolving Spotify source: {html.escape(query[:60])}...", parse_mode="HTML")
        result = resolve_spotify(query)
        if not result:
            await _send(update,
                f"❌ Could not resolve Spotify RSS for: {query}\n\n"
                f"Tip: Try searching by podcast name instead of URL, or add the RSS feed directly with /add podcast <rss_url>"
            )
            return
        sid, name, rss_url = result
        source_id = db.add_source("podcast", name, rss_url, {"spotify_query": query})
        if source_id:
            await _send(update,
                f"✅ Added Spotify podcast: *{name}*\n"
                f"RSS: {rss_url}\n"
                f"_New episodes will be summarized on each scheduled run._"
            )
        else:
            await _send(update, f"Already subscribed to: {name}")

    else:
        await _send(update, "Unknown type. Use: youtube, podcast, spotify, or topic")


# ── /list ─────────────────────────────────────────────────────────────────────

async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorised(update): return
    sources = db.get_sources()
    await _send(update, format_source_list(sources))


# ── /remove ───────────────────────────────────────────────────────────────────

async def remove_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorised(update): return
    if not context.args:
        await _send(update, "Usage: /remove <source id>  (get IDs from /list)")
        return
    try:
        source_id = int(context.args[0])
        db.remove_source(source_id)
        await _send(update, f"Source #{source_id} removed.")
    except ValueError:
        await _send(update, "Invalid ID — must be a number.")


# ── /summarize (manual trigger) ───────────────────────────────────────────────

async def summarize_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorised(update): return
    msg = await update.message.reply_text("Fetching feeds...", parse_mode="HTML")
    feed_stats = run_feed_monitor()
    await msg.edit_text(
        f"Fetched <b>{feed_stats['new']}</b> new items. Processing...",
        parse_mode="HTML"
    )
    proc_stats = run_processing_pipeline()
    await msg.edit_text(
        f"✅ <b>Done!</b>\n"
        f"New items: {feed_stats['new']}\n"
        f"Summarised: {proc_stats['success']}\n"
        f"Failed: {proc_stats['failed']}\n\n"
        f"Use /summary today to read them.",
        parse_mode="HTML"
    )


# ── /summary today ────────────────────────────────────────────────────────────

async def summary_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorised(update): return
    args = context.args or []
    if args and args[0].lower() == "today":
        summaries = db.get_today_summaries()
    else:
        summaries = db.get_unsent_summaries()

    if not summaries:
        await _send(update, "No summaries available yet. Try /summarize first.")
        return

    await _send(update, format_daily_digest_header(len(summaries)))
    for s in summaries:
        await _send(update, format_summary_message(s))
        db.mark_summary_sent(s["id"])


# ── /alerts  (dedicated Google Alerts setup guide) ────────────────────────────

async def alerts_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorised(update): return
    await _send(update, GOOGLE_ALERT_SETUP_GUIDE)


# ── /status ───────────────────────────────────────────────────────────────────

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorised(update): return
    api_ok   = check_gemini_health()
    pending  = len(db.get_pending_items(limit=1000))
    sources  = db.get_sources()
    await _send(update, format_status_message(api_ok, pending, len(sources)))


# ── URL message handler (THE NEW FEATURE) ─────────────────────────────────────

async def handle_url_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    When user sends a plain message containing a URL:
    - Detect type (YouTube / audio / article)
    - Transcribe or extract content
    - Summarize with Gemini
    - Reply with formatted summary

    This works for ANY URL — no need to add it as a source first.
    """
    if not _is_authorised(update): return

    text = update.message.text or ""
    url_match = URL_REGEX.search(text)
    if not url_match:
        return  # not a URL message — ignore

    url      = url_match.group(0).rstrip(".,)>")
    url_type = detect_url_type(url)

    type_labels = {
        "youtube": "YouTube video",
        "audio":   "audio/podcast",
        "article": "article",
    }
    label = type_labels.get(url_type, "content")

    # Tell user we're working on it
    if url_type in ("youtube", "audio"):
        status_msg = await update.message.reply_text(
            f"Detected {html.escape(label)}. Downloading and transcribing... this may take 1-3 minutes.",
            parse_mode="HTML"
        )
    else:
        status_msg = await update.message.reply_text(
            f"Detected {html.escape(label)}. Extracting and summarizing...",
            parse_mode="HTML"
        )

    try:
        # Pass status_msg so the keep-alive pinger can edit it during transcription
        title, summary, content_type = await process_on_demand_async(url, status_msg=status_msg)

        if not summary:
            await status_msg.edit_text(
                "<b>Could not extract content from this URL.</b>\n"
                "Make sure it's a public video/article with accessible content.",
                parse_mode="HTML"
            )
            return

        # Delete the "working..." message and send the summary
        await status_msg.delete()
        await _send(update, format_on_demand_summary(title, summary, url, content_type))

    except Exception as e:
        logger.exception("On-demand processing failed for %s: %s", url, e)
        await status_msg.edit_text(
            f"Something went wrong processing this URL.\nError: <i>{html.escape(str(e)[:200])}</i>",
            parse_mode="HTML"
        )


# ── Bot factory ───────────────────────────────────────────────────────────────

def build_application() -> Application:
    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",     start))
    app.add_handler(CommandHandler("help",      help_cmd))
    app.add_handler(CommandHandler("add",       add_cmd))
    app.add_handler(CommandHandler("list",      list_cmd))
    app.add_handler(CommandHandler("remove",    remove_cmd))
    app.add_handler(CommandHandler("summarize", summarize_cmd))
    app.add_handler(CommandHandler("summary",   summary_today))
    app.add_handler(CommandHandler("alerts",    alerts_cmd))
    app.add_handler(CommandHandler("status",    status_cmd))

    # ALL free-text messages → conversational NLP handler
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_conversation))

    return app


async def send_unsent_summaries(app: Application):
    """Called by scheduler to proactively push unsent summaries."""
    summaries = db.get_unsent_summaries()
    if not summaries:
        return

    chat_id = config.TELEGRAM_CHAT_ID
    if not chat_id:
        logger.error("TELEGRAM_CHAT_ID not set")
        return

    try:
        await app.bot.send_message(
            chat_id=chat_id,
            text=format_daily_digest_header(len(summaries)),
            parse_mode="HTML"
        )
        for s in summaries:
            await app.bot.send_message(
                chat_id=chat_id,
                text=format_summary_message(s),
                disable_web_page_preview=True,
                parse_mode="HTML"
            )
            db.mark_summary_sent(s["id"])
    except Exception as e:
        logger.error("Failed to push summaries: %s", e)