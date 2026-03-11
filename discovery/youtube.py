"""
discovery/youtube.py — Convert ANY YouTube URL or name → channel RSS feed.

Accepts:
  - Channel handle:  @mkbhd
  - Channel URL:     youtube.com/channel/UCxxxxxx
  - Video URL:       youtube.com/watch?v=xxxxx
  - Shorts URL:      youtube.com/shorts/xxxxx
  - Live URL:        youtube.com/live/xxxxx
  - youtu.be link:   youtu.be/xxxxx
  - Plain channel name / search term
  - Bare channel_id: UCxxxxxx...

Strategy (no quota cost):
  1. Extract channel_id directly if embedded in URL.
  2. For video/shorts/live — fetch the watch page and scrape channelId.
  3. For handles — fetch the @handle page and scrape channelId.
  4. Fallback: YouTube Data API v3 (needs API key).
"""
import re
import logging
import requests
from typing import Optional, Tuple
from utils.retry import retry
import config

logger = logging.getLogger(__name__)

YT_CHANNEL_RSS = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
YT_HANDLE_URL  = "https://www.youtube.com/@{handle}"
YT_WATCH_URL   = "https://www.youtube.com/watch?v={video_id}"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


# ── Helpers ────────────────────────────────────────────────────────────────────

def is_youtube_shorts(url: str) -> bool:
    """Return True if the RSS feed entry URL is a YouTube Short."""
    return bool(re.search(r"youtube\.com/shorts/", url, re.IGNORECASE))


def _extract_video_id(url: str) -> Optional[str]:
    """Extract video_id from any YouTube video/shorts/live URL."""
    patterns = [
        r"(?:v=|youtu\.be/|shorts/|live/)([A-Za-z0-9_-]{11})",
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return None


def _extract_channel_id_from_html(html: str) -> Optional[str]:
    """Parse channel_id (UCxxx) from any YouTube page source."""
    patterns = [
        r'"channelId"\s*:\s*"(UC[\w-]{22})"',
        r'<meta itemprop="channelId" content="(UC[\w-]{22})"',
        r'"externalId"\s*:\s*"(UC[\w-]{22})"',
        r'channel_id=(UC[\w-]{22})',
        r'"browseId"\s*:\s*"(UC[\w-]{22})"',
    ]
    for pat in patterns:
        m = re.search(pat, html)
        if m:
            return m.group(1)
    return None


def _extract_channel_name_from_html(html: str) -> Optional[str]:
    """Try to get a human-readable channel name from page HTML."""
    patterns = [
        r'"author"\s*:\s*"([^"]{2,60})"',
        r'"channelName"\s*:\s*"([^"]{2,60})"',
        r'"ownerChannelName"\s*:\s*"([^"]{2,60})"',
        r'<title>([^<]{2,80}) - YouTube</title>',
    ]
    for pat in patterns:
        m = re.search(pat, html)
        if m:
            name = m.group(1).strip()
            if name and name.lower() not in ("youtube", ""):
                return name
    return None


@retry(exceptions=(requests.RequestException,))
def _fetch_page(url: str) -> str:
    resp = requests.get(url, timeout=config.REQUEST_TIMEOUT, headers=HEADERS)
    resp.raise_for_status()
    return resp.text


# ── Main resolver ──────────────────────────────────────────────────────────────

def resolve_youtube_channel(query: str) -> Optional[Tuple[str, str, str]]:
    """
    Given ANY YouTube input (video URL, shorts URL, channel URL,
    handle, bare channel_id, or search name),
    return (channel_id, channel_name, rss_url).

    The RSS feed contains ONLY regular videos (YouTube excludes Shorts
    from the channel RSS automatically).
    """
    query = query.strip()

    # ── 1. Bare channel_id ────────────────────────────────────────────────────
    if re.match(r"^UC[\w-]{22}$", query):
        channel_id = query
        rss = YT_CHANNEL_RSS.format(channel_id=channel_id)
        logger.info("Direct channel_id: %s", channel_id)
        return channel_id, channel_id, rss

    # ── 2. Channel URL with channel_id embedded ───────────────────────────────
    channel_match = re.search(r"youtube\.com/channel/(UC[\w-]{22})", query)
    if channel_match:
        channel_id = channel_match.group(1)
        rss = YT_CHANNEL_RSS.format(channel_id=channel_id)
        logger.info("Extracted channel_id from URL: %s", channel_id)
        return channel_id, channel_id, rss

    # ── 3. Video / Shorts / Live URL → fetch watch page → scrape channel_id ──
    video_id = _extract_video_id(query)
    if video_id:
        watch_url = YT_WATCH_URL.format(video_id=video_id)
        try:
            html = _fetch_page(watch_url)
            channel_id = _extract_channel_id_from_html(html)
            if channel_id:
                name = _extract_channel_name_from_html(html) or channel_id
                rss = YT_CHANNEL_RSS.format(channel_id=channel_id)
                logger.info("Resolved video %s → channel %s (%s)", video_id, channel_id, name)
                return channel_id, name, rss
        except Exception as e:
            logger.error("Failed scraping video page %s: %s", watch_url, e)

    # ── 4. Handle URL (@name) or bare @handle ────────────────────────────────
    handle_match = re.search(r"youtube\.com/@([\w.-]+)", query)
    if handle_match:
        handle = handle_match.group(1)
    elif query.startswith("@"):
        handle = query.lstrip("@")
    else:
        handle = None

    if handle:
        url = YT_HANDLE_URL.format(handle=handle)
        try:
            html = _fetch_page(url)
            channel_id = _extract_channel_id_from_html(html)
            if channel_id:
                name = _extract_channel_name_from_html(html) or handle
                rss = YT_CHANNEL_RSS.format(channel_id=channel_id)
                logger.info("Resolved @%s → channel %s (%s)", handle, channel_id, name)
                return channel_id, name, rss
        except Exception as e:
            logger.error("Failed scraping handle page @%s: %s", handle, e)

    # ── 5. Plain text name → YouTube API or handle-page scrape ───────────────
    # Try treating the name as a handle first (common for well-known channels)
    url = YT_HANDLE_URL.format(handle=query.replace(" ", ""))
    try:
        html = _fetch_page(url)
        channel_id = _extract_channel_id_from_html(html)
        if channel_id:
            name = _extract_channel_name_from_html(html) or query
            rss = YT_CHANNEL_RSS.format(channel_id=channel_id)
            logger.info("Resolved name '%s' via handle page → %s", query, channel_id)
            return channel_id, name, rss
    except Exception:
        pass

    # ── 6. YouTube Data API v3 fallback ──────────────────────────────────────
    if config.YOUTUBE_API_KEY:
        result = _resolve_via_api(query)
        if result:
            return result

    logger.error("Could not resolve YouTube channel for: %s", query)
    return None


def _resolve_via_api(query: str) -> Optional[Tuple[str, str, str]]:
    """YouTube Data API v3 search fallback."""
    try:
        resp = requests.get(
            "https://www.googleapis.com/youtube/v3/search",
            params={
                "part": "snippet",
                "q": query,
                "type": "channel",
                "maxResults": 1,
                "key": config.YOUTUBE_API_KEY,
            },
            timeout=config.REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        if items:
            channel_id = items[0]["id"]["channelId"]
            name = items[0]["snippet"]["title"]
            rss = YT_CHANNEL_RSS.format(channel_id=channel_id)
            logger.info("YouTube API resolved '%s' → %s", query, channel_id)
            return channel_id, name, rss
    except Exception as e:
        logger.error("YouTube API fallback failed: %s", e)
    return None
