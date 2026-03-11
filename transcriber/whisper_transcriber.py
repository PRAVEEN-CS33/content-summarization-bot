"""
transcriber/whisper_transcriber.py — Download audio and transcribe with Whisper.

FIXES:
  - Normalizes mobile YouTube URLs (m.youtube.com → youtube.com)
  - Resolves Spotify episode URLs → direct MP3 via RSS (bypasses DRM)
  - Direct HTTP download tried first (fast for plain .mp3 links); yt-dlp is fallback
  - Whisper transcribes audio in 5-minute chunks (pydub) to handle long episodes
"""
import logging
import re
import tempfile
import subprocess
import asyncio
from pathlib import Path
from typing import Optional
from concurrent.futures import ThreadPoolExecutor
import config
from discovery.spotify import is_spotify_url, extract_spotify_audio_url

logger = logging.getLogger(__name__)

# Single shared thread pool for heavy CPU/IO work
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="transcriber")

_whisper_model = None


# ── URL normalizer ─────────────────────────────────────────────────────────────

def normalize_youtube_url(url: str) -> str:
    """
    Convert any YouTube URL variant to a clean desktop URL.
    Fixes:  m.youtube.com  →  www.youtube.com
            youtu.be/ID    →  youtube.com/watch?v=ID
            /shorts/ID     →  youtube.com/watch?v=ID
    """
    # Mobile → desktop
    url = url.replace("m.youtube.com", "www.youtube.com")

    # Short link → full
    short = re.match(r"https?://youtu\.be/([\w-]+)", url)
    if short:
        return f"https://www.youtube.com/watch?v={short.group(1)}"

    # Shorts → watch
    shorts = re.match(r"https?://(?:www\.)?youtube\.com/shorts/([\w-]+)", url)
    if shorts:
        return f"https://www.youtube.com/watch?v={shorts.group(1)}"

    return url


# ── Whisper model loader ───────────────────────────────────────────────────────

def _get_model():
    global _whisper_model
    if _whisper_model is None:
        try:
            from faster_whisper import WhisperModel
            logger.info("Loading Whisper '%s' model (first run — ~30s)...", config.WHISPER_MODEL)
            _whisper_model = WhisperModel(
                config.WHISPER_MODEL,
                device=config.WHISPER_DEVICE,
                compute_type="int8",
            )
            logger.info("Whisper model ready.")
        except ImportError:
            logger.error("faster-whisper not installed. Run: pip install faster-whisper")
            raise
    return _whisper_model


# ── yt-dlp downloader ──────────────────────────────────────────────────────────

def _download_audio_sync(url: str, output_path: Path) -> bool:
    """
    Download audio from a URL. Tries:
      1. Direct HTTP download (fast for plain .mp3/.m4a links — bypasses yt-dlp overhead)
      2. yt-dlp fallback (for YouTube, SoundCloud, etc.)
    Runs in a thread pool — never called directly from async context.
    """
    # ── Strategy 1: Direct HTTP download ──────────────────────────────────────
    # Works great for podcast .mp3 enclosure URLs resolved from RSS
    lowered = url.lower()
    is_direct_audio = any(lowered.split("?")[0].endswith(ext) for ext in (".mp3", ".m4a", ".ogg", ".wav"))
    is_podcast_host = any(host in url for host in (
        "buzzsprout.com", "simplecast.com", "transistor.fm", "podbean.com",
        "anchor.fm", "soundcloud.com", "libsyn.com", "spreaker.com",
        "podomatic.com", "captivate.fm", "megaphone.fm",
    ))

    if is_direct_audio or is_podcast_host:
        try:
            logger.info("Attempting direct HTTP download: %s", url)
            resp = requests.get(
                url, stream=True, timeout=120,
                headers={"User-Agent": "Mozilla/5.0"}
            )
            resp.raise_for_status()
            downloaded = 0
            with open(str(output_path) + ".mp3", "wb") as f:
                for chunk in resp.iter_content(8192):
                    f.write(chunk)
                    downloaded += len(chunk)
            mb = downloaded / (1024 * 1024)
            logger.info("Direct download OK: %.1f MB", mb)
            return True
        except Exception as e:
            logger.warning("Direct download failed, falling back to yt-dlp: %s", e)

    # ── Strategy 2: yt-dlp ────────────────────────────────────────────────────
    cmd = [
        "yt-dlp",
        "--no-check-certificates",
        "--add-header", "Accept-Language:en-US,en;q=0.9",
        "--add-header", "Accept:text/html,application/xhtml+xml,*/*",
        "--no-playlist",
        "--extract-audio",
        "--audio-format", "mp3",
        "--audio-quality", "5",
        "--max-filesize", "150m",
        "--output", str(output_path),
        "--no-progress",
        url,
    ]
    try:
        result = subprocess.run(cmd, timeout=300, capture_output=True, text=True)
        if result.returncode != 0:
            stderr = result.stderr[:800]
            logger.error("yt-dlp failed (exit %d):\n%s", result.returncode, stderr)
            if "HTTP Error 400" in stderr or "Precondition check failed" in stderr:
                logger.error("YouTube 400 error — try: pip install -U yt-dlp")
            elif "Sign in to confirm" in stderr or "bot" in stderr.lower():
                logger.error("YouTube bot detection — video may need login")
            elif "Private video" in stderr:
                logger.error("Video is private — cannot download")
            return False
        candidates = list(output_path.parent.glob(f"{output_path.name}*"))
        return len(candidates) > 0
    except subprocess.TimeoutExpired:
        logger.error("yt-dlp timed out (>5 min) for: %s", url)
        return False
    except FileNotFoundError:
        logger.error("yt-dlp not installed. Run: pip install yt-dlp")
        return False


# ── Whisper transcription ──────────────────────────────────────────────────────

def _transcribe_file_sync(audio_path: Path) -> Optional[str]:
    """
    Transcribes audio in 5-minute chunks using pydub + faster-whisper.
    Chunking prevents Whisper from truncating or crashing on long episodes.
    """
    model = _get_model()
    try:
        from pydub import AudioSegment
        import os

        logger.info("Loading audio for chunked transcription: %s", audio_path)
        audio = AudioSegment.from_file(str(audio_path))

        chunk_length_ms = 5 * 60 * 1000   # 5 minutes per chunk
        chunks = [audio[i:i + chunk_length_ms] for i in range(0, len(audio), chunk_length_ms)]
        logger.info("Audio split into %d × 5-min chunks (%.1f min total)",
                    len(chunks), len(audio) / 60000)

        full_text      = []
        total_duration = 0.0

        for idx, chunk in enumerate(chunks, 1):
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                chunk_path = tmp.name
            try:
                chunk.export(chunk_path, format="mp3")
                logger.info("Transcribing chunk %d/%d...", idx, len(chunks))
                segments, info = model.transcribe(
                    chunk_path, beam_size=1, vad_filter=True,
                    vad_parameters={"min_silence_duration_ms": 500},
                )
                chunk_text = " ".join(seg.text.strip() for seg in segments)
                full_text.append(chunk_text)
                total_duration += info.duration
            finally:
                if os.path.exists(chunk_path):
                    os.remove(chunk_path)

        transcript = " ".join(full_text).strip()
        logger.info(
            "Transcription done: %d chars across %.1f min audio",
            len(transcript), total_duration / 60,
        )
        return transcript

    except ImportError:
        logger.warning("pydub not installed — falling back to single-pass Whisper")
        # Fallback: single-pass without chunking
        try:
            segments, info = model.transcribe(
                str(audio_path), beam_size=1, vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500},
            )
            text = " ".join(seg.text.strip() for seg in segments)
            logger.info("Single-pass transcription: %d chars, %.1f min", len(text), info.duration / 60)
            return text
        except Exception as e:
            logger.error("Transcription failed: %s", e)
            return None
    except Exception as e:
        logger.error("Transcription failed: %s", e)
        return None


def _full_pipeline_sync(url: str) -> Optional[str]:
    """Combined download + transcribe — runs entirely in thread pool."""
    config.AUDIO_TEMP_DIR.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(dir=config.AUDIO_TEMP_DIR) as tmp_dir:
        audio_path = Path(tmp_dir) / "audio"

        success = _download_audio_sync(url, audio_path)
        if not success:
            return None

        candidates = list(Path(tmp_dir).glob("audio*"))
        if not candidates:
            logger.error("No audio file found after download in %s", tmp_dir)
            return None

        actual_path = candidates[0]
        size_mb = actual_path.stat().st_size / (1024 * 1024)
        logger.info("Downloaded %.1f MB — starting transcription", size_mb)

        transcript = _transcribe_file_sync(actual_path)
        if transcript:
            logger.info("Transcription done: %d chars", len(transcript))
        return transcript


# ── Public API ─────────────────────────────────────────────────────────────────

def transcribe_url(url: str) -> Optional[str]:
    """
    Synchronous entry point (used by RSS pipeline in background thread).
    Normalizes URL before processing.
    """
    url = normalize_youtube_url(url)
    if is_spotify_url(url):
        url = extract_spotify_audio_url(url)
        
    logger.info("Transcribing: %s", url)
    return _full_pipeline_sync(url)


async def transcribe_url_async(url: str) -> Optional[str]:
    """
    Async entry point (used by Telegram bot handler).
    Runs heavy work in thread pool so the event loop stays responsive
    and Telegram heartbeat / keep-alive messages don't time out.
    """
    url = normalize_youtube_url(url)
    if is_spotify_url(url):
        # resolving RSS needs network requests, do it in a thread safely
        loop = asyncio.get_event_loop()
        url = await loop.run_in_executor(None, extract_spotify_audio_url, url)
        
    logger.info("Async transcribing: %s", url)
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, _full_pipeline_sync, url)