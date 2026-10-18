"""The assistant: one entry point for every channel (WhatsApp, SMS, USSD shortcuts, web simulator, CLI).

    replies = assistant.handle(phone="254712345678", text="sold 3 bread @ 60")

Order of understanding, cheapest first:
1. Onboarding and pending follow-ups ("what price?", "was this money a sale?", quiz answers)
2. Forwarded mobile-money / bank SMS
3. Rule engine (free, instant, offline)
4. Claude with tools (when configured), for everything else
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass

from . import actions, learning
from .config import Settings, get_settings
from .db import Database, get_db
from .i18n import t
from .mobilemoney.parsers import looks_like_momo, parse_sms
from .money import parse_amount
from .nlu import rules
from .nlu.intents import Intent
from .replies import Reply
from .services import businesses
from .services.businesses import Business
from .voice import Transcriber, get_transcriber

log = logging.getLogger(__name__)


@dataclass
class Inbound:
    phone: str
    text: str | None = None
    audio: bytes | None = None
    audio_mime: str = "audio/ogg"
    channel: str = "whatsapp"
    profile_name: str | None = None
    message_id: str | None = None


class Assistant:
    def __init__(
        self,
        db: Database | None = None,
        settings: Settings | None = None,
        agent: object | None = None,
        transcriber: Transcriber | None = None,
    ):
        self.db = db or get_db()
        self.settings = settings or get_settings()
        self.transcriber = transcriber or get_transcriber(self.settings)
        self._agent = agent
        self._agent_checked = agent is not None

    # -- AI layer ---------------------------------------------------------------------------

    @property
    def agent(self):
        if not self._agent_checked:
            self._agent_checked = True
            if self.settings.llm_enabled and (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
                try:
                    from .nlu.llm import ClaudeAgent

                    self._agent = ClaudeAgent(self.settings.llm_model, self.settings.llm_effort)
                except ImportError:
                    log.warning("anthropic package not installed; running with the rule engine only")
        return self._agent

    # -- entry point ------------------------------------------------------------------------

    def handle(self, phone: str, text: str | None = None, **kwargs) -> list[Reply]:
        return self.handle_inbound(Inbound(phone=phone, text=text, **kwargs))

    def handle_inbound(self, msg: Inbound) -> list[Reply]:
        with self.db.tx() as conn:
            if msg.message_id:
                cur = conn.execute("INSERT OR IGNORE INTO processed_inbound (message_id) VALUES (?)", (msg.message_id,))
                if cur.rowcount == 0:
                    return []  # webhook retry of a message we already handled
            business, created = businesses.get_or_create(conn, msg.phone)
            ctx = actions.Context(conn, business, self.settings, channel=msg.channel)
            replies: list[Reply] = []

            text = (msg.text or "").strip()
            if msg.audio is not None:
                heard = self.transcriber.transcribe(msg.audio, msg.audio_mime, business.language)
                if not heard:
                    return self._finish(conn, ctx, "[voice note]", [Reply(t("voice_unavailable", business.language))])
                text = heard
                replies.append(Reply(t("heard", business.language, text=heard)))

            if created:
                return self._finish(conn, ctx, text, [Reply(t("welcome", business.language))])
            if not text:
                return self._finish(conn, ctx, text, [Reply(t("not_understood", business.language))])

            if not ctx.business.onboarded:
                replies += self._onboarding(ctx, text)
                return self._finish(conn, ctx, text, replies)

            replies += self._understand(ctx, text)
            return self._finish(conn, ctx, text, replies)

    # -- steps ------------------------------------------------------------------------------

    def _onboarding(self, ctx: actions.Context, text: str) -> list[Reply]:
        biz = ctx.business
        lang_choice = rules.parse_command(text)
        if lang_choice and lang_choice.name == "set_language":
            return actions.set_language(ctx, lang_choice) + [Reply(t("welcome", ctx.business.language))]
        if biz.onboarding_step == "ask_name":
            name = re.sub(r"^(?:it'?s|my business is|the name is|jina ni|inaitwa|c'est|na)\s+", "", text, flags=re.I)
            ctx.business = businesses.update(ctx.conn, biz, name=name.strip()[:60] or "My business",
                                             onboarding_step="ask_type")
            return [Reply(t("ask_type", ctx.business.language, name=ctx.business.name))]
        ctx.business = businesses.update(ctx.conn, biz, business_type=text.strip()[:60], onboarding_step="done")
        replies = [Reply(t("onboarded", ctx.business.language, currency=ctx.business.currency))]
        first = learning.get_lesson(self.settings.content_dir, "records-01")
        if first:
            translated = learning.find_lesson(self.settings.content_dir, ctx.business.language, first.id) or first
            replies.append(actions.send_lesson(ctx, translated))
        return replies

    def _understand(self, ctx: actions.Context, text: str) -> list[Reply]:
        pending = ctx.business.pending_action
        if pending:
            handled = self._pending(ctx, pending, text)
            if handled is not None:
                return handled

        if looks_like_momo(text) and parse_sms(text.split("\n\n")[0]) is not None:
            return actions.execute(ctx, Intent("momo_import", {"text": text}))

        intent = rules.parse(text)
        if intent is not None:
            return actions.execute(ctx, intent)

        agent = self.agent
        if agent is not None:
            try:
                history = self._history(ctx)
                result = agent.run(ctx, text, history)
                if result.replies:
                    return result.replies
            except Exception:  # network, quota, model errors: degrade to rules-only help
                log.exception("AI layer failed")
        return [Reply(t("not_understood", ctx.lang))]

    def _pending(self, ctx: actions.Context, pending: dict, text: str) -> list[Reply] | None:
        kind = pending.get("type")
        low = text.lower().strip()
        if kind == "need_price":
            words = low.split()
            amount = parse_amount(text)
            if amount is not None and len(words) <= 4:
                return actions.resume_with_price(ctx, pending, amount)
            businesses.set_pending(ctx.conn, ctx.business, None)
            return None
        if kind == "momo_classify":
            choice = {"1": "momo_sale", "momo_sale": "momo_sale", "sale": "momo_sale", "a sale": "momo_sale",
                      "2": "momo_debt", "momo_debt": "momo_debt", "debt": "momo_debt", "debt payment": "momo_debt",
                      "3": "momo_skip", "momo_skip": "momo_skip", "no": "momo_skip", "personal": "momo_skip",
                      "not business": "momo_skip"}.get(low.split(" ")[0] if low[:1].isdigit() else low)
            if choice:
                return actions.momo_classify(ctx, pending, choice)
            businesses.set_pending(ctx.conn, ctx.business, None)
            return None
        if kind == "confirm_delete":
            if low not in {"yes delete", "yes, delete"}:
                businesses.set_pending(ctx.conn, ctx.business, None)
                return [Reply("👍 Nothing was deleted.")] if low in {"no", "cancel", "hapana", "non"} else None
            return None
        if kind == "quiz":
            return None  # single letters are routed by the rule engine to quiz_answer
        businesses.set_pending(ctx.conn, ctx.business, None)
        return None

    def _history(self, ctx: actions.Context) -> list[dict[str, str]]:
        n = max(self.settings.llm_history_turns, 0) * 2
        if n == 0:
            return []
        rows = ctx.conn.execute(
            "SELECT direction, body FROM messages WHERE business_id = ? ORDER BY id DESC LIMIT ?",
            (ctx.business.id, n),
        ).fetchall()
        return [{"role": "user" if r["direction"] == "in" else "assistant", "content": r["body"][:1500]}
                for r in reversed(rows)]

    def _finish(self, conn, ctx: actions.Context, text: str, replies: list[Reply]) -> list[Reply]:
        if conn.execute("SELECT 1 FROM businesses WHERE id = ?", (ctx.business.id,)).fetchone() is None:
            return replies  # data was just deleted at the owner's request: keep no log
        if text:
            conn.execute("INSERT INTO messages (business_id, direction, channel, body) VALUES (?, 'in', ?, ?)",
                         (ctx.business.id, ctx.channel, text[:4000]))
        for reply in replies:
            if reply.text:
                conn.execute("INSERT INTO messages (business_id, direction, channel, body) VALUES (?, 'out', ?, ?)",
                             (ctx.business.id, ctx.channel, reply.text[:4000]))
        return replies


def business_for(db: Database, phone: str) -> Business | None:
    with db.tx() as conn:
        return businesses.get_by_phone(conn, phone)
