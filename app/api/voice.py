from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field

from app.security.auth import OwnerDep
from app.voice.base import STTProvider, TTSProvider, VoiceError

router = APIRouter(prefix="/v1", tags=["voice"], dependencies=[OwnerDep])

MAX_AUDIO_BYTES = 10 * 1024 * 1024


class TTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class STTResponse(BaseModel):
    text: str


def _tts(request: Request) -> TTSProvider:
    tts: TTSProvider | None = request.app.state.tts
    if tts is None:
        raise HTTPException(503, "voice is not configured (set FISH_AUDIO_API_KEY)")
    return tts


def _stt(request: Request) -> STTProvider:
    stt: STTProvider | None = request.app.state.stt
    if stt is None:
        raise HTTPException(503, "speech recognition is not configured (set FISH_AUDIO_API_KEY)")
    return stt


@router.post(
    "/tts",
    summary="Speak text in Alfred's voice",
    response_class=Response,
    responses={200: {"content": {"audio/mpeg": {}}, "description": "Audio"}},
)
async def tts(body: TTSRequest, request: Request) -> Response:
    try:
        speech = await _tts(request).synthesize(body.text)
    except VoiceError as exc:
        raise HTTPException(502, f"voice provider error ({exc.code})") from exc
    return Response(content=speech.audio, media_type=speech.media_type)


@router.post("/stt", response_model=STTResponse, summary="Transcribe recorded speech")
async def stt(
    request: Request,
    audio: UploadFile,
    language: Annotated[str, Form(max_length=10)] = "",
) -> STTResponse:
    data = await audio.read(MAX_AUDIO_BYTES + 1)
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "audio too large (max 10 MB)")
    if not data:
        raise HTTPException(422, "empty audio")
    try:
        text = await _stt(request).transcribe(data, audio.content_type or "", language)
    except VoiceError as exc:
        raise HTTPException(502, f"speech recognition error ({exc.code})") from exc
    return STTResponse(text=text)
