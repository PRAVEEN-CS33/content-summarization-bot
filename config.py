"""
Central configuration — reads from environment variables or .env file.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(override=True)

BASE_DIR = Path(__file__).parent

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")   # your personal chat ID

# ── Database ──────────────────────────────────────────────────────────────────
DATABASE_PATH = BASE_DIR / "data" / "summarizer.db"

# ── Google Gemini API ─────────────────────────────────────────────────────────
GEMINI_API_KEY   = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL     = os.getenv("GEMINI_MODEL", "")  # fast + capable
# Options: "gemini-2.0-flash" | "gemini-1.5-pro" | "gemini-1.5-flash"

# ── Whisper (local transcription) ─────────────────────────────────────────────
WHISPER_MODEL    = os.getenv("WHISPER_MODEL", "base")   # base fits 16GB comfortably
WHISPER_DEVICE   = os.getenv("WHISPER_DEVICE", "cpu")   # "cuda" if GPU available
AUDIO_TEMP_DIR   = BASE_DIR / "data" / "audio_tmp"

# ── Podcast Index API ─────────────────────────────────────────────────────────
PODCAST_INDEX_API_KEY    = os.getenv("PODCAST_INDEX_API_KEY", "")
PODCAST_INDEX_API_SECRET = os.getenv("PODCAST_INDEX_API_SECRET", "")
PODCAST_INDEX_BASE_URL   = "https://api.podcastindex.org/api/1.0"

# ── YouTube ───────────────────────────────────────────────────────────────────
YOUTUBE_API_KEY  = os.getenv("YOUTUBE_API_KEY", "")     # optional; scraping fallback exists
YT_RSS_BASE      = "https://www.youtube.com/feeds/videos.xml?channel_id="

# ── Scheduler ─────────────────────────────────────────────────────────────────
FETCH_INTERVAL_MINUTES  = int(os.getenv("FETCH_INTERVAL_MINUTES", "60"))
SUMMARY_HOUR            = int(os.getenv("SUMMARY_HOUR", "8"))     # 8 AM daily digest
MAX_ITEMS_PER_RUN       = int(os.getenv("MAX_ITEMS_PER_RUN", "10"))

# NOTE: No transcript/audio limits — gemini_summarizer passes full content and lets Gemini decide length.

# ── Retry / reliability ───────────────────────────────────────────────────────
MAX_RETRIES      = 3
RETRY_BACKOFF    = 2          # exponential backoff base (seconds)
REQUEST_TIMEOUT  = 30
