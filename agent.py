"""
agent.py — Conversational AI Agent (Qwen3-32B via Groq)

Turns natural language Telegram messages into Blog Empire actions.
The agent understands intent from free-form text — no slash commands needed.

Examples:
  "write a blog about rust programming"  → generate
  "optimise my posts"                    → optimize
  "what are trending topics right now"   → trending
  "show me my stats"                     → stats
  "how is the scheduler doing"           → schedule
  "write about python anyway its a dup"  → generate_force
  "hey whats up"                         → chat (casual reply)
  "explain what you can do"              → help

The agent maintains a short per-user conversation history (last 6 turns)
for context-aware conversations. History resets after 30 minutes of inactivity.
"""

import asyncio
import html as html_mod
import json
import logging
import re
import time
from typing import Optional

from clients.groq_client import GroqClient
from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

groq = GroqClient()

# ---------------------------------------------------------------------------
# Intent types
# ---------------------------------------------------------------------------
INTENTS = {
    "generate":       "Write a new blog post about a topic",
    "generate_force": "Force write a blog post ignoring duplicates",
    "optimize":       "Run SEO optimizer on all low-performing posts",
    "stats":          "Show top blog posts by views",
    "schedule":       "Show scheduler status and next run time",
    "trending":       "Fetch trending topics from HackerNews/Reddit",
    "help":           "Show what the bot can do",
    "chat":           "Casual conversation / general question",
}

# ---------------------------------------------------------------------------
# Per-user conversation history (in-memory, TTL 30 min)
# ---------------------------------------------------------------------------
_histories: dict[int, list[dict]] = {}
_last_seen:  dict[int, float]      = {}
HISTORY_TTL       = 30 * 60   # 30 minutes
MAX_HISTORY_TURNS = 6          # keep last 6 (3 user + 3 assistant) messages


def _get_history(user_id: int) -> list[dict]:
    now = time.time()
    if user_id in _last_seen and (now - _last_seen[user_id]) > HISTORY_TTL:
        _histories.pop(user_id, None)
    _last_seen[user_id] = now
    return _histories.setdefault(user_id, [])


def _push_history(user_id: int, role: str, content: str) -> None:
    h = _get_history(user_id)
    h.append({"role": role, "content": content})
    # Keep last MAX_HISTORY_TURNS messages
    if len(h) > MAX_HISTORY_TURNS:
        h[:] = h[-MAX_HISTORY_TURNS:]


def clear_history(user_id: int) -> None:
    _histories.pop(user_id, None)
    _last_seen.pop(user_id, None)


# ---------------------------------------------------------------------------
# Agent system prompt
# ---------------------------------------------------------------------------
_SYSTEM_PROMPT = """You are GHOST — the AI agent behind Blog Empire, a self-healing autonomous blogging platform.

## Your Capabilities
- Write new blog posts on any tech topic (uses AI research + fact-checking + auto-publishing)
- Optimize existing low-performing posts with self-healing SEO
- Show blog statistics and analytics
- Fetch trending topics from HackerNews, Reddit, and GitHub
- Show and control the daily auto-posting scheduler
- Have natural conversations about strategy, content ideas, etc.

## Responding to User Messages
Analyze the user message carefully and respond with ONLY this JSON (no markdown fences):

{
  "intent": "<one of: generate | generate_force | optimize | stats | schedule | trending | help | chat>",
  "topic": "<extracted topic if intent=generate or generate_force, else null>",
  "niche": "<extracted or inferred niche if intent=generate or generate_force, else null>",
  "reply": "<your conversational response in plain text — friendly, concise, max 2 sentences>"
}

## Intent Classification Rules
- "write/create/make/draft/blog about X" → generate (topic=X)
- "write about X anyway/force/ignore duplicate" → generate_force (topic=X)
- "optimize/fix/improve my posts/SEO" → optimize
- "stats/analytics/views/traffic/performance" → stats
- "schedule/scheduler/next batch/auto post" → schedule
- "trending/what's popular/hot topics/what should I write" → trending
- "help/what can you do/commands/features" → help
- Everything else → chat

## Niche Inference
If the user doesn't specify a niche, infer it from the topic:
- Python/JS/Rust/Go → programming language name
- Docker/K8s/CI-CD → devops
- LLM/GPT/agents/AI → artificial-intelligence
- React/Next/Vue → web-development
- Security/hacking → cybersecurity
- Default → technology

## Tone
Friendly, smart, concise. You're like a brilliant coworker who gets things done.
Never apologize excessively. Be confident but approachable.
"""


# ---------------------------------------------------------------------------
# Main agent entry point
# ---------------------------------------------------------------------------
async def process_message(
    user_message: str,
    user_id: int,
    username: str = "there",
) -> "AgentResult":
    """
    Parse a natural language message and return an AgentResult
    with the detected intent and all extracted parameters.
    """
    logger.info("[Agent] user=%d msg=%r", user_id, user_message[:100])

    # Push user turn to history
    _push_history(user_id, "user", user_message)

    # Build messages: system + history
    history = _get_history(user_id)
    messages = [{"role": "system", "content": _SYSTEM_PROMPT}] + history

    try:
        # Call Groq for intent classification
        raw = await groq._client.chat.completions.create(
            model=groq.model,
            messages=messages,
            temperature=0.3,
            max_tokens=512,
        )
        raw_text = raw.choices[0].message.content or "{}"

        # Strip accidental markdown fences
        raw_text = re.sub(r"^```(?:json)?\n?", "", raw_text.strip())
        raw_text = re.sub(r"\n?```$", "", raw_text.strip())

        parsed = json.loads(raw_text)

    except json.JSONDecodeError:
        # Try to extract JSON from mixed response
        match = re.search(r"\{.*\}", raw_text, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group())
            except Exception:
                parsed = {"intent": "chat", "reply": "Sorry, I had trouble understanding that. Can you rephrase?"}
        else:
            parsed = {"intent": "chat", "reply": "I encountered a glitch. Let me try again — what would you like to do?"}
    except Exception as exc:
        logger.error("[Agent] Groq failed: %s", exc)
        parsed = {"intent": "chat", "reply": f"My AI brain had a hiccup: {str(exc)[:100]}. Try again?"}

    intent    = str(parsed.get("intent", "chat")).lower().strip()
    topic     = str(parsed.get("topic") or "").strip() or None
    niche     = str(parsed.get("niche") or "").strip() or None
    reply     = str(parsed.get("reply") or "").strip()

    # Validate intent
    if intent not in INTENTS:
        intent = "chat"

    # Push assistant reply to history
    _push_history(user_id, "assistant", reply)

    logger.info("[Agent] intent=%s topic=%r niche=%r", intent, topic, niche)
    return AgentResult(intent=intent, topic=topic, niche=niche, reply=reply)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------
class AgentResult:
    __slots__ = ("intent", "topic", "niche", "reply")

    def __init__(
        self,
        intent: str,
        topic: Optional[str],
        niche: Optional[str],
        reply: str,
    ):
        self.intent = intent
        self.topic  = topic
        self.niche  = niche
        self.reply  = reply

    def __repr__(self) -> str:
        return f"AgentResult(intent={self.intent!r} topic={self.topic!r} niche={self.niche!r})"
