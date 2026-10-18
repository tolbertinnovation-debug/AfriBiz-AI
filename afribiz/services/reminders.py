"""Chasing customers politely: drafts reminder messages the owner can forward in one tap.

Messages escalate gently with the age of the debt, because in close-knit markets the
relationship matters as much as the money. Drafts come with a wa.me click-to-chat link,
so the reminder goes out from the owner's own WhatsApp (no template approval needed, and
the customer sees a familiar number). Where a WhatsApp template is configured, the
platform can also send directly.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import quote

from ..money import format_money
from ..timeutil import iso, local_today, parse_iso, tz, utcnow
from . import customers as customers_svc
from .businesses import Business
from .customers import Debtor

COOLDOWN_DAYS = 3

TEMPLATES = {
    "en": {
        "gentle": "Hello {name}, hope you are well. This is a friendly reminder from {biz} that your balance is {amount}. "
                  "You can pay whenever convenient this week. Thank you!",
        "firm": "Hello {name}, a reminder from {biz}: your balance of {amount} has been outstanding for {days} days. "
                "Kindly pay by {deadline}. Thank you for your support.",
        "final": "Dear {name}, your balance of {amount} with {biz} is now {days} days overdue. Please settle it by "
                 "{deadline} or call us to agree on a payment plan.",
    },
    "sw": {
        "gentle": "Habari {name}, natumai u mzima. Hii ni kumbusho la upole kutoka {biz} kwamba deni lako ni {amount}. "
                  "Unaweza kulipa wiki hii wakati wowote. Asante!",
        "firm": "Habari {name}, kumbusho kutoka {biz}: deni lako la {amount} lina siku {days}. "
                "Tafadhali lipa kufikia {deadline}. Asante kwa ushirikiano.",
        "final": "Mpendwa {name}, deni lako la {amount} kwa {biz} limechelewa siku {days}. Tafadhali lipa kufikia "
                 "{deadline} au tupigie tukubaliane mpango wa malipo.",
    },
    "fr": {
        "gentle": "Bonjour {name}, petit rappel amical de {biz} : votre solde est de {amount}. "
                  "Vous pouvez régler quand cela vous convient cette semaine. Merci !",
        "firm": "Bonjour {name}, rappel de {biz} : votre solde de {amount} est impayé depuis {days} jours. "
                "Merci de régler avant le {deadline}.",
        "final": "Cher/Chère {name}, votre solde de {amount} chez {biz} est en retard de {days} jours. Merci de régler "
                 "avant le {deadline} ou de nous appeler pour convenir d'un échéancier.",
    },
    "pcm": {
        "gentle": "Hello {name}, how body? Na small reminder from {biz} say your balance na {amount}. "
                  "You fit pay anytime this week. Thank you!",
        "firm": "Hello {name}, reminder from {biz}: your balance of {amount} don stay {days} days. "
                "Abeg pay before {deadline}. Thank you.",
        "final": "{name}, your balance of {amount} for {biz} don pass {days} days. Abeg settle am before {deadline} "
                 "or call us make we agree how you go take pay small small.",
    },
}


@dataclass
class ReminderDraft:
    debtor: Debtor
    days_outstanding: int
    tone: str
    message: str
    link: str | None  # wa.me click-to-chat link, when we know the customer's number
    recently_reminded: bool


def _tone(days: int) -> str:
    if days < 14:
        return "gentle"
    if days < 45:
        return "firm"
    return "final"


def draft_for(conn: sqlite3.Connection, business: Business, debtor: Debtor) -> ReminderDraft:
    today = local_today(business.timezone)
    days = 0
    if debtor.oldest_unpaid:
        days = max((today - parse_iso(debtor.oldest_unpaid).astimezone(tz(business.timezone)).date()).days, 0)
    tone = _tone(days)
    deadline = (today + timedelta(days=3 if tone != "gentle" else 7)).strftime("%d %b")
    lang = business.language if business.language in TEMPLATES else "en"
    message = TEMPLATES[lang][tone].format(
        name=debtor.customer.name.split()[0] if debtor.customer.name else "",
        biz=business.display_name,
        amount=format_money(debtor.balance, business.currency),
        days=days,
        deadline=deadline,
    )
    link = f"https://wa.me/{debtor.customer.phone}?text={quote(message)}" if debtor.customer.phone else None
    since = iso(utcnow() - timedelta(days=COOLDOWN_DAYS))
    recent = conn.execute(
        "SELECT 1 FROM reminders WHERE customer_id = ? AND created_at >= ? LIMIT 1", (debtor.customer.id, since)
    ).fetchone()
    return ReminderDraft(debtor, days, tone, message, link, recent is not None)


def drafts(conn: sqlite3.Connection, business: Business, customer_name: str | None = None) -> list[ReminderDraft]:
    debtors = customers_svc.debtors(conn, business)
    if customer_name:
        wanted = customers_svc.find(conn, business, customer_name)
        debtors = [d for d in debtors if wanted and d.customer.id == wanted.id]
    return [draft_for(conn, business, d) for d in debtors]


def log(conn: sqlite3.Connection, business: Business, draft: ReminderDraft, channel: str) -> None:
    conn.execute(
        "INSERT INTO reminders (business_id, customer_id, amount, message, channel) VALUES (?, ?, ?, ?, ?)",
        (business.id, draft.debtor.customer.id, draft.debtor.balance, draft.message, channel),
    )
