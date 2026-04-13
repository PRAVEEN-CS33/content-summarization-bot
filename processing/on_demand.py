"""
processing/on_demand.py — Instantly transcribe + summarize any URL sent to the bot.

Strategy for YouTube / generic Audio:
  1. Try Gemini URL-first — ask Gemini to summarize the URL directly (fast, no download)
  2. If Gemini returns CANNOT_ACCESS, fall back to:
     - Whisper transcription (download + local model)
     - Then summarize the transcript with Gemini

Strategy for Spotify episodes (open.spotify.com/episode/...):
  FIXED: Gemini URL-first is SKIPPED entirely for Spotify episodes because
  Spotify uses DRM-protected audio that Gemini cannot access. Instead the
  pipeline goes directly:
    RSS resolution → MP3 extraction → Whisper transcription → Gemini(text)
  Gemini only ever receives the Whisper transcript—never the Spotify URL.

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


# FIXED: Custom exception for RSS/MP3 resolution failures.
# Raised instead of silently falling through to Gemini URL-first, which would
# hallucinate a summary for DRM-protected Spotify audio.
class ResolutionError(Exception):
    """Raised when Spotify RSS / MP3 resolution fails with no fallback."""
    pass


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

# FIXED: Dedicated pattern for Spotify episode URLs — these get special-cased
# before any Gemini URL call to avoid hallucination on DRM-protected audio.
SPOTIFY_EPISODE_PATTERN = re.compile(
    r"open\.spotify\.com/episode/", re.IGNORECASE
)


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


def _is_spotify_episode(url: str) -> bool:
    """Return True iff the URL is an open.spotify.com/episode/ URL."""
    return bool(SPOTIFY_EPISODE_PATTERN.search(url))


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


# ── Spotify-specific pipeline helpers ─────────────────────────────────────────

def _spotify_resolve_mp3(spotify_url: str) -> Tuple[str, str]:
    """
    FIXED: Dedicated Spotify episode resolution path.

    Runs synchronously (called via run_in_executor from the async path).
    Returns (episode_title, mp3_url).
    Raises ResolutionError with a Telegram-safe message on failure.

    Steps:
      1. Scrape Spotify page for episode title + show name
      2. Resolve RSS feed via multi-strategy (Podcast Index → iTunes → gPodder → bridge)
      3. Fuzzy-match episode in RSS feed → return MP3 URL

    Gemini is NOT called here; the caller transcribes and then passes the
    transcript text to gemini_summarizer.summarize().
    """
    from discovery.spotify import (
        _scrape_spotify_page,
        resolve_rss_feed,
        _get_mp3_from_rss,
        _parse_spotify_url,
        resolve_spotify,
    )
    import requests
    import re
    import config

    BOT_HEADERS = {"User-Agent": "SpotifyBot/1.0"}

    # Step 1: Scrape Spotify page
    podcast_name, episode_title, _ = _scrape_spotify_page(spotify_url)
    title = episode_title or podcast_name or spotify_url.split("/")[-1][:80]

    # Step 2: Resolve parent show_id (needed for RSS bridge strategy)
    show_id = None
    og_audio_url = None
    parsed = _parse_spotify_url(spotify_url)
    if parsed and parsed[0] == "episode":
        _, episode_id = parsed
        try:
            resp = requests.get(spotify_url, timeout=config.REQUEST_TIMEOUT, headers=BOT_HEADERS)
            sm = re.search(r'spotify\.com/show/([A-Za-z0-9]+)', resp.text)
            if not sm:
                sm = re.search(r'spotify:show:([A-Za-z0-9]+)', resp.text)
            if sm:
                show_id = sm.group(1)
                logger.info("Spotify episode → parent show_id: %s", show_id)
            
            # Additional fallback: Find direct audio if RSS fails
            am = re.search(r'<meta[^>]+property=["\']og:audio["\'][^>]+content=["\']([^"\']+)["\']', resp.text)
            if am:
                og_audio_url = am.group(1).replace("&amp;", "&")
        except Exception as e:
            logger.warning("Could not extract show_id from episode page: %s", e)

    # Step 3: RSS resolution (via cached resolve_rss_feed)
    show_name = podcast_name or episode_title or ""
    logger.info("[Spotify] RSS resolution for show='%s' id='%s'", show_name[:50], show_id or "?")
    rss_url = resolve_rss_feed(show_id, show_name)

    if not rss_url:
        if og_audio_url:
            logger.info("[Spotify] RSS resolution failed, but found direct audio fallback: %s", og_audio_url)
            return title, og_audio_url
            
        # FIXED: Raise ResolutionError instead of falling through to Gemini.
        # Gemini receiving a Spotify URL would hallucinate a summary because
        # Spotify audio is DRM-protected and inaccessible to Gemini.
        raise ResolutionError(
            "⚠️ Could not find a podcast RSS feed for this Spotify episode.\n"
            "The show may not be listed in Podcast Index, iTunes, or gPodder.\n"
            "Try sharing a direct podcast URL or RSS feed link instead."
        )

    # Step 4: Extract MP3 URL from RSS feed (fuzzy title match)
    mp3_url = _get_mp3_from_rss(rss_url, episode_title)

    if not mp3_url:
        if og_audio_url:
            logger.info("[Spotify] MP3 not found in RSS, but found direct audio fallback: %s", og_audio_url)
            return title, og_audio_url
            
        # FIXED: Raise ResolutionError if the episode MP3 is not found.
        # Do NOT fall through to Gemini — it cannot access Spotify audio.
        raise ResolutionError(
            "⚠️ Found the podcast feed but could not locate this specific episode's audio.\n"
            "The episode may be very new or the feed may not include a direct MP3 link.\n"
            "Try again later or share a direct audio URL."
        )

    logger.info("[Spotify] Resolved MP3: %s → %s", title[:60], mp3_url)
    return title, mp3_url


# ── Main on-demand processor ───────────────────────────────────────────────────

async def process_on_demand_async(url: str, status_msg=None) -> Tuple[Optional[str], Optional[str], str]:
    """
    Async pipeline: detect → Gemini URL-first → Whisper fallback → summarize.
    Accepts an optional status_msg to keep alive during transcription.
    Returns (title, summary, content_type).

    For Spotify episodes (open.spotify.com/episode/...):
      FIXED: Gemini URL-first is SKIPPED. Pipeline goes directly:
        RSS resolution → MP3 extraction → Whisper transcription → Gemini(text)

    For YouTube:
      Step 1: Ask Gemini to summarize the URL directly (fast)
      Step 2: If Gemini can't access it → download + Whisper → Gemini summarizes transcript

    For generic Audio:
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

    # ── FIXED: Spotify episode fast-path (before any Gemini URL call) ──────────
    # Spotify uses DRM-protected audio; Gemini cannot access it and will
    # hallucinate. Go directly to RSS → MP3 → Whisper → Gemini(text).
    if _is_spotify_episode(url):
        logger.info("[OnDemand] Spotify episode detected — skipping Gemini URL-first.")
        try:
            title, mp3_url = await loop.run_in_executor(None, _spotify_resolve_mp3, url)
        except ResolutionError as exc:
            if ping_task:
                ping_task.cancel()
            # Return a None summary with the user-friendly error as the title
            # so the caller can surface it directly to the user.
            return str(exc), None, "audio"
        except Exception as exc:
            logger.error("[OnDemand] Unexpected Spotify resolution error: %s", exc)
            if ping_task:
                ping_task.cancel()
            return (
                "⚠️ An unexpected error occurred while processing this Spotify episode.",
                None,
                "audio",
            )

        try:
            content = await transcribe_url_async(mp3_url)
        finally:
            if ping_task:
                ping_task.cancel()
                try:
                    await ping_task
                except asyncio.CancelledError:
                    pass

        if not content:
            return title, None, "audio"

        # Gemini only receives the Whisper transcript — never the Spotify URL.
        summary = await loop.run_in_executor(
            None,
            lambda: gemini_summarizer.summarize(
                content=content,
                title=title,
                source_name="On-Demand",
                source_type="audio",
            )
        )
        return title, summary, "audio"

    # ── Step 1: Gemini URL-first for YouTube / non-Spotify Audio ──────────────
    if url_type in ("youtube", "audio"):
        if url_type == "youtube":
            vid_match = re.search(r"(?:v=|youtu\.be/|shorts/|live/)([\w-]+)", url)
            video_id = vid_match.group(1) if vid_match else "video"
            title = f"YouTube Video ({video_id})"
            # Try to fetch actual title
            try:
                from discovery.youtube import _fetch_page
                html_text = await loop.run_in_executor(None, _fetch_page, url)
                t_match = re.search(r'<meta property="og:title" content="([^"]+)"', html_text)
                if t_match:
                    title = html.unescape(t_match.group(1))
                else:
                    t_match = re.search(r'<title>([^<]+) - YouTube</title>', html_text)
                    if t_match:
                        title = html.unescape(t_match.group(1))
            except Exception:
                pass
        elif url_type == "audio":
            title = url.split("/")[-1][:60] or "Audio"
            # Try to resolve podcast title
            try:
                from discovery.spotify import is_spotify_url, resolve_spotify
                if is_spotify_url(url):
                    res = resolve_spotify(url)
                    if res and res[1]:
                        title = res[1]
                else:
                    from discovery.podcast import resolve_podcast
                    res = resolve_podcast(url)
                    if res and res[1]:
                        title = res[1]
            except Exception:
                pass

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
            def _extract_with_metadata(u):
                import trafilatura
                d = trafilatura.fetch_url(u)
                if not d: return None, None
                t = trafilatura.extract(d)
                # Try to get title from metadata
                m = trafilatura.metadata.extract_metadata(d)
                return t, (m.title if m and m.title else None)

            content, fetched_title = await loop.run_in_executor(None, _extract_with_metadata, url)
            if fetched_title:
                title = fetched_title
            else:
                title = url.split("/")[-1][:80] or url
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

    # ── FIXED: Spotify episode fast-path (sync version) ────────────────────────
    # Mirrors the async branch: skip Gemini URL-first, go direct to
    # RSS → MP3 → Whisper → Gemini(text).
    if _is_spotify_episode(url):
        logger.info("[OnDemand-sync] Spotify episode — skipping Gemini URL-first.")
        try:
            title, mp3_url = _spotify_resolve_mp3(url)
        except ResolutionError as exc:
            return str(exc), None, "audio"
        except Exception as exc:
            logger.error("[OnDemand-sync] Unexpected Spotify resolution error: %s", exc)
            return "⚠️ An unexpected error occurred while processing this Spotify episode.", None, "audio"

        content = transcribe_url(mp3_url)
        if not content:
            return title, None, "audio"

        summary = gemini_summarizer.summarize(
            content=content, title=title,
            source_name="On-Demand", source_type="audio",
        )
        return title, summary, "audio"

    if url_type == "youtube":
        vid_match = re.search(r"(?:v=|youtu\.be/|shorts/|live/)([\w-]+)", url)
        video_id = vid_match.group(1) if vid_match else "video"
        title = f"YouTube Video ({video_id})"
        try:
            from discovery.youtube import _fetch_page
            html_text = _fetch_page(url)
            t_match = re.search(r'<meta property="og:title" content="([^"]+)"', html_text)
            if t_match: title = html.unescape(t_match.group(1))
            else:
                t_match = re.search(r'<title>([^<]+) - YouTube</title>', html_text)
                if t_match: title = html.unescape(t_match.group(1))
        except: pass

        # Try Gemini URL-first
        summary = gemini_summarizer.summarize_from_url(
            url=url, title=title, source_name="On-Demand", source_type="youtube"
        )
        if summary:
            return title, summary, url_type
        content = transcribe_url(url)
    elif url_type == "audio":
        title = url.split("/")[-1][:60] or "Audio"
        try:
            from discovery.spotify import is_spotify_url, resolve_spotify
            if is_spotify_url(url):
                res = resolve_spotify(url)
                if res and res[1]:
                    title = res[1]
            else:
                from discovery.podcast import resolve_podcast
                res = resolve_podcast(url)
                if res and res[1]:
                    title = res[1]
        except Exception:
            pass

        # Try Gemini URL-first (non-Spotify audio only — Spotify is handled above)
        summary = gemini_summarizer.summarize_from_url(
            url=url, title=title, source_name="On-Demand", source_type="audio"
        )
        if summary:
            return title, summary, url_type
        content = transcribe_url(url)
    else:
        title = url.split("/")[-1][:80] or url
        import trafilatura
        d = trafilatura.fetch_url(url)
        if d:
            content = trafilatura.extract(d)
            m = trafilatura.metadata.extract_metadata(d)
            if m and m.title: title = m.title
        else:
            content = _extract_article_content(url)

    if not content:
        return title, None, url_type

    summary = gemini_summarizer.summarize(
        content=content, title=title,
        source_name="On-Demand", source_type=url_type,
    )
    return title, summary, url_type
