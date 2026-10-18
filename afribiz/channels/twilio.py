"""Twilio (WhatsApp sandbox and SMS). Quick to start with, then move to the Cloud API at scale.

Inbound webhooks are form posts; we answer with TwiML so no extra API call is needed to reply.
"""

from __future__ import annotations

import logging
from xml.sax.saxutils import escape

import httpx

from ..assistant import Inbound
from ..config import Settings
from ..replies import Reply
from . import split_text

log = logging.getLogger(__name__)


def parse_form(form: dict[str, str]) -> Inbound:
    sender = form.get("From", "")
    channel = "whatsapp" if sender.startswith("whatsapp:") else "sms"
    inbound = Inbound(
        phone=sender.replace("whatsapp:", ""),
        text=form.get("ButtonPayload") or form.get("Body", ""),
        channel=channel,
        profile_name=form.get("ProfileName"),
        message_id=form.get("MessageSid"),
    )
    return inbound


def media_of(form: dict[str, str]) -> tuple[str, str] | None:
    if int(form.get("NumMedia", "0") or 0) > 0:
        return form.get("MediaUrl0", ""), form.get("MediaContentType0", "")
    return None


def download(settings: Settings, url: str) -> bytes | None:
    try:
        response = httpx.get(url, auth=(settings.twilio_account_sid, settings.twilio_auth_token),
                             follow_redirects=True, timeout=30)
        response.raise_for_status()
        return response.content
    except httpx.HTTPError:
        log.exception("twilio media download failed")
        return None


def twiml(replies: list[Reply]) -> str:
    limit = 1500  # Twilio caps a WhatsApp/SMS body at 1600 characters
    parts = ["<?xml version=\"1.0\" encoding=\"UTF-8\"?><Response>"]
    for reply in replies:
        if reply.document and reply.document.url:
            parts.append(f"<Message><Body>{escape(reply.document.caption or reply.text)}</Body>"
                         f"<Media>{escape(reply.document.url)}</Media></Message>")
            continue
        text = reply.text
        if reply.buttons:
            text += "\n\n" + " | ".join(f"*{b.id}*" for b in reply.buttons)
        for chunk in split_text(text, limit):
            parts.append(f"<Message>{escape(chunk)}</Message>")
    parts.append("</Response>")
    return "".join(parts)


class TwilioSender:
    name = "twilio"

    def __init__(self, settings: Settings):
        self.settings = settings

    def send(self, to: str, replies: list[Reply]) -> None:
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.settings.twilio_account_sid}/Messages.json"
        sender = self.settings.twilio_from
        whatsapp = sender.startswith("whatsapp:")
        dest = f"whatsapp:+{to.lstrip('+')}" if whatsapp else f"+{to.lstrip('+')}"
        for reply in replies:
            for chunk in split_text(reply.text, 1500):
                data = {"From": sender, "To": dest, "Body": chunk}
                if reply.document and reply.document.url:
                    data["MediaUrl"] = reply.document.url
                response = httpx.post(url, data=data, auth=(self.settings.twilio_account_sid,
                                                            self.settings.twilio_auth_token), timeout=30)
                if response.status_code >= 400:
                    log.error("twilio send failed: %s", response.text[:300])
