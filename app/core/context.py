"""Context assembly within a token budget.

Order (brief §8): system prompt → owner profile (both in the persona render) → [M3: relevant
memories, conversation summary] → recent turns → per-turn context note.

Turns are never split: a turn is a user message plus everything up to the next user message
(assistant replies, tool calls/results, context notes), so tool calls stay paired with results.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.tokens import estimate_tokens
from app.llm.base import ChatMessage


@dataclass
class ContextWindow:
    system: str
    messages: list[ChatMessage]
    estimated_tokens: int
    dropped_turns: int


def split_turns(history: list[ChatMessage]) -> list[list[ChatMessage]]:
    turns: list[list[ChatMessage]] = []
    for msg in history:
        if msg.role == "user" or not turns:
            turns.append([msg])
        else:
            turns[-1].append(msg)
    return turns


def _msg_tokens(msg: ChatMessage) -> int:
    size = len(msg.text)
    size += sum(len(c.name) + len(str(c.arguments)) for c in msg.tool_calls)
    size += sum(len(r.content) for r in msg.tool_results)
    return estimate_tokens("x" * size) if size else 4


def build_window(
    system: str, history: list[ChatMessage], *, budget_tokens: int, max_turns: int
) -> ContextWindow:
    """Keep the most recent whole turns that fit the budget. The newest turn is always kept."""
    turns = split_turns(history)
    used = estimate_tokens(system)
    kept: list[list[ChatMessage]] = []
    for turn in reversed(turns):
        cost = sum(_msg_tokens(m) for m in turn)
        if kept and (used + cost > budget_tokens or len(kept) >= max_turns):
            break
        kept.append(turn)
        used += cost
    kept.reverse()
    return ContextWindow(
        system=system,
        messages=[m for turn in kept for m in turn],
        estimated_tokens=used,
        dropped_turns=len(turns) - len(kept),
    )
