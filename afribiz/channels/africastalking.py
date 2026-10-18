"""Africa's Talking: USSD menus and two-way SMS, for owners on basic feature phones.

USSD callback (form post): sessionId, serviceCode, phoneNumber, text. `text` holds every answer
in the session joined with '*' (empty on the first request). Responses start with "CON "
(more input expected) or "END " (session over). Screens are kept under ~160 characters.
"""

from __future__ import annotations

import logging

import httpx

from .. import actions
from ..assistant import Assistant
from ..config import Settings
from ..money import format_money, parse_amount, to_minor
from ..nlu.intents import Intent, LineSpec
from ..services import businesses, credit, customers, inventory, reports

log = logging.getLogger(__name__)

MENU = {
    "en": "CON AfriBiz: {name}\n1. Today's summary\n2. Record sale\n3. Record expense\n4. Who owes me\n"
          "5. Low stock\n6. Credit score\n7. Business tip",
    "sw": "CON AfriBiz: {name}\n1. Muhtasari wa leo\n2. Rekodi mauzo\n3. Rekodi gharama\n4. Wanaonidai\n"
          "5. Mzigo unaoisha\n6. Alama ya mkopo\n7. Ushauri",
    "fr": "CON AfriBiz: {name}\n1. Bilan du jour\n2. Vente\n3. Dépense\n4. Qui me doit\n5. Stock bas\n"
          "6. Score crédit\n7. Conseil",
}


def _menu(lang: str, name: str) -> str:
    return (MENU.get(lang) or MENU["en"]).format(name=name[:20])


def handle_ussd(assistant: Assistant, phone: str, text: str) -> str:
    steps = [s for s in (text or "").split("*")] if text else []
    with assistant.db.tx() as conn:
        business, _ = businesses.get_or_create(conn, phone)
        if not business.onboarded:
            if not steps:
                return "CON Welcome to AfriBiz!\nEnter your business name:"
            businesses.update(conn, business, name=steps[0][:60], business_type="ussd", onboarding_step="done")
            return f"END Welcome {steps[0][:30]}! Dial again to use AfriBiz, or chat with us on WhatsApp."
        ctx = actions.Context(conn, business, assistant.settings, channel="ussd")
        money = ctx.money
        if not steps:
            return _menu(business.language, business.display_name)
        choice = steps[0]
        if choice == "1":
            s = reports.summary(conn, business, "today")
            return (f"END Today: sales {money(s.sales)}, expenses {money(s.expenses)}, "
                    f"profit {money(s.net_profit)}. Owed to you: {money(s.receivables)}")
        if choice == "2":
            if len(steps) == 1:
                return "CON Enter amount received:"
            if len(steps) == 2:
                return "CON What was sold? (e.g. 2 bread)"
            amount = parse_amount(steps[1])
            if amount is None:
                return "END Invalid amount. Please try again."
            spec = _line_from(steps[2])
            spec.price, spec.price_kind = amount, "total"
            actions.execute(ctx, Intent("record_sale", {"lines": [spec], "method": "cash"}))
            return f"END Sale of {money(to_minor(amount))} recorded. Thank you!"
        if choice == "3":
            if len(steps) == 1:
                return "CON Enter amount spent:"
            if len(steps) == 2:
                return "CON What was it for? (e.g. transport)"
            amount = parse_amount(steps[1])
            if amount is None:
                return "END Invalid amount. Please try again."
            actions.execute(ctx, Intent("record_expense", {"amount": amount, "description": steps[2]}))
            return f"END Expense of {money(to_minor(amount))} recorded."
        if choice == "4":
            rows = customers.debtors(conn, business)[:5]
            if not rows:
                return "END Nobody owes you money."
            lines = [f"{d.customer.name[:14]}: {format_money(d.balance, business.currency)}" for d in rows]
            return "END Owed to you:\n" + "\n".join(lines)
        if choice == "5":
            low = inventory.low_stock(conn, business)[:6]
            if not low:
                return "END Nothing is running low."
            return "END Low stock:\n" + "\n".join(f"{i.name[:16]}: {i.quantity:g}" for i in low)
        if choice == "6":
            p = credit.build_profile(conn, business)
            if not p.sufficient_data:
                return "END Not enough mobile money history yet. Forward your M-PESA/MoMo SMS to our number."
            return f"END Credit score: {p.score}/100 (grade {p.grade}). Affordable loan: {money(p.suggested_loan)}"
        if choice == "7":
            replies = actions.execute(ctx, Intent("lesson", {}))
            body = replies[0].text if replies else ""
            first_para = [ln for ln in body.split("\n") if ln and not ln.startswith(("📘", "_"))][:1]
            return "END " + (first_para[0][:170] if first_para else "Record every sale today!")
        return "END Invalid choice."


def _line_from(text: str) -> LineSpec:
    from ..nlu.rules import parse_line

    spec = parse_line(text) or LineSpec(name=text or "item")
    return LineSpec(name=spec.name, quantity=spec.quantity)


def send_sms(settings: Settings, to: str, message: str) -> None:
    if not settings.africastalking_configured:
        log.info("SMS (not sent, Africa's Talking not configured) -> %s: %s", to, message)
        return
    host = "api.sandbox.africastalking.com" if settings.at_username == "sandbox" else "api.africastalking.com"
    data = {"username": settings.at_username, "to": f"+{to.lstrip('+')}", "message": message}
    if settings.at_sender_id:
        data["from"] = settings.at_sender_id
    response = httpx.post(f"https://{host}/version1/messaging", data=data,
                          headers={"apiKey": settings.at_api_key, "Accept": "application/json"}, timeout=30)
    if response.status_code >= 400:
        log.error("Africa's Talking SMS failed: %s", response.text[:300])
