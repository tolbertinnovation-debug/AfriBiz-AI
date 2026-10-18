"""Voice notes -> text.

Speech-to-text is pluggable because the best engine depends on the market and language:
any server that speaks the OpenAI-compatible `/v1/audio/transcriptions` shape works,
including self-hosted whisper.cpp or faster-whisper servers, and many hosted providers
(some, like Lelapa AI or Spitch, specialise in African languages; check each one's API).
WhatsApp voice notes arrive as OGG/Opus.
"""

from __future__ import annotations

import logging
from typing import Protocol

from .config import Settings

log = logging.getLogger(__name__)

WHISPER_LANG = {"en": "en", "sw": "sw", "fr": "fr", "pcm": "en"}


class Transcriber(Protocol):
    def transcribe(self, audio: bytes, mime_type: str, language: str | None = None) -> str | None: ...


class NoTranscriber:
    def transcribe(self, audio: bytes, mime_type: str, language: str | None = None) -> str | None:
        return None


class HTTPTranscriber:
    def __init__(self, url: str, api_key: str = "", model: str = "whisper-1", timeout: float = 60.0):
        self.url = url
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def transcribe(self, audio: bytes, mime_type: str, language: str | None = None) -> str | None:
        import httpx

        ext = "ogg" if "ogg" in mime_type else (mime_type.split("/")[-1] or "bin")
        data = {"model": self.model}
        if language and language in WHISPER_LANG:
            data["language"] = WHISPER_LANG[language]
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        try:
            response = httpx.post(
                self.url, data=data, files={"file": (f"voice.{ext}", audio, mime_type)}, headers=headers,
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            log.exception("transcription failed")
            return None
        text = (payload.get("text") or "").strip()
        return text or None


def get_transcriber(settings: Settings) -> Transcriber:
    if settings.stt_url:
        return HTTPTranscriber(settings.stt_url, settings.stt_api_key, settings.stt_model)
    return NoTranscriber()
