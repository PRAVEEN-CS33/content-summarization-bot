"""
processing/pipeline.py — Orchestrates the full pipeline for each pending item:
  1. Determine content type
  2. Extract text (description / transcript / article)
  3. Summarize
  4. Save to DB

YouTube & Podcast/Spotify strategy (NEW):
  - Step 1: Give URL directly to Gemini and ask it to summarize (fastest, no download)
  - Step 2: If Gemini cannot access it, fall back to caption extraction (YouTube)
            or Whisper audio transcription (Podcast/Spotify)
"""
import logging
import re
import subprocess
import tempfile
import requests
from pathlib import Path
from typing import Optional
from database import db
from summarizer import gemini_summarizer
from summarizer.gemini_summarizer import summarize_from_url as gemini_summarize_from_url
from transcriber import whisper_transcriber
from discovery.youtube import is_youtube_shorts
import config

logger = logging.getLogger(__name__)


# ── Content Extractors ────────────────────────────────────────────────────────

def _extract_youtube_captions(url: str) -> Optional[str]:
    """
    Step 1: Extract auto-generated captions / subtitles via yt-dlp.
    This is much faster than downloading audio — no Whisper needed.
    Returns concatenated subtitle text, or None if unavailable.
    """
    try:
        with tempfile.TemporaryDirectory(dir=config.AUDIO_TEMP_DIR) as tmp:
            out_base = Path(tmp) / "subs"
            cmd = [
                "yt-dlp",
                "--no-check-certificates",
                "--skip-download",               # don't download video/audio
                "--write-auto-sub",              # auto-generated captions
                "--write-sub",                   # manual captions if available
                "--sub-lang", "en",
                "--sub-format", "vtt",
                "--convert-subs", "vtt",
                "--no-playlist",
                "--output", str(out_base),
                url,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            # Find any .vtt file written
            vtt_files = list(Path(tmp).glob("*.vtt"))
            if not vtt_files:
                return None
            # Parse VTT — strip cue metadata, keep text lines
            raw = vtt_files[0].read_text(encoding="utf-8", errors="ignore")
            lines = []
            for line in raw.splitlines():
                line = line.strip()
                # Skip timing lines (00:00:00.000 --> ...) and WEBVTT header
                if re.match(r"^\d{2}:\d{2}", line) or line.startswith("WEBVTT") or not line:
                    continue
                # Strip VTT tags like <00:00:00.000><c> etc.
                line = re.sub(r"<[^>]+>", "", line)
                if line:
                    lines.append(line)
            text = " ".join(lines)
            # Deduplicate repeated phrases (auto-captions repeat)
            sentences = list(dict.fromkeys(text.split(". ")))
            text = ". ".join(sentences)
            if len(text) > 100:
                logger.info("Caption extraction OK: %d chars", len(text))
                return text
    except Exception as e:
        logger.warning("Caption extraction failed: %s", e)
    return None


def _youtube_url_first(item: dict) -> Optional[str]:
    """
    NEW Step 1: Ask Gemini to summarize the YouTube URL directly.
    Gemini can read public YouTube video captions/transcripts via the URL.
    Returns a fully-formatted summary string, or None if it cannot access it.
    """
    url   = item.get("url", "")
    title = item.get("title", "")
    sname = item.get("source_name", "")
    if not url:
        return None
    return gemini_summarize_from_url(url=url, title=title, source_name=sname, source_type="youtube")


def _extract_youtube_content(item: dict) -> Optional[str]:
    """
    YouTube extraction pipeline (fallback — used when URL-first fails):
      1. Try yt-dlp subtitle/caption extraction (fast)
      2. Fall back to full audio download + Whisper (slow)
      3. Last resort: RSS description text
    Returns raw text content (NOT a summary).
    """
    url = item.get("url", "")
    title = item.get("title", "")

    # Step 1: captions (fast, no audio download)
    if url:
        config.AUDIO_TEMP_DIR.mkdir(parents=True, exist_ok=True)
        captions = _extract_youtube_captions(url)
        if captions:
            return captions

    # Step 2: full Whisper transcription
    if url:
        logger.info("No captions — transcribing audio for: %s", title[:60])
        transcript = whisper_transcriber.transcribe_url(url)
        if transcript:
            return transcript

    # Step 3: RSS description as last resort
    description = item.get("description", "")
    if description:
        logger.info("Using RSS description fallback for: %s", title[:60])
        return description

    return None


def _extract_podcast_content(item: dict) -> Optional[str]:
    """Download and transcribe podcast/Spotify audio (used as Whisper fallback)."""
    url = item.get("url", "")
    if not url:
        return None
    logger.info("Transcribing podcast audio (Whisper fallback): %s", item.get("title", "")[:60])
    return whisper_transcriber.transcribe_url(url)


def _podcast_url_first(item: dict) -> Optional[str]:
    """
    NEW Step 1: Ask Gemini to summarize the podcast/episode URL directly.
    Returns a fully-formatted summary string, or None if it cannot access it.
    """
    url   = item.get("url", "")
    title = item.get("title", "")
    sname = item.get("source_name", "")
    if not url:
        return None
    return gemini_summarize_from_url(url=url, title=title, source_name=sname, source_type="podcast")


def _extract_article_content(url: str) -> Optional[str]:
    """Extract readable text from article URL using trafilatura."""
    try:
        import trafilatura
        downloaded = trafilatura.fetch_url(url)
        if downloaded:
            text = trafilatura.extract(downloaded, include_comments=False, include_tables=False)
            return text
    except ImportError:
        pass

    # Fallback: requests + basic parse
    try:
        resp = requests.get(url, timeout=config.REQUEST_TIMEOUT,
                           headers={"User-Agent": "Mozilla/5.0"})
        # Very basic — strip tags
        import re
        text = re.sub(r"<[^>]+>", " ", resp.text)
        text = re.sub(r"\s+", " ", text).strip()
        return text
    except Exception as e:
        logger.error("Article extraction failed for %s: %s", url, e)
        return None


def _get_feed_entry_description(item: dict) -> Optional[str]:
    """For Google Alerts / news: use the RSS entry description."""
    # Re-fetch the item's feed to get full entry details
    # The DB stores the entry URL; description comes from the feed
    return item.get("description", "")


# ── Main Pipeline ─────────────────────────────────────────────────────────────

def process_item(item: dict) -> bool:
    """
    Process a single pending item end-to-end.
    Returns True on success.
    """
    item_id   = item["id"]
    source_id = item["source_id"]
    title     = item.get("title", "Untitled")
    url       = item.get("url", "")
    stype     = item.get("source_type", "")
    sname     = item.get("source_name", "Unknown")

    logger.info("Processing [%s] %s", stype, title[:70])
    db.update_item_status(item_id, "processing")

    try:
        # Step 1: Extract content
        content = None

        if stype == "youtube":
            # Skip YouTube Shorts — they are not informational content
            if is_youtube_shorts(url):
                logger.info("Skipping YouTube Short: %s", title[:60])
                db.update_item_status(item_id, "skipped", "YouTube Short")
                return True  # not a failure, just skip

            # Step 1: URL-first — ask Gemini to summarize the YouTube URL directly
            logger.info("[YouTube] Trying URL-first strategy for: %s", title[:60])
            summary = _youtube_url_first(item)
            if summary:
                # Got a full summary directly — skip extraction entirely
                db.save_summary(
                    item_id=item_id, source_id=source_id,
                    title=title, summary_text=summary,
                    model_used=config.GEMINI_MODEL,
                )
                db.update_item_status(item_id, "done")
                logger.info("✓ [URL-first] Summary saved for: %s", title[:60])
                return True

            # Step 2: Fallback — extract captions/audio then summarize
            logger.info("[YouTube] URL-first failed — falling back to caption/Whisper for: %s", title[:60])
            content = _extract_youtube_content(item)

        elif stype == "podcast":
            # Step 1: URL-first — ask Gemini to summarize the episode URL directly
            logger.info("[Podcast] Trying URL-first strategy for: %s", title[:60])
            summary = _podcast_url_first(item)
            if summary:
                db.save_summary(
                    item_id=item_id, source_id=source_id,
                    title=title, summary_text=summary,
                    model_used=config.GEMINI_MODEL,
                )
                db.update_item_status(item_id, "done")
                logger.info("✓ [URL-first] Podcast summary saved for: %s", title[:60])
                return True

            # Step 2: Fallback — download audio + Whisper transcription
            logger.info("[Podcast] URL-first failed — falling back to Whisper for: %s", title[:60])
            content = _extract_podcast_content(item)

        elif stype == "google_alert":
            # Google Alerts RSS already contains article title + snippet in description
            # Use that directly — no need to fetch the article URL
            rss_content = item.get("description", "") or item.get("title", "")
            # Also try fetching the full article for richer content
            if url and len(rss_content) < 300:
                fetched = _extract_article_content(url)
                content = fetched if fetched and len(fetched) > len(rss_content) else rss_content
            else:
                content = rss_content

        else:
            # Generic: try article extraction
            content = _extract_article_content(url)

        if not content:
            logger.warning("No content extracted for: %s", title)
            db.update_item_status(item_id, "failed", "No content extracted")
            return False

        # Step 2: Summarize with Gemini
        summary = gemini_summarizer.summarize(
            content=content,
            title=title,
            source_name=sname,
            source_type=stype,
        )

        if not summary:
            db.update_item_status(item_id, "failed", "Summarization failed")
            return False

        # Step 3: Save
        db.save_summary(
            item_id=item_id,
            source_id=source_id,
            title=title,
            summary_text=summary,
            model_used=config.GEMINI_MODEL,
        )
        db.update_item_status(item_id, "done")
        logger.info("✓ Summary saved for: %s", title[:60])
        return True

    except Exception as e:
        logger.exception("Unexpected error processing item %d: %s", item_id, e)
        db.update_item_status(item_id, "failed", str(e)[:500])
        return False


def run_processing_pipeline(limit: int = None) -> dict:
    """Process all pending items. Called by scheduler."""
    limit = limit or config.MAX_ITEMS_PER_RUN
    pending = db.get_pending_items(limit=limit)
    stats = {"success": 0, "failed": 0}

    if not pending:
        logger.info("No pending items to process.")
        return stats

    logger.info("Processing %d pending items...", len(pending))
    for item in pending:
        if process_item(item):
            stats["success"] += 1
        else:
            stats["failed"] += 1

    logger.info("Processing complete: %s", stats)
    return stats
