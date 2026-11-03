"""Wake word: notices "Alfred" in a transcript and returns the command around it.

Accepted: "Alfred, is the car ready?", "Hey Alfred what's the time", "What's the time, Alfred?",
and near-misses speech recognition produces ("Alfred's", "Elfred", "Al Fred").
Not accepted: the name buried mid-sentence ("I told Alfred about it"), to avoid
answering conversations that merely mention him.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

_FILLERS = {"hey", "hi", "hello", "ok", "okay", "oh", "um", "uh", "so", "yo", "dear", "and"}
_WORD = re.compile(r"[\w']+", re.UNICODE)


@dataclass(frozen=True)
class Wake:
    heard: bool
    command: str  # the transcript without the wake word ("" if only the name was said)


def _norm(word: str) -> str:
    word = word.lower().replace("'s", "").replace("'", "")
    return word


def _similar(word: str, target: str) -> bool:
    return SequenceMatcher(None, word, target).ratio() >= 0.8


def _tidy(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^[\s,.;:!?—–-]+", "", text)  # noqa: RUF001 (dashes are deliberate)
    text = re.sub(r"[\s,;:—–-]+$", "", text)  # noqa: RUF001 (dashes are deliberate)
    return text[:1].upper() + text[1:] if text else ""


def find_wake(transcript: str, name: str = "Alfred") -> Wake:
    target = _norm(name)
    words = list(_WORD.finditer(transcript))
    if not words:
        return Wake(False, "")

    def match_at(i: int) -> tuple[int, int] | None:
        """Character span of the wake word starting at word i (one or two words)."""
        w = _norm(words[i].group())
        if _similar(w, target):
            return words[i].start(), words[i].end()
        if i + 1 < len(words):
            joined = w + _norm(words[i + 1].group())
            if _similar(joined, target):
                return words[i].start(), words[i + 1].end()
        return None

    # at the start, after optional fillers ("hey", "ok", ...)
    i = 0
    while i < len(words) - 1 and _norm(words[i].group()) in _FILLERS and i < 3:
        i += 1
    span = match_at(i) or (match_at(0) if i else None)
    if span:
        return Wake(True, _tidy(transcript[span[1] :]))

    # at the end: "What time is it, Alfred?"
    for j in (len(words) - 1, len(words) - 2):
        if j > 0:
            span = match_at(j)
            if span and not _WORD.search(transcript[span[1] :]):
                return Wake(True, _tidy(transcript[: span[0]]) + transcript[span[1] :].strip(" ,"))
    return Wake(False, transcript.strip())


def strip_wake(transcript: str, name: str = "Alfred") -> str:
    """For follow-ups: drop a leading/trailing name if present, keep everything else."""
    wake = find_wake(transcript, name)
    return wake.command if wake.heard else transcript.strip()
