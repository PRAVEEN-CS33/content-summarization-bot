"""
discovery/podcast.py — Resolve podcast name → RSS feed URL.

Priority:
  1. Podcast Index API (free, comprehensive)
  2. Apple Podcasts lookup (public API, no key needed)
  3. Direct URL input (user pastes RSS directly)
"""
import hashlib
import time
import logging
import requests
from typing import Optional, Tuple, List
from utils.retry import retry
import config

logger = logging.getLogger(__name__)


def _podcast_index_headers() -> dict:
    """HMAC-SHA1 auth headers for Podcast Index API."""
    api_key    = config.PODCAST_INDEX_API_KEY
    api_secret = config.PODCAST_INDEX_API_SECRET
    epoch      = str(int(time.time()))
    data_hash  = hashlib.sha1(
        (api_key + api_secret + epoch).encode()
    ).hexdigest()
    return {
        "X-Auth-Date":  epoch,
        "X-Auth-Key":   api_key,
        "Authorization": data_hash,
        "User-Agent":   "ContentSummarizer/1.0",
    }


@retry(exceptions=(requests.RequestException,))
def search_podcast_index(query: str, max_results: int = 5) -> List[dict]:
    """Search Podcast Index for podcasts matching query."""
    if not config.PODCAST_INDEX_API_KEY:
        logger.warning("No Podcast Index API key — skipping PI search")
        return []

    resp = requests.get(
        f"{config.PODCAST_INDEX_BASE_URL}/search/byterm",
        params={"q": query, "max": max_results},
        headers=_podcast_index_headers(),
        timeout=config.REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    feeds = resp.json().get("feeds", [])
    return [
        {
            "id":    f.get("id"),
            "title": f.get("title"),
            "url":   f.get("url"),
            "image": f.get("image"),
            "desc":  f.get("description", "")[:200],
        }
        for f in feeds
    ]


@retry(exceptions=(requests.RequestException,))
def search_apple_podcasts(query: str) -> List[dict]:
    """Apple iTunes Search API — no key required."""
    resp = requests.get(
        "https://itunes.apple.com/search",
        params={"term": query, "media": "podcast", "limit": 5},
        timeout=config.REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    results = resp.json().get("results", [])
    return [
        {
            "id":    r.get("collectionId"),
            "title": r.get("collectionName"),
            "url":   r.get("feedUrl"),
            "image": r.get("artworkUrl100"),
            "desc":  "",
        }
        for r in results
        if r.get("feedUrl")
    ]


def resolve_podcast(query: str) -> Optional[Tuple[str, str, str]]:
    """
    Returns (podcast_id_str, name, rss_url) or None.
    If query looks like a URL, return it directly.
    """
    # Direct RSS URL
    if query.startswith("http") and ("rss" in query or "feed" in query or "xml" in query):
        return "direct", query, query

    # Try Podcast Index first
    results = search_podcast_index(query)
    if not results:
        # Fallback to Apple
        results = search_apple_podcasts(query)

    if results:
        best = results[0]
        logger.info("Resolved podcast '%s' → %s", query, best["title"])
        return str(best["id"]), best["title"], best["url"]

    logger.error("Could not resolve podcast: %s", query)
    return None


def extract_spotify_rss(spotify_url: str) -> Optional[str]:
    """
    Spotify shows are hosted on Anchor/Buzzsprout/etc.
    Attempt to extract RSS from the podcast's hosting provider.
    Works when the RSS is exposed in the Spotify show page meta.
    """
    try:
        resp = requests.get(
            spotify_url,
            timeout=config.REQUEST_TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        # Spotify embeds RSS URL in Open Graph tags on some pages
        import re
        m = re.search(r'<link[^>]+type="application/rss\+xml"[^>]+href="([^"]+)"', resp.text)
        if m:
            return m.group(1)
        # Try JSON-LD
        m2 = re.search(r'"url"\s*:\s*"(https://[^"]+\.rss[^"]*)"', resp.text)
        if m2:
            return m2.group(1)
    except Exception as e:
        logger.warning("Spotify RSS extraction failed: %s", e)
    return None
