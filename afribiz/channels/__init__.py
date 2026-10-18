"""Messaging channels. Each one turns provider webhooks into `Inbound` and `Reply` into provider calls."""

from __future__ import annotations

import logging
from typing import Protocol

from ..config import Settings
from ..replies import Reply

log = logging.getLogger(__name__)


class Sender(Protocol):
    name: str

    def send(self, to: str, replies: list[Reply]) -> None: ...


class LogSender:
    """Development sender: prints what would be sent."""

    name = "log"

    def __init__(self) -> None:
        self.sent: list[tuple[str, Reply]] = []

    def send(self, to: str, replies: list[Reply]) -> None:
        for reply in replies:
            self.sent.append((to, reply))
            log.info("-> %s: %s", to, reply.text)


def get_sender(settings: Settings) -> Sender:
    if settings.whatsapp_configured:
        from .whatsapp_cloud import WhatsAppCloudClient

        return WhatsAppCloudClient(settings)
    if settings.twilio_configured:
        from .twilio import TwilioSender

        return TwilioSender(settings)
    return LogSender()


def split_text(text: str, limit: int = 4000) -> list[str]:
    """Split on paragraph boundaries so long reports stay readable across several messages."""
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    current = ""
    for para in text.split("\n"):
        candidate = f"{current}\n{para}" if current else para
        if len(candidate) > limit and current:
            parts.append(current)
            current = para
        else:
            current = candidate
    if current:
        parts.append(current)
    return [p[i:i + limit] for p in parts for i in range(0, len(p), limit)]
