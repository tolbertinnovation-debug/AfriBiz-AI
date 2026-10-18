"""Rule-based understanding of the most common things owners type (English, Swahili, French, Pidgin).

It is free, instant and works offline, and it handles most daily messages. Anything it is
unsure about is passed to the AI layer (when configured), so the rules only need to be
*precise*, not exhaustive: returning None is always safe.
"""

from __future__ import annotations

import re
from decimal import Decimal

from ..money import find_amounts, parse_amount
from .intents import Intent, LineSpec

UNITS = (
    "bags?|kgs?|kilos?|crates?|pcs|pieces?|packets?|pkts?|litres?|liters?|ltrs?|bottles?|loaves|loaf|dozens?|"
    "sacks?|tins?|cartons?|bundles?|cups?|plates?|sets?|pairs?|meters?|metres?|yards?|boxes|box|trays?|"
    "basins?|buckets?|rolls?|sachets?|units?|tubers?|heaps?|debes?|gorogoro|mudu|paint|congo"
)
_UNIT_RE = re.compile(rf"^(?:{UNITS})$", re.I)

SALE_VERBS = (
    r"(?:i\s+)?(?:have\s+|just\s+|don\s+|done\s+)?(?:sold|sell|sale|sales)|nimeuza|niliuza|tumeuza|nauza|"
    r"(?:j'ai\s+)?vendu|vente|a\s+vendu"
)
BUY_VERBS = (
    r"(?:i\s+)?(?:have\s+|just\s+|don\s+)?(?:bought|buy|purchased|restock(?:ed)?)|nimenunua|nilinunua|tumenunua|"
    r"(?:j'ai\s+)?achet[ée]|achat|add(?:ed)?\s+stock|stock\s+in|received\s+stock"
)
SPEND_VERBS = (
    r"(?:i\s+)?(?:have\s+|just\s+|don\s+)?(?:spent|spend|expense|expenses|paid\s+for|pay\s+for)|nimetumia|nimelipia|"
    r"nimelipa|tumetumia|(?:j'ai\s+)?d[ée]pens[ée]|d[ée]pense|(?:j'ai\s+)?pay[ée]"
)
DRAW_VERBS = (
    r"(?:i\s+)?(?:took|take|taken|withdrew|don\s+take|carry)|nimechukua|nilichukua|(?:j'ai\s+)?pris"
)
CREDIT_WORDS = (
    r"on\s+credit|for\s+credit|credit|on\s+loan|kwa\s+deni|deni|mkopo|à\s+crédit|a\s+credit|crédit|"
    r"owing|will\s+pay\s+later|go\s+pay\s+later|later"
)
PAY_IN_VERBS = r"paid|has\s+paid|have\s+paid|don\s+pay|amelipa|alilipa|ameleta|a\s+payé|a\s+paye|pay\s+me|repaid|settled"

PERIODS = {
    "today": "today", "leo": "today", "aujourd'hui": "today", "day": "today", "daily": "today",
    "yesterday": "yesterday", "jana": "yesterday", "hier": "yesterday",
    "week": "week", "wiki": "week", "semaine": "week", "weekly": "week",
    "month": "month", "mwezi": "month", "mois": "month", "monthly": "month",
    "all": "all", "total": "all",
}

_NUM = r"\d+(?:[.,]\d+)?"


def _clean(text: str) -> str:
    text = text.replace("’", "'").replace("’", "'")
    text = re.sub(r"[!?;]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _num(value: str) -> float:
    return float(value.replace(",", "."))


def _strip_unit(words: list[str]) -> tuple[list[str], str | None]:
    if len(words) > 1 and _UNIT_RE.match(words[0]):
        unit = words[0].lower()
        rest = words[1:]
        if rest and rest[0].lower() in {"of", "ya", "de", "za"}:
            rest = rest[1:]
        return rest, unit
    return words, None


_LINE_RE = re.compile(
    rf"^(?:(?P<qty>{_NUM})\s*(?:x\s*)?)?(?P<item>.+?)"
    rf"(?:\s*(?P<marker>@|at|each|per|kwa|for|à|a|x|=|of)?\s*(?P<price>(?:ksh|kes|sh|tsh|ush|ngn|n|₦|ghs|gh₵|cfa|fcfa|rwf|r|k)?\.?\s?{_NUM}\s?(?:k|/=|/-)?)"
    rf"(?:\s*(?P<each>each|per\s+\w+|kila\s+moja|chacun|pièce))?)?$",
    re.I,
)


def parse_line(segment: str) -> LineSpec | None:
    """'3 bags rice @ 1500' -> LineSpec('rice', 3, 1500, 'unit', 'bags')."""
    seg = segment.strip(" ,.-")
    if not seg:
        return None
    # "rice x3" / "rice 3pcs" quantity after item: normalise "item x3 @ 50"
    m_after = re.match(rf"^(?P<item>[^\d@]+?)\s*x\s*(?P<qty>{_NUM})\b(?P<rest>.*)$", seg, re.I)
    if m_after:
        seg = f"{m_after.group('qty')} {m_after.group('item')} {m_after.group('rest')}".strip()
    m = _LINE_RE.match(seg)
    if not m:
        return None
    item_words = m.group("item").split()
    item_words, unit = _strip_unit(item_words)
    item = " ".join(item_words).strip(" ,.-")
    item = re.sub(r"^(?:of|ya|de|za)\s+", "", item, flags=re.I)
    if not item or re.fullmatch(r"[\d\s.,]+", item):
        return None
    qty = _num(m.group("qty")) if m.group("qty") else 1.0
    # Swahili and many local languages put the number after the noun: "mkate 3 @ 60", "soda 20 kwa 800".
    tail = re.match(rf"^(?P<item>.*\D)\s+(?P<qty>{_NUM})$", item)
    if tail and not m.group("qty") and m.group("price"):
        item, qty = tail.group("item").strip(), _num(tail.group("qty"))
    price = parse_amount(m.group("price")) if m.group("price") else None
    marker = (m.group("marker") or "").lower()
    if m.group("each") or marker in {"@", "at", "each", "per", "à", "a", "x"}:
        kind = "unit"
    elif marker in {"for", "kwa", "=", "of"}:
        kind = "total"
    else:
        kind = "bare"
    return LineSpec(name=item, quantity=qty, price=price, price_kind=kind, unit=unit)


def _split_lines(body: str) -> list[str]:
    return [p for p in re.split(r"\s*(?:,|;|\+|&|\band\b|\bna\b|\bet\b|\bplus\b)\s*", body) if p.strip()]


def _extract_customer(body: str) -> tuple[str, str | None]:
    """Pull 'to Mary' / 'kwa Mary' / 'à Marie' out of a sale message."""
    m = re.search(
        r"\b(?:to|for|kwa|à|give)\s+(?P<name>[A-Za-zÀ-ÿ'][A-Za-zÀ-ÿ' .-]*?)"
        rf"(?=\s+(?:{CREDIT_WORDS})\b|\s+(?:paid|deposit|alilipa|amelipa|due)\b|\s*,|\s*$)",
        body, re.I,
    )
    if not m:
        return body, None
    name = m.group("name").strip()
    if re.fullmatch(rf"(?:{CREDIT_WORDS})", name, re.I) or not re.search(r"[A-Za-z]", name):
        return body, None
    return (body[: m.start()] + body[m.end():]).strip(), name


def _extract_paid(body: str) -> tuple[str, Decimal | None]:
    m = re.search(rf"\b(?:paid|deposit|alilipa|amelipa|advance|a\s+payé|don\s+pay)\s+(?P<amt>[^\s,]*{_NUM}\S*)", body, re.I)
    if not m:
        return body, None
    return (body[: m.start()] + body[m.end():]).strip(), parse_amount(m.group("amt"))


def _extract_method(body: str) -> tuple[str, str | None]:
    m = re.search(r"\b(?:via|by|kwa|through|par)?\s*(m-?pesa|mpesa|momo|mobile money|airtel(?: money)?|bank|transfer|"
                  r"pos|card|cash|taslimu|orange money|wave|opay|moniepoint|palmpay)\b", body, re.I)
    if not m:
        return body, None
    raw = m.group(1).lower()
    method = "cash" if raw in {"cash", "taslimu"} else ("bank" if raw in {"bank", "transfer", "pos", "card"} else "mobile_money")
    return (body[: m.start()] + body[m.end():]).strip(), method


def parse_sale(text: str) -> Intent | None:
    m = re.match(rf"^(?:{SALE_VERBS})\b\s*(?P<body>.*)$", text, re.I)
    if not m:
        return None
    body = m.group("body").strip(" :-")
    if not body:
        return None
    on_credit = bool(re.search(rf"\b(?:{CREDIT_WORDS})\b", body, re.I))
    body, paid = _extract_paid(body)
    body, method = _extract_method(body)
    body, customer = _extract_customer(body)
    body = re.sub(rf"\b(?:{CREDIT_WORDS})\b", " ", body, flags=re.I).strip(" ,.")
    total = None
    mt = re.search(rf"\b(?:total|jumla|all\s+for)\s*(?P<amt>\S*{_NUM}\S*)\s*$", body, re.I)
    if mt:
        total = parse_amount(mt.group("amt"))
        body = body[: mt.start()].strip(" ,")
    lines = [line for seg in _split_lines(body) if (line := parse_line(seg))]
    if not lines:
        return None
    return Intent("record_sale", {
        "lines": lines, "customer": customer, "on_credit": on_credit, "amount_paid": paid,
        "method": method or "cash", "total": total,
    })


def parse_purchase(text: str) -> Intent | None:
    m = re.match(rf"^(?:{BUY_VERBS})\b\s*(?P<body>.*)$", text, re.I)
    if not m:
        return None
    body = m.group("body").strip(" :-")
    supplier = None
    ms = re.search(r"\bfrom\s+(?P<s>[A-Za-z][\w' .-]*?)\s*$", body, re.I)
    if ms:
        supplier = ms.group("s").strip()
        body = body[: ms.start()].strip()
    sell = None
    mp = re.search(rf"\b(?:sell(?:ing)?\s*(?:at|price)?|selling\s+price|kuuza)\s*(?P<amt>\S*{_NUM}\S*)", body, re.I)
    if mp:
        sell = parse_amount(mp.group("amt"))
        body = (body[: mp.start()] + body[mp.end():]).strip(" ,")
    body, method = _extract_method(body)
    line = parse_line(body)
    if line is None:
        return None
    return Intent("record_purchase", {"line": line, "sell_price": sell, "supplier": supplier, "method": method or "cash"})


def parse_draw(text: str) -> Intent | None:
    m = re.match(rf"^(?:{DRAW_VERBS})\b\s*(?P<body>.*)$", text, re.I)
    if not m:
        return None
    amount = parse_amount(m.group("body"))
    if amount is None:
        return None
    return Intent("record_expense", {"amount": amount, "description": _squash(_remove_first_amount(m.group("body"))) or "personal",
                                     "personal": True})


def parse_expense(text: str) -> Intent | None:
    m = re.match(rf"^(?:{SPEND_VERBS})\b\s*(?P<body>.*)$", text, re.I)
    if m:
        body = m.group("body")
        amount = parse_amount(body)
        if amount is None:
            return None
        desc = re.sub(r"\b(?:on|for|kwa|pour|sur)\b", " ", _remove_first_amount(body), flags=re.I)
        return Intent("record_expense", {"amount": amount, "description": _squash(desc) or "expense"})
    # "rent 5000", "transport 200", "kodi 3000", "paid rent 300"
    from ..services.ledger import EXPENSE_CATEGORIES

    m2 = re.match(rf"^(?:paid\s+|pay\s+)?(?P<word>[a-zA-Z][a-zA-Z ]{{1,25}}?)\s+(?P<amt>\S*{_NUM}\S*)$", text, re.I)
    if m2:
        word = m2.group("word").lower().strip()
        for words in EXPENSE_CATEGORIES.values():
            if any(word == w or word.startswith(w + " ") or word.endswith(" " + w) for w in words):
                return Intent("record_expense", {"amount": parse_amount(m2.group("amt")), "description": word})
    return None


def _remove_first_amount(text: str) -> str:
    return re.sub(rf"(?:ksh|kes|sh|ngn|n|₦|ghs|cfa|fcfa|ush|tsh)?\.?\s?{_NUM}\s?(?:k|/=|/-)?", " ", text, count=1, flags=re.I)


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip(" ,.-")


def parse_payment(text: str) -> Intent | None:
    # "Mary paid 300", "Juma amelipa 200", "Emeka don pay 500 via opay"
    m = re.match(rf"^(?P<name>[A-Za-zÀ-ÿ][\w' .-]*?)\s+(?:{PAY_IN_VERBS})\b\s*(?P<rest>.*)$", text, re.I)
    if m and not re.match(rf"^(?:{SPEND_VERBS}|{SALE_VERBS}|{BUY_VERBS}|i|we|nime|ni)$", m.group("name"), re.I):
        amount = parse_amount(m.group("rest"))
        if amount is not None:
            _, method = _extract_method(m.group("rest"))
            return Intent("record_payment", {"customer": m.group("name").strip(), "amount": amount,
                                             "method": method or "cash"})
    # "received 300 from Mary", "payment from Mary 300"
    m = re.match(rf"^(?:received|got|nimepokea|reçu|payment)\s+(?P<a>.*?)\bfrom\s+(?P<name>[A-Za-z][\w' .-]*?)(?P<b>\s+\S*{_NUM}.*)?$",
                 text, re.I)
    if m:
        amount = parse_amount((m.group("a") or "") + " " + (m.group("b") or ""))
        if amount is not None:
            _, method = _extract_method(text)
            return Intent("record_payment", {"customer": m.group("name").strip(), "amount": amount,
                                             "method": method or "cash"})
    return None


def parse_command(text: str) -> Intent | None:
    t = text.lower().strip(" .")
    words = t.split()
    if not words:
        return None
    first = words[0]
    rest = " ".join(words[1:])

    if t in {"hi", "hello", "hey", "habari", "hujambo", "mambo", "bonjour", "salut", "how far", "start", "menu",
             "help", "msaada", "aide", "?", "commands"}:
        return Intent("help")
    if t in {"yes delete", "yes, delete"}:
        return Intent("confirm_delete")
    if t in {"delete my data", "delete everything", "futa data yangu", "supprimer mes données", "delete account"}:
        return Intent("delete_data")
    if first in {"report", "ripoti", "rapport", "summary", "muhtasari", "bilan"} or t in PERIODS:
        period = PERIODS.get(rest or t, "today") if (rest or t in PERIODS) else "today"
        return Intent("report", {"period": period})
    if t in {"profit", "faida", "bénéfice", "how much did i make", "how much i make today"}:
        return Intent("report", {"period": "today"})
    if first in {"stock", "mzigo", "inventory", "goods", "bidhaa"} and not re.search(_NUM, rest):
        return Intent("stock", {"item": rest or None})
    if t in {"low stock", "what is finishing", "what to buy", "shopping list"}:
        return Intent("stock", {"low_only": True})
    if t in {"debtors", "deni", "madeni", "who owes me", "who owes", "credit book", "debts", "débiteurs", "owing"}:
        return Intent("debtors")
    if first in {"remind", "kumbusha", "relancer", "chase", "reminder", "reminders"}:
        return Intent("remind", {"customer": rest or None})
    if t in {"undo", "futa", "annuler", "cancel last", "delete last", "mistake", "wrong"}:
        return Intent("undo")
    if t in {"recent", "history", "last", "historia", "historique", "transactions"}:
        return Intent("recent")
    if t in {"score", "credit score", "my score", "credit", "credit profile", "loan", "can i get a loan", "mkopo",
             "alama", "crédit"}:
        return Intent("credit_score")
    if t in {"share score", "share credit", "share my score", "send score to lender"}:
        return Intent("share_score")
    if t in {"stop sharing", "unshare score", "revoke"}:
        return Intent("unshare_score")
    if first in {"lesson", "somo", "leçon", "lecon", "learn", "tip", "jifunze", "course", "class"}:
        return Intent("lesson", {"query": rest or None})
    if t in {"progress", "my progress", "maendeleo"}:
        return Intent("progress")
    if first in {"language", "lugha", "langue", "lang"} and rest:
        return Intent("set_language", {"language": rest})
    if first in {"currency", "sarafu", "monnaie"} and rest:
        return Intent("set_currency", {"currency": rest.upper()})
    if t in {"dashboard", "web", "link", "website", "my dashboard"}:
        return Intent("dashboard")
    if t in {"summary on", "summary off", "daily summary on", "daily summary off"}:
        return Intent("daily_summary", {"on": t.endswith("on")})
    if re.fullmatch(r"[a-d]", t):
        return Intent("quiz_answer", {"letter": t.upper()})

    m = re.match(rf"^(?:count|hesabu|counted|compter)\s+(?P<item>.+?)\s+(?P<qty>{_NUM})\s*$", text, re.I)
    if m:
        return Intent("stock_count", {"item": m.group("item"), "quantity": _num(m.group("qty"))})
    m = re.match(rf"^(?:reorder|alert|min|minimum)\s+(?P<item>.+?)\s+(?:at\s+)?(?P<qty>{_NUM})\s*$", text, re.I)
    if m:
        return Intent("reorder_level", {"item": m.group("item"), "level": _num(m.group("qty"))})
    m = re.match(rf"^(?:price|bei|prix|set price)\s+(?:of\s+)?(?P<item>.+?)\s+(?:is\s+|to\s+)?(?P<amt>\S*{_NUM}\S*)\s*$", text, re.I)
    if m:
        return Intent("set_price", {"item": m.group("item"), "price": parse_amount(m.group("amt"))})
    m = re.match(r"^(?:phone|number|namba|simu|tel)\s+(?P<name>.+?)\s+(?P<phone>\+?[\d\s]{9,15})$", text, re.I)
    if m:
        return Intent("set_customer_phone", {"customer": m.group("name"), "phone": m.group("phone")})
    m = re.match(rf"^(?:capital|invested|added capital|i added|nimeweka)\s+(?P<amt>\S*{_NUM}\S*)", text, re.I)
    if m:
        return Intent("capital", {"amount": parse_amount(m.group("amt"))})
    return None


def parse_invoice(text: str) -> Intent | None:
    m = re.match(r"^(?:invoice|ankara|facture|bill)\s+(?P<body>.+)$", text, re.I)
    if not m:
        return None
    body = m.group("body")
    due = 7
    md = re.search(r"\bdue\s+(?:in\s+)?(?P<n>\d+)\s*(?:days?|siku)?\b", body, re.I)
    if md:
        due = int(md.group("n"))
        body = (body[: md.start()] + body[md.end():]).strip()
    phone = None
    mp = re.search(r"(\+?\d[\d\s]{8,14}\d)", body)
    if mp and len(re.sub(r"\D", "", mp.group(1))) >= 9:
        phone = mp.group(1)
        body = (body[: mp.start()] + body[mp.end():]).strip()
    # Customer = leading words before the first quantity/number
    mc = re.match(r"^(?:for\s+|to\s+)?(?P<name>[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ' .-]*?)\s*[:,-]?\s+(?P<items>\d.*)$", body)
    if not mc:
        return None
    lines = [line for seg in _split_lines(mc.group("items")) if (line := parse_line(seg))]
    if not lines:
        return None
    return Intent("invoice", {"customer": mc.group("name").strip(), "lines": lines, "due_days": due, "phone": phone})


def parse(text: str) -> Intent | None:
    """Return an Intent when we are confident, else None (the caller may then ask the AI layer)."""
    cleaned = _clean(text)
    if not cleaned:
        return None
    for parser in (parse_command, parse_invoice, parse_sale, parse_purchase, parse_draw, parse_payment, parse_expense):
        intent = parser(cleaned)
        if intent is not None:
            return intent
    return None


def amounts_in(text: str) -> list[Decimal]:
    return find_amounts(text)
