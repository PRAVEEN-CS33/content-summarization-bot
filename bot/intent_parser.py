"""
bot/intent_parser.py — Multi-turn conversational intent parser using Gemini.

Now accepts full conversation history so the model understands follow-ups
like "remove it", "add it back", "change that to 9pm", etc.
"""
import json
import logging
import re
from typing import Optional, List
import config

logger = logging.getLogger(__name__)

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
            system_instruction=SYSTEM_PROMPT,
            generation_config={
                "temperature": 0.2,
                "top_p": 0.9,
                "max_output_tokens": 200,
            },
        )
    return _gemini_model


def _build_contents(history: Optional[List[dict]], user_message: str) -> list:
    contents = []
    if history:
        for msg in history:
            role = msg.get("role")
            text = msg.get("content", "")
            if not text:
                continue
            if role == "assistant":
                role = "model"
            if role not in ("user", "model"):
                continue
            contents.append({
                "role": role,
                "parts": [{"text": text}],
            })

    contents.append({
        "role": "user",
        "parts": [{"text": user_message}],
    })
    return contents


def _parse_json(raw: str) -> Optional[dict]:
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None


SYSTEM_PROMPT = """You are NaradaAI, a smart personal content summarization assistant running on Telegram.

You help the user manage their content subscriptions and get AI-powered summaries.
You are conversational, friendly, and concise. You remember context across messages.

CAPABILITIES:
- Subscribe to YouTube channels, podcasts, news topics (Google Alerts)
- Remove subscriptions
- List current subscriptions
- Summarize any YouTube video, podcast, or article URL instantly
- Send today's or unread summaries
- Change the daily digest delivery time
- Fetch fresh content on demand
- Answer questions about what you can do

RESPONSE FORMAT:
You must ALWAYS respond with a JSON object. Two possible formats:

1. ACTION — when you know exactly what to do:
{
  "type": "action",
  "action": "<action_name>",
  ... action-specific fields ...
  "reply": "<friendly conversational message to show the user>"
}

2. CLARIFY — when you need more info before acting:
{
  "type": "clarify",
  "reply": "<friendly question to ask the user>"
}

3. CHAT — for general conversation, greetings, or when no action is needed:
{
  "type": "chat",
  "reply": "<friendly response>"
}

AVAILABLE ACTIONS:

add_source:
{ "type":"action", "action":"add_source", "source_type":"youtube|podcast|topic", "query":"<name or URL>", "reply":"..." }

remove_source:
{ "type":"action", "action":"remove_source", "query":"<name or ID>", "reply":"..." }

list_sources:
{ "type":"action", "action":"list_sources", "reply":"..." }

get_summary:
{ "type":"action", "action":"get_summary", "period":"today|week|unsent", "reply":"..." }

summarize_url:
{ "type":"action", "action":"summarize_url", "url":"<url>", "reply":"..." }

set_schedule:
{ "type":"action", "action":"set_schedule", "hour":<0-23>, "reply":"..." }

trigger_fetch:
{ "type":"action", "action":"trigger_fetch", "reply":"..." }

status:
{ "type":"action", "action":"status", "reply":"..." }

INTENT DETECTION RULES:
- "add", "subscribe", "follow", "track", "monitor" → add_source
- "remove", "delete", "unsubscribe", "stop following" → remove_source
- "list", "show", "what am I", "subscriptions" → list_sources
- "summary", "digest", "what's new", "catch me up" → get_summary
- Any URL alone or "summarize this" → summarize_url
- "change time", "send at", "deliver at X" → set_schedule
- "fetch", "check now", "refresh", "any new" → trigger_fetch
- Greetings, thanks, casual chat → chat type

SOURCE TYPE DETECTION for add_source:
- youtube.com, youtu.be, @handle, "YouTube", "channel" → youtube
- "podcast", "show", "episode", spotify, apple podcasts → podcast
- "news about", "topic", "alert", "keyword", RSS URL → topic
- Ambiguous name without context → ask to clarify

TIME CONVERSION for set_schedule:
- "9pm" / "21:00" → 21
- "8am" / "08:00" → 8
- "noon" / "12pm" → 12
- "midnight" → 0

CONTEXT AWARENESS:
- "it", "that", "this" → refer to the last mentioned source
- "add it back" → re-add the last removed source
- "actually X" → override/change the previous action
- "yes" / "no" → respond to your last clarification question

RULES:
- Return ONLY valid JSON. No markdown, no explanation outside the JSON.
- Always include a "reply" field with a warm, human response.
- Keep replies short (1-2 sentences max).
- Never say "I cannot" — if unclear, ask a clarifying question instead.
- Use emojis naturally but sparingly.
"""


def parse_intent(user_message: str, history: List[dict] = None) -> dict:
    """
    Parse user message with full conversation history for context.
    Returns structured intent dict.
    """
    # Fast-path: bare URL with no other text
    stripped = user_message.strip()
    url_only = re.match(r"^https?://[^\s]+$", stripped)
    if url_only:
        return {
            "type":   "action",
            "action": "summarize_url",
            "url":    stripped,
            "reply":  "On it! Let me process that for you.",
        }

    try:
        model    = _get_model()
        contents = _build_contents(history, user_message)
        response = model.generate_content(contents)
        raw      = response.text.strip() if hasattr(response, "text") else ""
        intent   = _parse_json(raw) if raw else None
        if not intent:
            raise ValueError("Gemini response was not valid JSON")
        logger.info("Intent: '%s' → %s", user_message[:60], intent.get("action", intent.get("type")))
        return intent

    except json.JSONDecodeError as e:
        logger.error("Intent JSON error: %s", e)
    except Exception as e:
        logger.error("Intent parser error: %s", e)

    return {
        "type":  "chat",
        "reply": "Sorry, something went wrong on my end. Could you rephrase that?",
    }
