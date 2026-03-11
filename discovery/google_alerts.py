"""
discovery/google_alerts.py — Full Google Alerts RSS integration.

TWO modes:
  1. REAL Google Alerts RSS  — user pastes their personal feed URL
                               (https://www.google.com/alerts/feeds/USER_ID/ALERT_ID)
  2. Google News RSS fallback — built from keyword, works without login

HOW TO GET YOUR REAL GOOGLE ALERTS RSS:
  1. Go to https://www.google.co.in/alerts
  2. Create or edit an alert
  3. In "Deliver to" -> choose RSS feed
  4. Click the RSS icon next to the alert -> copy the URL
  5. Paste it with: /add topic <url>
"""
import logging
import urllib.parse
import re
import feedparser
from typing import Optional, Tuple, List
from utils.retry import retry

logger = logging.getLogger(__name__)


def build_google_news_rss(topic: str, lang: str = "en", country: str = "IN", time_range: str = "1d") -> str:
    """Build a Google News RSS URL. No account required. Refreshes ~15 min."""
    query   = f"{topic} when:{time_range}"
    encoded = urllib.parse.quote(query)
    return (
        f"https://news.google.com/rss/search"
        f"?q={encoded}&hl={lang}&gl={country}&ceid={country}:{lang}"
    )


# Matches Google Alerts RSS URLs from any regional domain:
# google.com, google.co.in, google.co.uk, google.com.au, etc.
_GOOGLE_ALERTS_RSS_PATTERN = re.compile(
    r"https?://www\.google\.[a-z.]+/alerts/feeds/", re.IGNORECASE
)


def resolve_google_alert(query: str) -> Tuple[str, str, str]:
    """
    Given keyword OR Google Alerts RSS URL,
    return (id_string, display_name, rss_url).

    Supports Google Alerts RSS URLs from any regional Google domain
    (google.com, google.co.in, google.co.uk, google.com.au, …).
    """
    # Real Google Alerts RSS — any regional domain
    if _GOOGLE_ALERTS_RSS_PATTERN.search(query):
        parts    = query.rstrip("/").split("/")
        alert_id = parts[-1] if parts else "alert"
        name     = f"Google Alert ({alert_id[:12]})"
        logger.info("Using real Google Alerts RSS: %s", query)
        return f"ga_{alert_id}", name, query

    # Google News RSS URL passed directly
    if "news.google.com/rss" in query:
        slug = re.sub(r"[^\w]", "_", query)[:40]
        return slug, "Google News Feed", query

    # Keyword -> Google News RSS
    rss_url = build_google_news_rss(query)
    slug    = re.sub(r"[^\w]", "_", query.lower())[:40]
    logger.info("Built Google News RSS for: %s", query)
    return slug, f"Alert: {query}", rss_url


@retry(exceptions=(Exception,))
def preview_alert_feed(rss_url: str, max_items: int = 3) -> List[dict]:
    """Fetch RSS and return latest N items as preview."""
    feed = feedparser.parse(rss_url)
    items = []
    for entry in feed.entries[:max_items]:
        items.append({
            "title":     getattr(entry.title, "title", "No title"),
            "link":      getattr(entry.link, "link", ""),
            "published": getattr(entry.published, "published", ""),
            "source":    getattr(entry.source, "source", {}).get("title", ""),
        })
    return items


GOOGLE_ALERT_SETUP_GUIDE = """
<b>Setting Up Real Google Alerts RSS</b>

<b>Step 1:</b> Go to https://www.google.co.in/alerts

<b>Step 2:</b> Create alert:
  Enter topic → Show options → Deliver to: RSS feed → Create Alert

<b>Step 3:</b> Get RSS URL:
  Click the RSS icon next to your alert → Copy URL
  (looks like: https://www.google.com/alerts/feeds/XXXXX/YYYYY)

<b>Step 4:</b> Add it:
  /add topic https://www.google.com/alerts/feeds/XXXXX/YYYYY

Already working: Google News RSS set as backup — summaries start immediately!
"""

GOOGLE_ALERT_INSTRUCTIONS = GOOGLE_ALERT_SETUP_GUIDE  # backward compat alias
