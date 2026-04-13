"""
summarizer/openai_summarizer.py — Summarize content using OpenAI ChatGPT API.

Recommended models (cheapest → best):
  - gpt-4o-mini   ← DEFAULT: best value, very fast, ~₹0.10 per summary
  - gpt-4o        ← Higher quality, ~₹1.20 per summary
  - gpt-3.5-turbo ← Legacy, slightly cheaper than 4o-mini
"""
import logging
from typing import Optional
from openai import OpenAI, APIConnectionError, RateLimitError, APIStatusError
import config

logger = logging.getLogger(__name__)

# Lazy client — created once on first use
_client: Optional[OpenAI] = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        if not config.OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY is not set in your .env file")
        _client = OpenAI(api_key=config.OPENAI_API_KEY)
    return _client


# ── Prompt Template ───────────────────────────────────────────────────────────

SYSTEM_PROMPT = (
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


# ── Core API call ─────────────────────────────────────────────────────────────

def _call_openai(user_message: str) -> Optional[str]:
    """Send message to ChatGPT API and return response text."""
    client = _get_client()
    try:
        response = client.chat.completions.create(
            model=config.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": user_message},
            ],
            temperature=0.3,                       # low = factual, consistent
            top_p=0.9,
        )
        text = response.choices[0].message.content.strip()

        # Log token usage for cost tracking
        usage = response.usage
        cost_usd = _estimate_cost(usage.prompt_tokens, usage.completion_tokens)
        logger.info(
            "OpenAI usage — prompt: %d tokens, completion: %d tokens | "
            "est. cost: $%.4f (₹%.2f)",
            usage.prompt_tokens, usage.completion_tokens,
            cost_usd, cost_usd * 92,   # approx INR
        )
        return text

    except RateLimitError:
        logger.error("OpenAI rate limit hit — wait a moment and retry")
        return None
    except APIConnectionError:
        logger.error("Cannot connect to OpenAI API — check your internet connection")
        return None
    except APIStatusError as e:
        logger.error("OpenAI API error %d: %s", e.status_code, e.message)
        return None
    except Exception as e:
        logger.error("Unexpected OpenAI error: %s", e)
        return None


def _estimate_cost(prompt_tokens: int, completion_tokens: int) -> float:
    """Estimate API call cost in USD based on current model pricing."""
    pricing = {
        # model: (input per 1M tokens, output per 1M tokens) in USD
        "gpt-4o-mini":    (0.15,  0.60),
        "gpt-4o":         (2.50, 10.00),
        "gpt-3.5-turbo":  (0.50,  1.50),
        "gpt-4-turbo":    (10.0, 30.00),
    }
    model_key = config.OPENAI_MODEL.split("-20")[0]  # strip date suffix if any
    input_rate, output_rate = pricing.get(model_key, (0.15, 0.60))
    return (prompt_tokens * input_rate + completion_tokens * output_rate) / 1_000_000


# ── Public API ────────────────────────────────────────────────────────────────

def summarize(
    content: str,
    title: str,
    source_name: str,
    source_type: str,
) -> Optional[str]:
    """
    Summarize content using ChatGPT.
    Returns formatted summary string or None on failure.
    """
    if not content or len(content.strip()) < 50:
        logger.warning("Content too short to summarize: %d chars", len(content))
        return None

    logger.info(
        "Summarizing '%s' (%d chars) with %s...",
        title[:60], len(content), config.OPENAI_MODEL,
    )
    result = _call_openai(USER_PROMPT.format(
        source_name=source_name,
        source_type=source_type,
        title=title,
        content=content,
    ))

    if result:
        logger.info("Summary generated: %d chars", len(result))
    else:
        logger.warning("Failed to generate summary for: %s", title)

    return result


URL_PROMPT = """You are given a direct link to a piece of content (a YouTube video, podcast episode, or similar).

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


def summarize_from_url(
    url: str,
    title: str,
    source_name: str,
    source_type: str,
) -> Optional[str]:
    """
    URL-first summarization strategy: pass the URL directly to ChatGPT.
    ChatGPT will attempt to access and summarize the linked content.
    Returns None if ChatGPT can't access the URL (caller should fall back to Whisper).
    """
    if not url:
        return None

    user_message = URL_PROMPT.format(
        url=url,
        title=title,
        source_name=source_name,
    )

    logger.info("URL-first summarization for '%s' via ChatGPT...", title[:60])
    result = _call_openai(user_message)

    if not result:
        logger.warning("ChatGPT returned nothing for URL: %s", url)
        return None

    # ChatGPT signals it cannot access the content
    if "CANNOT_ACCESS" in result or len(result.strip()) < 80:
        logger.info("ChatGPT cannot access URL — will fall back to Whisper: %s", url)
        return None

    logger.info("URL-first summary OK: %d chars", len(result))
    return result


def check_openai_health() -> bool:
    """Verify the API key works by listing available models."""
    try:
        client = _get_client()
        client.models.list()
        logger.info("OpenAI API connection OK (model: %s)", config.OPENAI_MODEL)
        return True
    except Exception as e:
        logger.error("OpenAI health check failed: %s", e)
        return False
