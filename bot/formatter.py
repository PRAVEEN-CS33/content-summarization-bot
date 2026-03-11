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

    The LLM already produces the body in the format:
      [title bold]

      OVERVIEW
      ...

      SUMMARY
      ➡️ ...

      SOURCE LINK
      [link]

    We prepend the source/channel line and append the real URL.
    """
    stype = summary.get("source_type", "default")
    emoji = EMOJI_MAP.get(stype, EMOJI_MAP["default"])
    label = SOURCE_LABEL.get(stype, stype.capitalize())
    sname = summary.get("source_name", "")
    text  = summary.get("summary_text", "")
    url   = summary.get("url", "")

    lines = [
        f"{emoji} <b>[{_esc(label)}]</b> {_esc(sname)}",
        "",
        text,  # summary already formatted by LLM (includes title, overview, summary, source link placeholder)
    ]

    # Replace the "[link]" placeholder with the real URL if available
    if url:
        result = "\n".join(lines)
        result = result.replace("[link]", _esc(url))
        return result

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
    label = {"youtube": "YouTube", "audio": "Podcast/Audio", "article": "Article"}.get(content_type, "Content")

    lines = [
        f"{emoji} <b>[{_esc(label)}] On-Demand Summary</b>",
        "",
        summary,  # Already formatted by LLM with title, overview, summary, source link placeholder
    ]

    result = "\n".join(lines)
    # Replace the "[link]" placeholder with the real URL
    result = result.replace("[link]", _esc(url))
    return result


def format_status_message(ollama_ok: bool, pending: int, total_sources: int) -> str:
    ollama_status = "✅ Online" if ollama_ok else "❌ Offline"
    return (
        f"⚙️ <b>System Status</b>\n\n"
        f"🧠 Gemini API: {_esc(ollama_status)}\n"
        f"📡 Sources monitored: {_esc(str(total_sources))}\n"
        f"⏳ Items pending: {_esc(str(pending))}\n"
    )
