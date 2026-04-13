"""
summarizer/gemini_summarizer.py — Summarize content using Google Gemini API.

Recommended models:
  - gemini-2.0-flash    ← DEFAULT: very fast, free tier generous
  - gemini-1.5-pro      ← Higher quality, larger context window
  - gemini-1.5-flash    ← Fast + cheaper than 1.5-pro
"""
import logging
from typing import Optional
import config

logger = logging.getLogger(__name__)

# Lazy-initialized Gemini client
_gemini_client = None


def _get_client():
    global _gemini_client
    if _gemini_client is None:
        if not config.GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY is not set in your .env file")
        from google import genai
        from google.genai import types
        _gemini_client = genai.Client(
            api_key=config.GEMINI_API_KEY,
            http_options=types.HttpOptions(api_version='v1beta')
        )
    return _gemini_client


# ── Prompt Templates ──────────────────────────────────────────────────────────

SYSTEM_INSTRUCTION = (
    "You are a concise, accurate content summarizer. "
    "You produce structured summaries in the exact format the user requests. "
    "Never add opinions or information not present in the source content."
)

USER_PROMPT = """Summarize the following content:

SOURCE: {source_name} ({source_type})
TITLE: {title}

CONTENT:
{content}

---
Produce the summary in this EXACT format (no deviations, no extra sections, no asterisks for bolding):

<b>OVERVIEW</b>
[2-3 sentence high-level summary]

<b>SUMMARY</b>
➡️ [Key points / takeaways]

➡️ [Key points / takeaways]
......

Keep it factual and dense. Do NOT include the title or the source link in your output. """

URL_PROMPT = """Provide a detailed summary of this video/podcast (a YouTube video, podcast episode, or similar).

Please access the URL and produce a structured summary in this EXACT format (no deviations, no extra sections, no asterisks for bolding):

<b>OVERVIEW</b>
[2-3 sentence high-level summary of the content]

<b>SUMMARY</b>
➡️ [Key points / takeaways]

➡️ [Key points / takeaways]
......

Keep it factual and dense. Do NOT include the title or the source link in your output.

URL: {url}
Title (hint): {title}
Source: {source_name}

IMPORTANT: If you are unable to access or retrieve the content from this URL, respond with exactly the word: CANNOT_ACCESS"""


# ── Core API call ─────────────────────────────────────────────────────────────

def _call_gemini(prompt: str) -> Optional[str]:
    """Send a prompt to Gemini and return the response text."""
    try:
        client = _get_client()
        full_prompt = f"{SYSTEM_INSTRUCTION}\n\n{prompt}"
        response = client.models.generate_content(
            model=config.GEMINI_MODEL,
            contents=full_prompt,
            config={
                "temperature": 0.3,
                "top_p": 0.9,
            }
        )

        # Extract text from response
        text = response.text.strip() if hasattr(response, "text") else ""
        if not text:
            # Try candidates fallback
            if response.candidates:
                text = "".join(part.text for part in response.candidates[0].content.parts).strip()

        if text:
            logger.info("Gemini response: %d chars", len(text))
        return text or None

    except Exception as e:
        logger.error("Gemini API error: %s", e)
        return None


# ── Public API ────────────────────────────────────────────────────────────────

def summarize(
    content: str,
    title: str,
    source_name: str,
    source_type: str,
) -> Optional[str]:
    """
    Summarize content using Gemini.
    Returns formatted summary string or None on failure.
    """
    if not content or len(content.strip()) < 50:
        logger.warning("Content too short to summarize: %d chars", len(content))
        return None

    logger.info(
        "Summarizing '%s' (%d chars) with %s...",
        title[:60], len(content), config.GEMINI_MODEL,
    )

    result = _call_gemini(USER_PROMPT.format(
        source_name=source_name,
        source_type=source_type,
        title=title,
        content=content,
    ))

    if result:
        logger.info("Gemini summary generated: %d chars", len(result))
    else:
        logger.warning("Gemini failed to generate summary for: %s", title)

    return result


def summarize_from_url(
    url: str,
    title: str,
    source_name: str,
    source_type: str,
) -> Optional[str]:
    """
    URL-first summarization: pass the URL directly to Gemini.
    Gemini will attempt to access and summarize the linked content.
    Returns None if Gemini cannot access the URL (caller falls back to Whisper).

    FIXED: Raises ValueError immediately for Spotify episode URLs.
    Spotify audio is DRM-protected; Gemini cannot access it and will hallucinate.
    The correct path is: Whisper transcription → summarize(text=...).
    """
    if not url:
        return None

    # FIXED: Hard guard against Spotify episode URLs being passed here.
    # If this assertion fires it means on_demand.py regressed and is calling
    # Gemini URL-first for a Spotify episode — which must never happen.
    import re as _re
    if _re.search(r"open\.spotify\.com/episode/", url, _re.IGNORECASE):
        raise ValueError(
            "Spotify URLs must be transcribed first. Call summarize(text=...) instead."
        )

    logger.info("Gemini URL-first summarization for '%s'...", title[:60])
    try:
        from google.genai import types
        client = _get_client()
        
        prompt = URL_PROMPT.format(
            url=url,
            title=title,
            source_name=source_name,
        )
        full_prompt = f"{SYSTEM_INSTRUCTION}\n\n{prompt}"
        
        # Decide if we can use multimodal from_uri (YouTube only)
        is_youtube = "youtube.com" in url or "youtu.be" in url
        
        if is_youtube:
            response = client.models.generate_content(
                model=config.GEMINI_MODEL,
                contents=[
                    types.Part.from_uri(
                        file_uri=url,
                        mime_type="video/mp4"
                    ),
                    full_prompt
                ],
                config={"temperature": 0.3, "top_p": 0.9}
            )
        else:
            # For non-YouTube, try plain text prompt (Gemini sometimes still works if it has browsing enabled)
            # or just return None to force Whisper fallback
            response = client.models.generate_content(
                model=config.GEMINI_MODEL,
                contents=full_prompt,
                config={"temperature": 0.3, "top_p": 0.9}
            )
        
        # Extract text from response
        result = response.text.strip() if hasattr(response, "text") else ""
        if not result:
            # Try candidates fallback
            if getattr(response, "candidates", None):
                result = "".join(part.text for part in response.candidates[0].content.parts).strip()
    except Exception as e:
        logger.error("Gemini URL-first summarization error: %s", e)
        return None

    if not result:
        logger.warning("Gemini returned nothing for URL: %s", url)
        return None

    if "CANNOT_ACCESS" in result or len(result.strip()) < 80:
        logger.info("Gemini cannot access URL — will fall back to Whisper: %s", url)
        return None

    logger.info("Gemini URL-first summary OK: %d chars", len(result))
    return result


def check_gemini_health() -> bool:
    """Verify the Gemini API key works with a simple ping."""
    try:
        from google import genai
        from google.genai import types
        if not config.GEMINI_API_KEY:
            return False
        client = genai.Client(
            api_key=config.GEMINI_API_KEY,
            http_options=types.HttpOptions(api_version='v1beta')
        )
        # List models as a lightweight health check
        models = list(client.models.list())
        logger.info("Gemini API OK — %d models available (using %s)", len(models), config.GEMINI_MODEL)
        return True
    except Exception as e:
        logger.error("Gemini health check failed: %s", e)
        return False
