"""What the assistant sends back. Channels translate this into WhatsApp/SMS/USSD/web payloads."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Button:
    id: str
    title: str  # WhatsApp limits reply-button titles to 20 characters


@dataclass
class Document:
    path: str
    filename: str
    caption: str = ""
    url: str | None = None  # public link when the channel needs to fetch it


@dataclass
class Reply:
    text: str
    buttons: list[Button] = field(default_factory=list)
    document: Document | None = None

    @staticmethod
    def of(text: str, *buttons: tuple[str, str]) -> Reply:
        return Reply(text=text, buttons=[Button(i, t[:20]) for i, t in buttons])


def join(*replies: Reply | None) -> list[Reply]:
    return [r for r in replies if r is not None and (r.text or r.document)]
