"""Voice output for a turn: interleaves synthesized `audio` events with the agent's events.

Pipeline: agent tokens → SentenceChunker → TTS (one sentence at a time, in order) → `audio`
events. Text events pass through immediately so captions stay live. The agent's closing
`done` / `state: idle` events are held back until every sentence has been synthesized, so a
client knows that after `done` no more audio will arrive.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
from collections.abc import AsyncIterator

from app.core import events as ev
from app.core.events import Event
from app.voice.base import TTSProvider, VoiceError
from app.voice.chunker import SentenceChunker

log = logging.getLogger(__name__)

_SENTINEL = object()


async def speak_turn(agent_events: AsyncIterator[Event], tts: TTSProvider) -> AsyncIterator[Event]:
    out: asyncio.Queue[Event | object] = asyncio.Queue()
    sentences: asyncio.Queue[str | None] = asyncio.Queue()
    held: list[Event] = []
    conversation_id: str | None = None

    async def pump_agent() -> None:
        nonlocal conversation_id
        chunker = SentenceChunker()
        try:
            async for event in agent_events:
                conversation_id = event.conversation_id
                if event.type == "token":
                    for sentence in chunker.feed(event.data["text"]):
                        await sentences.put(sentence)
                if event.type == "done" or (
                    event.type == "state" and event.data.get("value") == "idle"
                ):
                    held.append(event)
                    continue
                await out.put(event)
            for sentence in chunker.flush():
                await sentences.put(sentence)
        finally:
            await sentences.put(None)

    async def synthesize() -> None:
        index = 0
        failed = False
        while (sentence := await sentences.get()) is not None:
            if failed:
                continue
            try:
                speech = await tts.synthesize(sentence)
            except VoiceError as exc:
                log.warning("TTS failed (%s); continuing as text only", exc.code)
                failed = True
                await out.put(ev.error(exc.code, "Alfred's voice is unavailable right now."))
                continue
            index += 1
            await out.put(
                Event(
                    type="audio",
                    data={
                        "index": index,
                        "format": speech.format,
                        "media_type": speech.media_type,
                        "text": sentence,
                        "data_b64": base64.b64encode(speech.audio).decode(),
                    },
                )
            )

    async def run() -> None:
        try:
            await asyncio.gather(pump_agent(), synthesize())
        finally:
            await out.put(_SENTINEL)

    task = asyncio.create_task(run())
    seq = 0
    try:
        while (item := await out.get()) is not _SENTINEL:
            assert isinstance(item, Event)
            seq += 1
            item.seq, item.conversation_id = seq, item.conversation_id or conversation_id
            yield item
        await task  # surface exceptions from the pipeline
        for item in held:
            seq += 1
            item.seq = seq
            yield item
    finally:
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
