"""
discovery/spotify.py — Resolve Spotify URLs → RSS feed + MP3 for scheduling.

Supports:
  - Podcasts:       open.spotify.com/show/...
  - Episodes:       open.spotify.com/episode/...
  - Audiobooks:     open.spotify.com/audiobook/...

Strategy for RSS resolution (in priority order):
  1. Podcast Index API  — FREE, needs free signup at api.podcastindex.org
  2. iTunes Search API  — 100% free, no key, no limit
  3. gpodder.net        — 100% free, no key, open source podcast directory
  4. RSS bridge (pod.co / spotifyrss.com) — public proxy by show_id

The resolved RSS is stored in the `sources` table under type="podcast"
so the existing feed_monitor + pipeline handles it automatically.
"""
import re
import logging
import hashlib
import time
import requests
import feedparser
from typing import Optional, Tuple
from utils.retry import retry
import config

logger = logging.getLogger(__name__)

# Browser-like headers for Spotify scraping
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# Bot-like headers — Spotify returns simple HTML with real title/meta when User-Agent is a bot
BOT_HEADERS = {"User-Agent": "SpotifyBot/1.0"}

# Public RSS bridges — converts Spotify show IDs to RSS (no auth needed)
SPOTIFY_RSS_BRIDGE = "https://feeds.pod.co/{show_id}"
SPOTIFYRSS_BRIDGE  = "https://spotifyrss.com/feed/show/{show_id}"


# ── URL parsing ────────────────────────────────────────────────────────────────

def _parse_spotify_url(url: str) -> Optional[Tuple[str, str]]:
    """
    Extract (content_type, spotify_id) from a Spotify URL.

    Examples:
      open.spotify.com/show/XXXX       → ('show', 'XXXX')
      open.spotify.com/episode/XXXX    → ('episode', 'XXXX')
      open.spotify.com/audiobook/XXXX  → ('audiobook', 'XXXX')
    """
    m = re.search(
        r"spotify\.com/(show|episode|audiobook|album)/([A-Za-z0-9]+)",
        url, re.IGNORECASE,
    )
    if m:
        return m.group(1).lower(), m.group(2)
    return None


def is_spotify_url(url: str) -> bool:
    return bool(re.search(r"spotify\.com/(show|episode|audiobook)", url, re.IGNORECASE))


# ── Episode + show name scraping ───────────────────────────────────────────────

def _scrape_spotify_page(spotify_url: str) -> Tuple[str, str, str]:
    """
    Scrape Spotify page to extract (podcast_name, episode_title, description).
    Uses bot User-Agent because Spotify returns simpler static HTML for bots,
    which contains the actual <title> with episode/show names embedded.

    Title format from Spotify: "Episode Name - Show Name | Podcast on Spotify"
    """
    podcast_name  = ""
    episode_title = ""
    description   = ""

    try:
        resp = requests.get(spotify_url, timeout=config.REQUEST_TIMEOUT, headers=BOT_HEADERS)
        html = resp.text

        # Extract title: "Episode Name - Show Name | Podcast on Spotify"
        m = re.search(r"<title>([^<]+)</title>", html, re.IGNORECASE)
        if m:
            raw_title = m.group(1).replace("&amp;", "&").strip()
            if "Web Player" not in raw_title:
                parts = raw_title.split("|")
                if len(parts) > 1:
                    left_side = parts[0].strip()
                    # Show name is after the last dash in the left part
                    idx = left_side.rfind(" - ")
                    if idx > 0:
                        episode_title = left_side[:idx].strip()
                        podcast_name  = left_side[idx + 3:].strip()
                    else:
                        # No " - " separator; entire left is the episode title
                        episode_title = left_side

        # og:title fallback (browser scrape might yield this)
        if not podcast_name:
            m2 = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', html)
            if m2:
                og = m2.group(1).replace("&amp;", "&").strip()
                parts = [p.strip() for p in og.split("|")]
                podcast_name  = parts[1] if len(parts) >= 2 else ""
                episode_title = parts[0] if len(parts) >= 2 else og

        # Description
        m3 = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']', html)
        if m3:
            description = m3.group(1).replace("&amp;", "&").strip()

        logger.info(
            "Spotify page scraped — show: '%s', episode: '%s'",
            podcast_name[:40], episode_title[:60],
        )

    except Exception as e:
        logger.warning("Spotify page scrape failed: %s", e)

    return podcast_name, episode_title, description


def _scrape_spotify_show_name(spotify_url: str) -> Optional[str]:
    """Backwards-compatible: return just the show name."""
    name, _, _ = _scrape_spotify_page(spotify_url)
    return name or None


# ── RSS resolution strategies ──────────────────────────────────────────────────

def _strategy_podcast_index(name: str) -> Optional[str]:
    """Podcast Index API — free signup, ~unlimited personal use."""
    if not config.PODCAST_INDEX_API_KEY:
        return None
    api_key    = config.PODCAST_INDEX_API_KEY
    api_secret = config.PODCAST_INDEX_API_SECRET
    epoch      = str(int(time.time()))
    data_hash  = hashlib.sha1((api_key + api_secret + epoch).encode()).hexdigest()
    headers = {
        "X-Auth-Date":   epoch,
        "X-Auth-Key":    api_key,
        "Authorization": data_hash,
        "User-Agent":    "NaradaAI/1.0",
    }
    try:
        resp = requests.get(
            f"{config.PODCAST_INDEX_BASE_URL}/search/byterm",
            params={"q": name, "max": 1},
            headers=headers,
            timeout=config.REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        feeds = resp.json().get("feeds", [])
        if feeds:
            rss = feeds[0].get("url")
            logger.info("Podcast Index found: %s → %s", name, rss)
            return rss
    except Exception as e:
        logger.warning("Podcast Index search failed: %s", e)
    return None


def _strategy_itunes(name: str) -> Optional[str]:
    """iTunes Search API — 100% free, no key, no signup."""
    if not name:
        return None
    try:
        resp = requests.get(
            "https://itunes.apple.com/search",
            params={"term": name, "media": "podcast", "limit": 5},
            timeout=config.REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        for result in resp.json().get("results", []):
            rss = result.get("feedUrl", "")
            if rss:
                logger.info("iTunes found: %s → %s", name, rss)
                return rss
    except Exception as e:
        logger.warning("iTunes search failed: %s", e)
    return None


def _strategy_gpodder(name: str) -> Optional[str]:
    """gpodder.net — open source podcast directory, no key needed."""
    if not name:
        return None
    try:
        resp = requests.get(
            "https://gpodder.net/search.json",
            params={"q": name},
            timeout=config.REQUEST_TIMEOUT,
        )
        results = resp.json()
        if results:
            rss = results[0].get("url", "")
            if rss:
                logger.info("gPodder found: %s → %s", name, rss)
                return rss
    except Exception as e:
        logger.warning("gPodder search failed: %s", e)
    return None


def _strategy_rss_bridge(show_id: str) -> Optional[str]:
    """Try public RSS bridges that proxy Spotify show IDs → RSS."""
    for template in (SPOTIFY_RSS_BRIDGE, SPOTIFYRSS_BRIDGE):
        url = template.format(show_id=show_id)
        try:
            resp = requests.get(url, timeout=15, headers=HEADERS, allow_redirects=True)
            content_type = resp.headers.get("Content-Type", "")
            if resp.ok and ("xml" in content_type or "rss" in content_type or resp.text.strip().startswith("<")):
                feed = feedparser.parse(resp.content)
                if feed.entries:
                    logger.info("RSS bridge OK: %s → %d entries", url, len(feed.entries))
                    return url
        except Exception as e:
            logger.debug("RSS bridge failed (%s): %s", url, e)
    return None


# ── Main resolver ──────────────────────────────────────────────────────────────

def resolve_spotify(query: str) -> Optional[Tuple[str, str, str]]:
    """
    Given a Spotify URL or podcast name, return (source_id, name, rss_url).

    Priority for RSS resolution:
      1. Podcast Index (by show name)
      2. iTunes          (by show name)
      3. gPodder         (by show name)
      4. RSS bridge      (by Spotify show_id — pod.co / spotifyrss.com)
    """
    rss_url = None
    name    = query
    show_id = None

    if is_spotify_url(query):
        parsed = _parse_spotify_url(query)
        if not parsed:
            logger.error("Could not parse Spotify URL: %s", query)
            return None

        content_type, show_id = parsed

        # For episodes, find the parent show_id embedded in the page HTML
        if content_type == "episode":
            try:
                resp = requests.get(query, timeout=config.REQUEST_TIMEOUT, headers=HEADERS)
                show_match = re.search(r'spotify\.com/show/([A-Za-z0-9]+)', resp.text)
                if show_match:
                    show_id = show_match.group(1)
                    logger.info("Episode → parent show_id: %s", show_id)
            except Exception as e:
                logger.warning("Could not extract show_id from episode page: %s", e)

        # Scrape show/episode name from Spotify page
        scraped_name, _, _ = _scrape_spotify_page(query)
        if scraped_name:
            name = scraped_name

    # Try strategies in order
    for label, fn in [
        ("Podcast Index", lambda: _strategy_podcast_index(name)),
        ("iTunes",        lambda: _strategy_itunes(name)),
        ("gPodder",       lambda: _strategy_gpodder(name)),
        ("RSS bridge",    lambda: _strategy_rss_bridge(show_id) if show_id else None),
    ]:
        logger.info("Trying %s for '%s'...", label, name[:50])
        rss_url = fn()
        if rss_url:
            break

    if not rss_url:
        logger.error("Could not resolve RSS for Spotify query: %s", query)
        return None

    source_id = f"spotify_{show_id}" if show_id else re.sub(r"[^\w]", "_", name.lower())[:40]
    logger.info("Resolved Spotify '%s' → RSS: %s", name, rss_url)
    return source_id, name, rss_url


# ── MP3 extractor from RSS ─────────────────────────────────────────────────────

def _get_mp3_from_rss(rss_url: str, episode_title: str = "") -> Optional[str]:
    """Parse RSS feed and return the direct MP3 URL for the matched episode."""
    try:
        feed = feedparser.parse(rss_url)
        if not feed.entries:
            logger.warning("RSS has no entries: %s", rss_url)
            return None

        def _has_audio(enc) -> bool:
            href = enc.get("href", "") or enc.get("url", "")
            return (
                "audio" in enc.get("type", "")
                or href.lower().endswith((".mp3", ".m4a", ".ogg"))
            )

        # Try to match episode by title (normalized comparison)
        if episode_title:
            norm_ep = re.sub(r"[^a-z0-9]", "", episode_title.lower())
            for entry in feed.entries:
                norm_entry = re.sub(r"[^a-z0-9]", "", entry.get("title", "").lower())
                if norm_ep in norm_entry or norm_entry in norm_ep:
                    for enc in getattr(entry, "enclosures", []):
                        if _has_audio(enc):
                            url = enc.get("href") or enc.get("url")
                            logger.info("Matched episode MP3: %s → %s", entry.get("title", "")[:60], url)
                            return url

        # Fallback: return latest episode's MP3
        for entry in feed.entries[:3]:
            for enc in getattr(entry, "enclosures", []):
                if _has_audio(enc):
                    url = enc.get("href") or enc.get("url")
                    logger.info("Latest episode MP3 fallback: %s → %s", entry.get("title", "")[:60], url)
                    return url

    except Exception as e:
        logger.warning("RSS MP3 extraction failed: %s", e)
    return None


def extract_spotify_audio_url(spotify_url: str) -> str:
    """
    Given a Spotify episode URL, resolves the show's RSS feed and extracts
    the direct DRM-free MP3 link from the enclosure.

    Flow:
      1. Scrape Spotify page → get episode title + show name
      2. Resolve RSS via multi-strategy (Podcast Index → iTunes → gPodder → bridge)
      3. Match episode in RSS → return mp3 URL
      4. On any failure → return original spotify_url (caller handles gracefully)
    """
    # Step 1: Scrape episode/show info
    podcast_name, episode_title, _ = _scrape_spotify_page(spotify_url)
    query = podcast_name or episode_title or spotify_url

    logger.info("Extracting audio URL for Spotify episode: '%s'", episode_title[:60])

    # Step 2: Resolve RSS
    result = resolve_spotify(spotify_url)
    if not result:
        logger.warning("Could not resolve RSS for: %s", spotify_url)
        return spotify_url

    _, _, rss_url = result

    # Step 3: Get MP3 from RSS
    mp3_url = _get_mp3_from_rss(rss_url, episode_title)
    if mp3_url:
        return mp3_url

    logger.warning("Could not find MP3 in RSS feed for: %s", spotify_url)
    return spotify_url
