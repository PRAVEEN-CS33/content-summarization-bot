"""
processing/on_demand.py — Instantly transcribe + summarize any URL sent to the bot.

Strategy for YouTube / Audio:
  1. Try Gemini URL-first — ask Gemini to summarize the URL directly (fast, no download)
  2. If Gemini returns CANNOT_ACCESS, fall back to:
     - Whisper transcription (download + local model)
     - Then summarize the transcript with Gemini

Articles: extract text → Gemini summarize.
"""
import logging
import re
import asyncio
import html
from typing import Optional, Tuple

from transcriber.whisper_transcriber import transcribe_url_async, normalize_youtube_url
from summarizer import gemini_summarizer
from processing.pipeline import _extract_article_content

logger = logging.getLogger(__name__)

# ── URL type detection ─────────────────────────────────────────────────────────

YOUTUBE_PATTERNS = [
    r"(?:https?://)?(?:www\.|m\.)?youtube\.com/watch\?v=[\w-]+",
    r"(?:https?://)?youtu\.be/[\w-]+",
    r"(?:https?://)?(?:www\.|m\.)?youtube\.com/shorts/[\w-]+",
    r"(?:https?://)?(?:www\.|m\.)?youtube\.com/live/[\w-]+",
]

AUDIO_PATTERNS = [
    r"\.mp3(\?|$)", r"\.m4a(\?|$)", r"\.wav(\?|$)", r"\.ogg(\?|$)",
    r"spotify\.com/episode/",
    r"podcasts\.apple\.com",
    r"anchor\.fm", r"buzzsprout\.com", r"soundcloud\.com",
    r"podbean\.com", r"transistor\.fm", r"simplecast\.com",
]


def detect_url_type(url: str) -> str:
    """Return 'youtube' | 'audio' | 'article'"""
    norm = normalize_youtube_url(url)
    for pat in YOUTUBE_PATTERNS:
        if re.search(pat, norm, re.IGNORECASE):
            return "youtube"
    for pat in AUDIO_PATTERNS:
        if re.search(pat, url, re.IGNORECASE):
            return "audio"
    return "article"


def _extract_url(text: str) -> Optional[str]:
    match = re.search(r"https?://[^\s]+", text)
    return match.group(0).rstrip(".,)>") if match else None


# ── Keep-alive ping during long operations ─────────────────────────────────────

async def _keep_alive_ping(status_msg, interval: int = 20):
    """
    Periodically edits the status message to prevent Telegram timeout.
    Cancel this task when processing is done.
    """
    dots = 1
    base = status_msg.text.split("...")[0]
    while True:
        await asyncio.sleep(interval)
        try:
            dot_str = "." * dots
            await status_msg.edit_text(f"{html.escape(base)}{dot_str}", parse_mode="HTML")
            dots = (dots % 4) + 1
        except Exception:
            pass  # message may have been deleted or edited elsewhere


# ── Main on-demand processor ───────────────────────────────────────────────────

async def process_on_demand_async(url: str, status_msg=None) -> Tuple[Optional[str], Optional[str], str]:
    """
    Async pipeline: detect → Gemini URL-first → Whisper fallback → summarize.
    Accepts an optional status_msg to keep alive during transcription.
    Returns (title, summary, content_type).

    For YouTube:
      Step 1: Ask Gemini to summarize the URL directly (fast)
      Step 2: If Gemini can't access it → download + Whisper → Gemini summarizes transcript

    For Audio:
      Step 1: Gemini URL-first attempt
      Step 2: Whisper transcription → Gemini summarization

    For Articles:
      Trafilatura extract → Gemini summarization
    """
    url      = normalize_youtube_url(url)
    url_type = detect_url_type(url)
    title    = url
    content  = None

    # Start keep-alive pinger for long operations
    ping_task = None
    if status_msg and url_type in ("youtube", "audio"):
        ping_task = asyncio.create_task(_keep_alive_ping(status_msg, interval=15))

    loop    = asyncio.get_event_loop()
    summary = None

    # ── Step 1: Gemini URL-first for YouTube / Audio ───────────────────────────
    if url_type in ("youtube", "audio"):
        if url_type == "youtube":
            vid_match = re.search(r"(?:v=|youtu\.be/|shorts/|live/)([\w-]+)", url)
            title = f"YouTube Video ({vid_match.group(1) if vid_match else 'video'})"
        else:
            title = url.split("/")[-1][:60] or "Audio"

        logger.info("[OnDemand] Gemini URL-first for: %s", title)
        summary = await loop.run_in_executor(
            None,
            lambda: gemini_summarizer.summarize_from_url(
                url=url, title=title, source_name="On-Demand", source_type=url_type
            )
        )

        if summary:
            logger.info("✓ [OnDemand] Gemini URL-first succeeded — skipping Whisper.")
            if ping_task:
                ping_task.cancel()
            return title, summary, url_type

        logger.info("[OnDemand] Gemini URL-first returned CANNOT_ACCESS — falling back to Whisper.")

    # ── Step 2: Whisper transcription / article extraction fallback ────────────
    try:
        if url_type in ("youtube", "audio"):
            content = await transcribe_url_async(url)
        else:
            # Article — fast, run in thread pool
            title   = url.split("/")[-1][:80] or url
            content = await loop.run_in_executor(None, _extract_article_content, url)
    finally:
        if ping_task:
            ping_task.cancel()
            try:
                await ping_task
            except asyncio.CancelledError:
                pass

    if not content:
        return title, None, url_type

    # ── Step 3: Summarize transcript / article text with Gemini ───────────────
    summary = await loop.run_in_executor(
        None,
        lambda: gemini_summarizer.summarize(
            content=content,
            title=title,
            source_name="On-Demand",
            source_type=url_type,
        )
    )

    return title, summary, url_type


# Keep sync version for pipeline compatibility
def process_on_demand(url: str) -> Tuple[Optional[str], Optional[str], str]:
    """Sync wrapper — used by non-async callers."""
    url      = normalize_youtube_url(url)
    url_type = detect_url_type(url)
    title    = url
    content  = None

    from transcriber.whisper_transcriber import transcribe_url

    if url_type == "youtube":
        vid_match = re.search(r"(?:v=|youtu\.be/|shorts/|live/)([\w-]+)", url)
        title   = f"YouTube Video ({vid_match.group(1) if vid_match else 'video'})"
        # Try Gemini URL-first
        summary = gemini_summarizer.summarize_from_url(
            url=url, title=title, source_name="On-Demand", source_type="youtube"
        )
        if summary:
            return title, summary, url_type
        content = transcribe_url(url)
    elif url_type == "audio":
        title   = url.split("/")[-1][:60] or "Audio"
        # Try Gemini URL-first
        summary = gemini_summarizer.summarize_from_url(
            url=url, title=title, source_name="On-Demand", source_type="audio"
        )
        if summary:
            return title, summary, url_type
        content = transcribe_url(url)
    else:
        title   = url.split("/")[-1][:80] or url
        content = _extract_article_content(url)

    if not content:
        return title, None, url_type

    summary = gemini_summarizer.summarize(
        content=content, title=title,
        source_name="On-Demand", source_type=url_type,
    )
    return title, summary, url_type
