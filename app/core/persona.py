"""Loads and renders Alfred's persona from `prompts/alfred_system.md`.

The file is re-read when it changes on disk, so the owner can edit it without a restart.

Rendering is split in two for prompt caching:
- `system_prompt()` is stable across turns (no clock), so the provider can cache it;
- `turn_context()` carries volatile values (current time, output mode) and is sent as a
  per-turn context note after the owner's message.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

OutputMode = Literal["text", "voice"]

_VAR = re.compile(r"\{([a-z_]+)\}")
VOLATILE = {"current_datetime"}

_MODE_HINTS: dict[str, str] = {
    "text": "Output mode: text (a screen). Light Markdown is fine.",
    "voice": (
        "Output mode: voice. Your reply will be spoken aloud. Use plain sentences only: no "
        "Markdown, lists, headings, emoji or URLs. Keep sentences short. Write numbers, dates "
        "and times the way they are said aloud."
    ),
}


@dataclass(frozen=True)
class OwnerInfo:
    name: str
    address_as: str
    profile: str
    timezone: str
    location: str


class Persona:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._mtime: float | None = None
        self._template = ""

    def template(self) -> str:
        mtime = self.path.stat().st_mtime
        if mtime != self._mtime:
            self._template = self.path.read_text(encoding="utf-8")
            self._mtime = mtime
        return self._template

    def system_prompt(self, owner: OwnerInfo, enabled_tools: list[str]) -> str:
        profile_parts = [owner.profile.strip()] if owner.profile.strip() else []
        if owner.address_as:
            profile_parts.append(f'Prefers to be addressed as "{owner.address_as}".')
        values = {
            "owner_name": owner.name or "the owner",
            "timezone": owner.timezone,
            "location": owner.location or "not specified",
            "owner_profile": " ".join(profile_parts) or "nothing yet",
            "enabled_tools": ", ".join(enabled_tools) if enabled_tools else "none",
            "current_datetime": "given in the latest turn context note",
        }
        return _VAR.sub(lambda m: values.get(m.group(1), m.group(0)), self.template()).strip()

    @staticmethod
    def turn_context(now: datetime, mode: OutputMode, extra: list[str] | None = None) -> str:
        lines = [
            f"Current date and time: {now.strftime('%A %d %B %Y, %H:%M')} "
            f"({now.tzname()}, UTC{now.strftime('%z')}).",
            _MODE_HINTS[mode],
        ]
        lines += extra or []
        return "\n".join(lines)
