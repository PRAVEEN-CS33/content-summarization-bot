"""
summarizer/ollama_summarizer.py — Summarize content using a local Ollama LLM.

Recommended models for 16GB RAM:
  - mistral:7b-instruct    (best quality/speed balance)
  - llama3.2:3b            (fastest, lower quality)
  - phi3:mini              (good for short summaries)
"""
import logging
import requests
from typing import Optional
import config

logger = logging.getLogger(__name__)

# ── Prompt Template ───────────────────────────────────────────────────────────

SUMMARY_PROMPT = """You are a concise content summarizer. Read the following content and produce a structured summary.

SOURCE: {source_name} ({source_type})
TITLE: {title}

CONTENT:
{content}

---
Produce a summary in this exact format:

📋 **OVERVIEW**
[2-3 sentence high-level summary of what this content is about]

🔑 **KEY POINTS**
• [Key point 1]
• [Key point 2]
• [Key point 3]
• [Key point 4 if relevant]

💡 **INSIGHTS & TAKEAWAYS**
• [Important insight or actionable takeaway 1]
• [Important insight or actionable takeaway 2]

⏱️ **WORTH YOUR TIME?**
[One sentence: who should watch/listen/read this and why]

Keep the summary factual, dense, and under 300 words. Do not add commentary or opinions beyond what the source says."""


def _call_ollama(prompt: str) -> Optional[str]:
    """Send prompt to local Ollama API, return response text."""
    try:
        resp = requests.post(
            f"{config.OLLAMA_BASE_URL}/api/generate",
            json={
                "model":  config.OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature":  0.3,     # low temp = factual, consistent
                    "num_predict":  config.MAX_SUMMARY_TOKENS,
                    "num_ctx":      4096,    # context window
                    "top_p":        0.9,
                },
            },
            timeout=config.OLLAMA_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("response", "").strip()
    except requests.Timeout:
        logger.error("Ollama request timed out (model: %s)", config.OLLAMA_MODEL)
        return None
    except requests.ConnectionError:
        logger.error(
            "Cannot connect to Ollama at %s — is it running? "
            "Start with: ollama serve",
            config.OLLAMA_BASE_URL,
        )
        return None
    except Exception as e:
        logger.error("Ollama error: %s", e)
        return None


def summarize(
    content: str,
    title: str,
    source_name: str,
    source_type: str,
) -> Optional[str]:
    """
    Summarize `content` using the local Ollama model.
    Returns formatted summary string or None on failure.
    """
    if not content or len(content.strip()) < 50:
        logger.warning("Content too short to summarize: %d chars", len(content))
        return None

    # Truncate content to avoid overwhelming the model context
    truncated = content[:config.MAX_TRANSCRIPT_CHARS]
    if len(content) > config.MAX_TRANSCRIPT_CHARS:
        truncated += "\n\n[Content truncated for length]"

    prompt = SUMMARY_PROMPT.format(
        source_name=source_name,
        source_type=source_type,
        title=title,
        content=truncated,
    )

    logger.info("Summarizing '%s' (%d chars) with %s...", title[:60], len(truncated), config.OLLAMA_MODEL)
    result = _call_ollama(prompt)

    if result:
        logger.info("Summary generated: %d chars", len(result))
    else:
        logger.warning("Failed to generate summary for: %s", title)

    return result


def check_ollama_health() -> bool:
    """Check if Ollama is running and the model is available."""
    try:
        resp = requests.get(f"{config.OLLAMA_BASE_URL}/api/tags", timeout=5)
        models = [m["name"] for m in resp.json().get("models", [])]
        if not any(config.OLLAMA_MODEL in m for m in models):
            logger.warning(
                "Model '%s' not found. Pull it with: ollama pull %s",
                config.OLLAMA_MODEL, config.OLLAMA_MODEL,
            )
            return False
        return True
    except Exception:
        return False
