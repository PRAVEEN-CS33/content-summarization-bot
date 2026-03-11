"""
rss_manager/feed_monitor.py — Fetch feeds, detect new entries, queue for processing.

FIXES for Google Alerts:
  - Fetches raw XML with requests first (handles redirects + cookies)
  - Extracts content from entry.content[0].value (not summary/description)
  - Unwraps Google redirect URLs (google.com/url?url=REAL_URL)
"""
import logging
import requests
import feedparser
import urllib.parse
import re
from datetime import datetime, timezone
from typing import List, Dict, Optional
from database import db
from utils.retry import retry
import config

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; NaradaAI/1.0; +https://github.com/naradaai)",
    "Accept": "application/atom+xml,application/xml,text/xml,*/*",
}


def _unwrap_google_url(url: str) -> str:
    """
    Google Alerts wraps all links as:
      https://www.google.com/url?rct=j&sa=t&url=REAL_URL&ct=...
    Extract the real URL from the ?url= parameter.
    """
    if "google.com/url" in url:
        parsed = urllib.parse.urlparse(url)
        params = urllib.parse.parse_qs(parsed.query)
        real = params.get("url", [url])
        return real[0] if real else url
    return url


def _get_entry_content(entry) -> str:
    """
    Extract content text from a feedparser entry.
    Google Alerts uses <content type="html"> → entry.content[0].value
    Other feeds use <summary> or <description>
    Strip HTML tags from the result.
    """
    text = ""

    # Priority 1: entry.content list (Google Alerts Atom format)
    content_list = getattr(entry, "content", [])
    if content_list:
        text = content_list[0].get("value", "")

    # Priority 2: summary field (standard RSS)
    if not text:
        text = getattr(entry, "summary", "")

    # Priority 3: description field
    if not text:
        text = getattr(entry, "description", "")

    # Strip HTML tags
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _normalize_entry_id(entry) -> str:
    return (
        getattr(entry, "id", None)
        or getattr(entry, "link", None)
        or str(hash(getattr(entry, "title", "") + getattr(entry, "published", "")))
    )


def _parse_date(entry) -> str:
    for attr in ("published_parsed", "updated_parsed"):
        t = getattr(entry, attr, None)
        if t:
            try:
                return datetime(*t[:6], tzinfo=timezone.utc).isoformat()
            except Exception:
                pass
    return datetime.now(timezone.utc).isoformat()


@retry(exceptions=(Exception,))
def _fetch_feed(url: str) -> feedparser.FeedParserDict:
    """
    Fetch feed by first getting raw XML with requests (handles redirects),
    then parsing with feedparser. This fixes Google Alerts 302/cookie issues.
    """
    try:
        resp = requests.get(url, headers=HEADERS, timeout=config.REQUEST_TIMEOUT, allow_redirects=True)
        resp.raise_for_status()
        # Parse from raw content — feedparser handles encoding automatically
        feed = feedparser.parse(resp.content)
    except Exception:
        # Fallback: let feedparser fetch directly
        feed = feedparser.parse(url, agent=HEADERS["User-Agent"])

    if feed.bozo and not feed.entries:
        raise ValueError(f"Malformed/empty feed at {url}: {feed.get('bozo_exception', 'unknown')}")

    return feed


def fetch_and_queue_source(source: dict) -> Dict[str, int]:
    """Fetch a single RSS source, detect new entries, insert pending items."""
    stats     = {"new": 0, "skipped": 0, "errors": 0}
    source_id = source["id"]
    url       = source["url"]

    try:
        feed = _fetch_feed(url)
    except Exception as e:
        logger.error("Failed to fetch feed %s: %s", url, e)
        stats["errors"] += 1
        return stats

    logger.info("Fetched %d entries from [%s] %s", len(feed.entries), source["type"], source["name"])
    entries = feed.entries[:config.MAX_ITEMS_PER_RUN]

    for entry in entries:
        entry_id = _normalize_entry_id(entry)
        title    = getattr(entry, "title", "Untitled")
        raw_link = getattr(entry, "link", "")
        link     = _unwrap_google_url(raw_link)   # unwrap google redirect
        pub_date = _parse_date(entry)
        content  = _get_entry_content(entry)       # works for Google Alerts

        if db.item_exists(source_id, entry_id):
            stats["skipped"] += 1
            continue

        item_id = db.add_item(source_id, entry_id, title, link, pub_date, content)
        if item_id:
            stats["new"] += 1
            logger.debug("Queued: [%s] %s", source["name"][:30], title[:60])
        else:
            stats["skipped"] += 1

    db.update_source_fetched(source_id)
    return stats


def run_feed_monitor() -> Dict[str, int]:
    """Fetch all active sources. Called by scheduler."""
    sources = db.get_sources(active_only=True)
    totals  = {"new": 0, "skipped": 0, "errors": 0}

    logger.info("Feed monitor: checking %d sources", len(sources))
    for source in sources:
        stats = fetch_and_queue_source(source)
        for k in totals:
            totals[k] += stats[k]
        logger.info("[%s] %-30s new=%-3d skipped=%-3d errors=%d",
            source["type"], source["name"][:30],
            stats["new"], stats["skipped"], stats["errors"])

    logger.info("Feed monitor done: %s", totals)
    return totals