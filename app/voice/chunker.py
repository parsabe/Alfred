"""Splits a streamed reply into speakable sentences as tokens arrive.

The first chunk is released early (at a clause boundary once long enough) so Alfred starts
speaking quickly; later chunks are whole sentences, which sound more natural.
"""

from __future__ import annotations

import re

_ABBREVIATIONS = {
    "mr",
    "mrs",
    "ms",
    "dr",
    "st",
    "sr",
    "jr",
    "prof",
    "vs",
    "etc",
    "e.g",
    "i.e",
    "approx",
    "no",
    "nr",
    "z.b",
    "bzw",
    "ca",
    "u.a",
    "inc",
    "ltd",
}
_END = re.compile(r"[.!?…。！？]+[\"'”’)\]]*(?=\s)")  # noqa: RUF001 (deliberate: CJK and typographic punctuation)
_CLAUSE = re.compile(r"[,;:—–](?=\s)")  # noqa: RUF001
_MARKDOWN = re.compile(r"(\*\*|__|`+|^#+\s*|^\s*[-*•]\s+)", re.MULTILINE)


def clean_for_speech(text: str) -> str:
    text = _MARKDOWN.sub("", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)  # [label](url) → label
    text = re.sub(r"https?://\S+", "", text)
    return re.sub(r"\s+", " ", text).strip()


class SentenceChunker:
    def __init__(self, *, first_min_chars: int = 40, max_chars: int = 280) -> None:
        self.buffer = ""
        self.first_min_chars = first_min_chars
        self.max_chars = max_chars
        self._emitted = 0

    def _split_point(self) -> int:
        """Index just after the first usable sentence end, or 0."""
        for match in _END.finditer(self.buffer):
            end = match.end()
            before = self.buffer[: match.start()]
            word = before.split()[-1].lower().rstrip(".") if before.split() else ""
            if match.group().startswith(".") and (
                word in _ABBREVIATIONS or (word.isdigit() and len(word) <= 2)
            ):
                continue  # "Dr. Smith", "3. item"
            return end
        return 0

    def feed(self, text: str) -> list[str]:
        self.buffer += text
        out: list[str] = []
        while True:
            cut = self._split_point()
            if not cut and self._emitted == 0 and len(self.buffer) >= self.first_min_chars * 2:
                clause = next(
                    (
                        m.end()
                        for m in _CLAUSE.finditer(self.buffer)
                        if m.end() >= self.first_min_chars
                    ),
                    0,
                )
                cut = clause
            if not cut and len(self.buffer) >= self.max_chars:
                space = self.buffer.rfind(" ", 0, self.max_chars)
                cut = space if space > 0 else self.max_chars
            if not cut:
                break
            piece, self.buffer = self.buffer[:cut], self.buffer[cut:].lstrip()
            spoken = clean_for_speech(piece)
            if spoken:
                out.append(spoken)
                self._emitted += 1
        return out

    def flush(self) -> list[str]:
        spoken = clean_for_speech(self.buffer)
        self.buffer = ""
        return [spoken] if spoken else []
