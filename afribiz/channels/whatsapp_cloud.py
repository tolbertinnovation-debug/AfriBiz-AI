"""WhatsApp Business Platform (Cloud API) by Meta.

Inbound: webhook POSTs with `entry[].changes[].value.messages[]` (text, audio voice notes,
interactive button replies, images/documents with captions). We verify the
X-Hub-Signature-256 header, acknowledge quickly, and process in the background.

Outbound: POST /{phone-number-id}/messages. Free-form messages (text, buttons, documents) are
allowed within 24 hours of the customer's last message; outside that window only approved
template messages can be sent, which is why scheduled jobs check the window first.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from ..assistant import Inbound
from ..config import Settings
from ..replies import Reply
from . import split_text

log = logging.getLogger(__name__)

GRAPH = "https://graph.facebook.com"


@dataclass
class ParsedMessage:
    inbound: Inbound
    media_id: str | None = None
    media_kind: str | None = None  # audio | document | image
    filename: str | None = None


def parse_webhook(payload: dict[str, Any]) -> list[ParsedMessage]:
    out: list[ParsedMessage] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            names = {c.get("wa_id"): (c.get("profile") or {}).get("name") for c in value.get("contacts", [])}
            for msg in value.get("messages", []):
                sender = msg.get("from", "")
                kind = msg.get("type")
                inbound = Inbound(phone=sender, channel="whatsapp", profile_name=names.get(sender),
                                  message_id=msg.get("id"))
                parsed = ParsedMessage(inbound)
                if kind == "text":
                    inbound.text = (msg.get("text") or {}).get("body", "")
                elif kind == "interactive":
                    inter = msg.get("interactive") or {}
                    reply = inter.get("button_reply") or inter.get("list_reply") or {}
                    inbound.text = reply.get("id") or reply.get("title") or ""
                elif kind == "button":  # quick-reply button on a template
                    inbound.text = (msg.get("button") or {}).get("payload") or (msg.get("button") or {}).get("text", "")
                elif kind == "audio":
                    audio = msg.get("audio") or {}
                    parsed.media_id, parsed.media_kind = audio.get("id"), "audio"
                    inbound.audio_mime = audio.get("mime_type", "audio/ogg")
                elif kind == "document":
                    doc = msg.get("document") or {}
                    parsed.media_id, parsed.media_kind = doc.get("id"), "document"
                    parsed.filename = doc.get("filename")
                    inbound.text = doc.get("caption", "")
                elif kind == "image":
                    inbound.text = (msg.get("image") or {}).get("caption", "")
                else:
                    inbound.text = ""
                out.append(parsed)
    return out


class WhatsAppCloudClient:
    name = "whatsapp"

    def __init__(self, settings: Settings, http: httpx.Client | None = None):
        self.settings = settings
        self.http = http or httpx.Client(timeout=30)

    @property
    def _base(self) -> str:
        return f"{GRAPH}/{self.settings.wa_graph_version}"

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.settings.wa_token}"}

    def _post(self, payload: dict[str, Any]) -> None:
        url = f"{self._base}/{self.settings.wa_phone_number_id}/messages"
        response = self.http.post(url, json={"messaging_product": "whatsapp", **payload}, headers=self._headers)
        if response.status_code >= 400:
            log.error("WhatsApp send failed %s: %s", response.status_code, response.text[:500])

    def send(self, to: str, replies: list[Reply]) -> None:
        for reply in replies:
            if reply.document:
                self.send_document(to, reply.document.path, reply.document.filename, reply.document.caption)
                continue
            chunks = split_text(reply.text, 4000)
            for i, chunk in enumerate(chunks):
                last = i == len(chunks) - 1
                if last and reply.buttons and len(chunk) <= 1024:
                    self._post({
                        "to": to, "type": "interactive",
                        "interactive": {
                            "type": "button",
                            "body": {"text": chunk},
                            "action": {"buttons": [
                                {"type": "reply", "reply": {"id": b.id[:256], "title": b.title[:20]}}
                                for b in reply.buttons[:3]
                            ]},
                        },
                    })
                else:
                    self._post({"to": to, "type": "text", "text": {"body": chunk, "preview_url": "http" in chunk}})

    def send_document(self, to: str, path: str, filename: str, caption: str = "") -> None:
        media_id = self.upload_media(path, "application/pdf")
        if media_id:
            self._post({"to": to, "type": "document",
                        "document": {"id": media_id, "filename": filename, "caption": caption}})

    def upload_media(self, path: str, mime: str) -> str | None:
        url = f"{self._base}/{self.settings.wa_phone_number_id}/media"
        with open(path, "rb") as fh:
            response = self.http.post(
                url, headers=self._headers, data={"messaging_product": "whatsapp", "type": mime},
                files={"file": (path.rsplit("/", 1)[-1], fh, mime)},
            )
        if response.status_code >= 400:
            log.error("WhatsApp media upload failed: %s", response.text[:300])
            return None
        return response.json().get("id")

    def download_media(self, media_id: str) -> tuple[bytes, str] | None:
        meta = self.http.get(f"{self._base}/{media_id}", headers=self._headers)
        if meta.status_code >= 400:
            log.error("media lookup failed: %s", meta.text[:300])
            return None
        info = meta.json()
        blob = self.http.get(info["url"], headers=self._headers)
        if blob.status_code >= 400:
            return None
        return blob.content, info.get("mime_type", "application/octet-stream")

    def send_template(self, to: str, template: str, language: str, params: list[str]) -> None:
        """Business-initiated message outside the 24h window (template must be approved in WhatsApp Manager)."""
        self._post({
            "to": to, "type": "template",
            "template": {
                "name": template, "language": {"code": language},
                "components": [{"type": "body", "parameters": [{"type": "text", "text": p} for p in params]}],
            },
        })
