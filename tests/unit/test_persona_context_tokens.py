from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.core.context import build_window, split_turns
from app.core.persona import OwnerInfo, Persona
from app.core.tokens import cost_usd
from app.llm.base import ChatMessage, ToolCall, ToolResult, Usage

OWNER = OwnerInfo(
    name="", address_as="sir", profile="", timezone="Europe/Berlin", location="Berlin"
)


def test_system_prompt_is_stable_and_has_no_clock() -> None:
    persona = Persona(Path("prompts/alfred_system.md"))
    a = persona.system_prompt(OWNER, ["get_datetime"])
    b = persona.system_prompt(OWNER, ["get_datetime"])
    assert a == b
    assert "{" not in a  # every template variable rendered
    assert "the owner" in a
    assert 'addressed as "sir"' in a
    assert "Berlin" in a
    assert "get_datetime" in a
    assert "latest turn context note" in a


def test_turn_context_has_time_and_mode() -> None:
    now = datetime(2026, 11, 2, 23, 5, tzinfo=ZoneInfo("Europe/Berlin"))
    text = Persona.turn_context(now, "voice")
    assert "Monday 02 November 2026, 23:05" in text
    assert "UTC+0100" in text
    assert "spoken aloud" in text
    assert "Light Markdown" in Persona.turn_context(now, "text")


def test_persona_reloads_when_file_changes(tmp_path: Path) -> None:
    path = tmp_path / "p.md"
    path.write_text("Hello {owner_name}")
    persona = Persona(path)
    assert persona.system_prompt(OWNER, []) == "Hello the owner"
    time.sleep(0.01)
    path.write_text("Good evening {owner_name}. {unknown_var}")
    import os

    os.utime(path, (time.time() + 5, time.time() + 5))
    assert persona.system_prompt(OWNER, []) == "Good evening the owner. {unknown_var}"


def _turn(i: int, size: int = 10) -> list[ChatMessage]:
    return [
        ChatMessage(role="user", text=f"u{i}" + "x" * size),
        ChatMessage(role="context", text="ctx"),
        ChatMessage(role="assistant", text="", tool_calls=[ToolCall("c", "t", {})]),
        ChatMessage(role="tool", tool_results=[ToolResult("c", "r")]),
        ChatMessage(role="assistant", text=f"a{i}"),
    ]


def test_turns_are_never_split() -> None:
    history = [m for i in range(5) for m in _turn(i)]
    assert len(split_turns(history)) == 5
    window = build_window("sys", history, budget_tokens=10_000, max_turns=2)
    assert window.dropped_turns == 3
    assert window.messages[0].role == "user" and window.messages[0].text.startswith("u3")
    assert len(window.messages) == 10


def test_budget_drops_oldest_but_keeps_newest() -> None:
    history = [m for i in range(4) for m in _turn(i, size=4000)]
    window = build_window("sys", history, budget_tokens=2500, max_turns=50)
    assert window.messages[0].text.startswith("u2") or window.messages[0].text.startswith("u3")
    tiny = build_window("sys", history, budget_tokens=1, max_turns=50)
    assert tiny.messages[0].text.startswith("u3")  # newest turn always survives


def test_cost() -> None:
    u = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost_usd("claude-opus-5-5", u) == 24.0
    assert cost_usd("gemma3:12b", u) == 0.0
