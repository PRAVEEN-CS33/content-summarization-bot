"""
summarizer/gemini_summarizer.py — Summarize content using Google Gemini API.

Mirrors the openai_summarizer interface exactly so callers can swap between them.

Recommended models:
  - gemini-2.0-flash    ← DEFAULT: very fast, free tier generous
  - gemini-1.5-pro      ← Higher quality, larger context window
  - gemini-1.5-flash    ← Fast + cheaper than 1.5-pro
"""
import logging
from typing import Optional
import config

logger = logging.getLogger(__name__)

# Lazy-initialised Gemini client
_gemini_model = None


def _get_model():
    global _gemini_model
    if _gemini_model is None:
        if not config.GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY is not set in your .env file")
        import google.generativeai as genai
        genai.configure(api_key=config.GEMINI_API_KEY)
        _gemini_model = genai.GenerativeModel(
            model_name=config.GEMINI_MODEL,
            generation_config={
                "temperature": 0.3,
                "top_p": 0.9,
            },
        )
    return _gemini_model


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
Produce the summary in this EXACT format (no deviations, no extra sections):

<b>{title}</b>

<b>OVERVIEW</b>
[2-3 sentence high-level summary]

<b>SUMMARY</b>
➡️ [Key points / takeaways]
......

<b>SOURCE LINK</b>
[link]

Keep it factual and dense. """

URL_PROMPT = """You are given a direct link to a piece of content (a YouTube video, podcast episode, or similar).

Please access the URL and produce a structured summary in this EXACT format (no deviations, no extra sections):

<b>{title}</b>

<b>OVERVIEW</b>
[2-3 sentence high-level summary of the content]

<b>SUMMARY</b>
➡️ [Key points / takeaways]
......

<b>SOURCE LINK</b>
[link]

Keep it factual and dense.

URL: {url}
Title (hint): {title}
Source: {source_name}

IMPORTANT: If you are unable to access or retrieve the content from this URL, respond with exactly the word: CANNOT_ACCESS"""


# ── Core API call ─────────────────────────────────────────────────────────────

def _call_gemini(prompt: str) -> Optional[str]:
    """Send a prompt to Gemini and return the response text."""
    try:
        model = _get_model()
        full_prompt = f"{SYSTEM_INSTRUCTION}\n\n{prompt}"
        response = model.generate_content(full_prompt)

        # Extract text from response
        text = response.text.strip() if hasattr(response, "text") else ""
        if not text:
            # Try candidates fallback
            if response.candidates:
                text = response.candidates[0].content.parts[0].text.strip()

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
    """
    if not url:
        return None

    logger.info("Gemini URL-first summarization for '%s'...", title[:60])
    result = _call_gemini(URL_PROMPT.format(
        url=url,
        title=title,
        source_name=source_name,
    ))

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
        import google.generativeai as genai
        if not config.GEMINI_API_KEY:
            return False
        genai.configure(api_key=config.GEMINI_API_KEY)
        # List models as a lightweight health check
        models = list(genai.list_models())
        logger.info("Gemini API OK — %d models available (using %s)", len(models), config.GEMINI_MODEL)
        return True
    except Exception as e:
        logger.error("Gemini health check failed: %s", e)
        return False
