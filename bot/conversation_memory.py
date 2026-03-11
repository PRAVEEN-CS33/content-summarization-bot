"""
bot/conversation_memory.py — Per-user conversation history store.

Keeps last N messages in memory so the LLM has full context
across multiple turns. Enables:
  - Clarification ("is that YouTube or podcast?")
  - Follow-up actions ("remove it", "add it back")
  - Context-aware responses ("actually change that to 9am")
"""
from collections import deque
from typing import List, Dict
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

# Max messages to keep per user (system + user + assistant turns)
MAX_HISTORY = 20

# Global store: { chat_id: deque of message dicts }
_histories: Dict[str, deque] = {}


def get_history(chat_id: str) -> List[dict]:
    """Return conversation history for a user as list of message dicts."""
    return list(_histories.get(str(chat_id), deque()))


def add_message(chat_id: str, role: str, content: str):
    """Append a message to the user's conversation history."""
    cid = str(chat_id)
    if cid not in _histories:
        _histories[cid] = deque(maxlen=MAX_HISTORY)
    _histories[cid].append({
        "role":    role,
        "content": content,
        "time":    datetime.now().isoformat(),
    })


def clear_history(chat_id: str):
    """Reset conversation (e.g. after /start)."""
    _histories.pop(str(chat_id), None)


def get_message_dicts(chat_id: str) -> List[dict]:
    """Return history in chat message format (role + content only)."""
    return [
        {"role": m["role"], "content": m["content"]}
        for m in get_history(chat_id)
    ]
