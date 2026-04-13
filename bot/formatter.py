"""
bot/formatter.py — Format summaries and system messages as Telegram HTML.
"""
import html
from typing import List, Dict
from datetime import datetime


EMOJI_MAP = {
    "youtube":       "▶️",
    "podcast":       "🎙",
    "google_alert":  "🔔",
    "default":       "📄",
}

SOURCE_LABEL = {
    "youtube":      "YouTube",
    "podcast":      "Podcast",
    "google_alert": "Alert",
}


def _esc(text: str) -> str:
    """Escape special chars for Telegram HTML."""
    return html.escape(str(text))


def format_summary_message(summary: dict) -> str:
    """
    Format a single summary record into a Telegram HTML message.
    """
    stype = summary.get("source_type", "default")
    emoji = EMOJI_MAP.get(stype, EMOJI_MAP["default"])
    sname = summary.get("source_name", "")
    text  = summary.get("summary_text", "")
    url   = summary.get("url", "")
    title = summary.get("title", "Untitled")

    text = text.replace("**OVERVIEW**", "<b>OVERVIEW</b>")
    text = text.replace("**SUMMARY**", "<b>SUMMARY</b>")
    text = text.replace("**", "")

    # Clean up the output if AI ignored the instructions and included title/link
    if text.startswith(f"<b>{title}</b>"):
        text = text[len(f"<b>{title}</b>"):].strip()
    if text.startswith(title):
        text = text[len(title):].strip()
    
    # Strip everything after <b>SOURCE LINK</b> if it exists
    if "<b>SOURCE LINK</b>" in text:
        text = text.split("<b>SOURCE LINK</b>")[0].strip()

    clean_title = title.replace("<b>", "").replace("</b>", "")
    clean_title = _esc(clean_title)

    if stype == "google_alert":
        prefix = "🔔 Google Alert"
    elif stype == "youtube":
        prefix = "▶️ YouTube"
    elif stype == "podcast":
        prefix = "🎙️ Spotify"
    else:
        prefix = f"{emoji} {_esc(sname)}"

    header = f"<b>{prefix} - {clean_title}</b>"

    lines = [
        header,
        "",
        text,
    ]

    if url:
        lines.append("")
        lines.append("<b>SOURCE LINK</b>")
        lines.append(_esc(url))

    return "\n".join(lines)


def format_source_list(sources: List[Dict]) -> str:
    """Format /list command response."""
    if not sources:
        return "📭 No sources added yet. Use /add to get started."

    grouped: Dict[str, list] = {}
    for s in sources:
        grouped.setdefault(s["type"], []).append(s)

    lines = ["📋 <b>Your Subscribed Sources</b>\n"]
    for stype, items in grouped.items():
        emoji = EMOJI_MAP.get(stype, "📄")
        label = SOURCE_LABEL.get(stype, stype)
        lines.append(f"{emoji} <b>{_esc(label)}s</b>")
        for s in items:
            lines.append(f"  <code>{s['id']}</code> — {_esc(s['name'][:50])}")
        lines.append("")

    return "\n".join(lines)


def format_daily_digest_header(count: int) -> str:
    today = datetime.now().strftime("%A, %d %b %Y")
    return (
        f"🌅 <b>Daily Digest — {_esc(today)}</b>\n"
        f"<i>{_esc(str(count))} new summaries ready</i>\n"
        f"{'—' * 30}"
    )


def format_help_message() -> str:
    return """
NaradaAI - Your Personal Content Summarizer

COMMANDS:
/add youtube <name, @handle, or any YouTube URL>  - Subscribe to YouTube channel (Shorts skipped)
/add podcast <name or RSS URL>  - Subscribe to podcast
/add spotify <Spotify show/podcast URL or name>   - Subscribe to Spotify podcast/audiobook
/add topic   <keyword or URL>   - Track Google Alert / news topic
/list                           - Show all subscribed sources
/remove <id>                    - Remove a source
/summarize                      - Manually fetch + summarize now
/summary today                  - Read today's summaries
/alerts                         - Google Alerts RSS setup guide
/status                         - System health

INSTANT SUMMARY (just paste a URL):
  youtube.com/watch?v=...       - Transcribes + summarizes video
  youtu.be/... or /shorts/...   - Works for any YouTube URL
  Any podcast episode URL       - Downloads + transcribes audio
  Any article or blog URL       - Extracts + summarizes text

HOW IT WORKS:
1. Add your sources with /add
2. New content auto-fetched every hour
3. Daily digest delivered at 8am
4. Or paste any URL for instant on-demand summary
""".strip()


def format_on_demand_summary(title: str, summary: str, url: str, content_type: str) -> str:
    """Format an on-demand (URL-pasted) summary."""
    emoji = {"youtube": "▶️", "audio": "🎙", "article": "📰"}.get(content_type, "📄")

    text = summary
    text = text.replace("**OVERVIEW**", "<b>OVERVIEW</b>")
    text = text.replace("**SUMMARY**", "<b>SUMMARY</b>")
    text = text.replace("**", "")
    
    if text.startswith(f"<b>{title}</b>"):
        text = text[len(f"<b>{title}</b>"):].strip()
    if text.startswith(title):
        text = text[len(title):].strip()
        
    if "<b>SOURCE LINK</b>" in text:
        text = text.split("<b>SOURCE LINK</b>")[0].strip()

    clean_title = title.replace("<b>", "").replace("</b>", "")
    clean_title = _esc(clean_title)

    if content_type == "youtube":
        prefix = "▶️ YouTube"
    elif content_type in ("audio", "podcast"):
        prefix = "🎙️ Spotify"
    elif content_type == "google_alert":
        prefix = "🔔 Google Alert"
    else:
        prefix = "📄 Article"

    header = f"<b>{prefix} - {clean_title}</b>"

    lines = [
        header,
        "",
        text,
    ]

    if url:
        lines.append("")
        lines.append("<b>SOURCE LINK</b>")
        lines.append(_esc(url))

    return "\n".join(lines)


def format_status_message(ollama_ok: bool, pending: int, total_sources: int) -> str:
    ollama_status = "✅ Online" if ollama_ok else "❌ Offline"
    return (
        f"⚙️ <b>System Status</b>\n\n"
        f"🧠 Gemini API: {_esc(ollama_status)}\n"
        f"📡 Sources monitored: {_esc(str(total_sources))}\n"
        f"⏳ Items pending: {_esc(str(pending))}\n"
    )
