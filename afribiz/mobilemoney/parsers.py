"""Parse mobile-money and bank-alert SMS that owners forward into the chat.

Providers covered: Safaricom M-PESA (KE), Vodacom M-Pesa (TZ), MTN MoMo (UG, GH, RW, ZM, CM, CI),
Airtel Money (KE, UG, TZ, ZM, MW), Telecel Cash / AirtelTigo (GH), Tigo Pesa/Mixx (TZ), Orange Money,
Wave, and Nigerian bank/fintech credit/debit alerts (OPay, Moniepoint, PalmPay and the banks), which
play the role of mobile money in Nigeria's transfer-led economy.

SMS wording differs by country and changes over time, so the parser does not rely on one fixed
template per provider. It finds the parts every confirmation has: a direction verb, an amount with
a currency, a counterparty, a reference and, often, a balance and a fee. It labels the amount, the
fee and the balance by the words around each figure, because a confirmation SMS often carries
three different amounts.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from ..money import to_minor

CURRENCY = r"(?:Ksh|KES|Kshs|UGX|USh|GHS|GH₵|GHC|TZS|Tsh|TSh|RWF|FRW|ZMW|ZK|MWK|MK|NGN|₦|N|XOF|XAF|F\s?CFA|FCFA|CFA|CDF|ZAR|R|ETB|SLE|USD)"
MONEY = rf"(?<![A-Za-z]){CURRENCY}\.?\s?(\d{{1,3}}(?:,\d{{3}})+(?:\.\d{{1,2}})?|\d+(?:\.\d{{1,2}})?)"
MONEY_RE = re.compile(MONEY, re.IGNORECASE)
MONEY_SUFFIX_RE = re.compile(r"(\d{1,3}(?:[,\s]\d{3})+|\d+)\s?(?:F\s?CFA|FCFA|CFA|XOF|XAF)", re.IGNORECASE)

PROVIDERS = [
    ("mpesa", re.compile(r"m-?pesa", re.I)),
    ("mtn_momo", re.compile(r"\bmomo\b|\bMTN\b|mobile money|payment (?:received|made) for GH", re.I)),
    ("airtel_money", re.compile(r"airtel", re.I)),
    ("telecel_cash", re.compile(r"telecel|vodafone cash|airteltigo|\bAT money", re.I)),
    ("tigo_pesa", re.compile(r"tigo ?pesa|mixx by yas|\bmixx\b", re.I)),
    ("orange_money", re.compile(r"orange money", re.I)),
    ("wave", re.compile(r"\bwave\b", re.I)),
    ("opay", re.compile(r"\bopay\b", re.I)),
    ("moniepoint", re.compile(r"moniepoint", re.I)),
    ("palmpay", re.compile(r"palmpay", re.I)),
    ("bank_alert", re.compile(r"credit alert|debit alert|\bAmt\s*:|\bAcct\s*:|\bCR\b|\bDR\b")),
]

BALANCE_WORDS = re.compile(
    r"(balance\s*(?:is|:)?|bal\.?\s*:?|avail(?:able)?\s*bal(?:ance)?\s*:?|current balance\s*:?|new balance\s*:?|solde\s*:?)\s*$",
    re.I,
)
FEE_WORDS = re.compile(
    r"(transaction cost,?|fee charged\s*:?|fees?\s*:?|charges?\s*:?|cost\s*:?|frais\s*:?|tax\s*:?|e-?levy\s*:?)\s*$",
    re.I,
)
OTHER_WORDS = re.compile(
    r"(daily (?:transaction )?limit|can transact|limit is|fuliza m-pesa amount is|outstanding amount is|"
    r"loan (?:limit|balance)\s*(?:is|:)?|interest\s*:?)\s*$",
    re.I,
)

DATE_PATTERNS = [
    (re.compile(r"(\d{1,2})/(\d{1,2})/(\d{2,4})\s*(?:at)?\s*(\d{1,2}):(\d{2})\s*([AP]M)?", re.I), "dmy"),
    (re.compile(r"(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::\d{2})?"), "ymd"),
    (re.compile(r"(\d{1,2})-(\d{1,2})-(\d{4})\s+(\d{1,2}):(\d{2})"), "dmy"),
    (re.compile(r"(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})"), "dmy_date"),
    (re.compile(r"(\d{4})-(\d{2})-(\d{2})"), "ymd_date"),
]

REF_PATTERNS = [
    re.compile(r"^\s*([A-Z0-9]{10})\s+(?:Confirmed|confirmed)"),
    re.compile(r"(?:Financial\s+)?Transaction\s+I[Dd]\s*[:.]?\s*([A-Za-z0-9.\-]{5,})"),
    re.compile(r"\bTID\s*[:.]?\s*([A-Za-z0-9.\-]{5,})"),
    re.compile(r"\bTxn\s*(?:ID|Ref)?\s*[:.]?\s*([A-Za-z0-9.\-]{5,})", re.I),
    re.compile(r"\bRef(?:erence)?\s*(?:No\.?)?\s*[:.]\s*([A-Za-z0-9\-/]{6,})"),
    re.compile(r"\bID\s*:\s*([A-Za-z0-9.\-]{5,})"),
]

PHONE_RE = re.compile(r"(?<!\d)(?:\+?(?:2\d{2}|0))\d{8,9}(?!\d)")


@dataclass
class MomoTxn:
    provider: str
    ref: str
    direction: str  # "in" | "out"
    category: str  # received | sent | paybill | buygoods | withdraw | deposit | airtime | loan | reversal
    amount: int  # minor units
    fee: int
    balance: int | None
    counterparty: str | None
    counterparty_phone: str | None
    occurred_at: str  # ISO UTC
    raw: str

    @property
    def is_business_income(self) -> bool:
        return self.direction == "in" and self.category == "received"


def looks_like_momo(text: str) -> bool:
    """Cheap pre-check used by the router before it attempts a full parse."""
    if not text or len(text) < 25:
        return False
    has_money = bool(MONEY_RE.search(text) or MONEY_SUFFIX_RE.search(text))
    keywords = re.search(
        r"confirmed|received|sent to|paid to|payment (?:received|made)|transaction id|\bTID\b|new balance|"
        r"credit alert|debit alert|withdraw|avail(?:able)? bal|\bbal\b|solde|transfert|re[çc]u",
        text, re.I,
    )
    return has_money and bool(keywords)


def _detect_provider(text: str) -> str:
    for name, pattern in PROVIDERS:
        if pattern.search(text):
            return name
    return "mobile_money"


def _classify(text: str) -> tuple[str, str] | None:
    t = text.lower()
    if re.search(r"revers", t):
        return "in", "reversal"
    if re.search(r"fuliza|m-?shwari loan|loan (?:disbursed|credited)|kcb m-?pesa loan|you have borrowed|qwikloan|xtracash", t):
        if re.search(r"repa(y|id)|paid to (?:fuliza|m-?shwari)|deducted", t):
            return "out", "loan_repayment"
        return "in", "loan"
    if "airtime" in t and re.search(r"bought|purchase|recharge", t):
        return "out", "airtime"
    if re.search(r"withdr[ae]w|cash ?out|retrait", t):
        return "out", "withdraw"
    if re.search(r"give .{0,30}cash to|deposit(?:ed)?|cash ?in\b|d[ée]p[ôo]t", t):
        return "in", "deposit"
    if re.search(r"credit alert|\bcr\b|you have received|payment received|received\b|re[çc]u|has sent you|credited", t):
        return "in", "received"
    if re.search(r"sent to .{0,60}for account|pay ?bill|for account", t):
        return "out", "paybill"
    if re.search(r"paid to|payment (?:made|to)|merchant payment|buy goods|till", t):
        return "out", "buygoods"
    if re.search(r"debit alert|\bdr\b|sent to|you have sent|transferred to|transfer to|debited|envoy[ée]", t):
        return "out", "sent"
    return None


def _labelled_amounts(text: str) -> tuple[list[Decimal], Decimal | None, Decimal | None]:
    """Split the money figures in the SMS into (transaction amounts, balance, fee)."""
    amounts: list[Decimal] = []
    balance: Decimal | None = None
    fee: Decimal | None = None
    matches = list(MONEY_RE.finditer(text))
    if not matches:
        matches = list(MONEY_SUFFIX_RE.finditer(text))
    for m in matches:
        raw = m.group(1).replace(",", "").replace(" ", "")
        value = Decimal(raw)
        before = text[max(0, m.start() - 40): m.start()]
        if BALANCE_WORDS.search(before):
            balance = value if balance is None else balance
        elif FEE_WORDS.search(before):
            fee = value if fee is None else fee
        elif OTHER_WORDS.search(before):
            continue
        else:
            amounts.append(value)
    return amounts, balance, fee


def _parse_date(text: str) -> str:
    for pattern, kind in DATE_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        try:
            if kind == "dmy":
                day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
                hour, minute = int(m.group(4)), int(m.group(5))
                ampm = (m.group(6) or "").upper() if m.lastindex and m.lastindex >= 6 else ""
                if ampm == "PM" and hour < 12:
                    hour += 12
                elif ampm == "AM" and hour == 12:
                    hour = 0
            elif kind == "ymd":
                year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
                hour, minute = int(m.group(4)), int(m.group(5))
            elif kind == "dmy_date":
                day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
                hour = minute = 12
            else:
                year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
                hour = minute = 12
            if year < 100:
                year += 2000
            # Local time in East/West Africa is UTC+0..+3; storing as UTC-naive local is close enough
            # for daily bucketing, and we keep the raw SMS for audit.
            return datetime(year, month, day, hour, minute, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            continue
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ref(text: str) -> str:
    for pattern in REF_PATTERNS:
        m = pattern.search(text)
        if m:
            return m.group(1).rstrip(".")
    return "H" + hashlib.sha1(re.sub(r"\s+", " ", text.strip()).encode()).hexdigest()[:15].upper()


_COUNTERPARTY_STOP = r"(?=\s+on\s|\s+for\s+account|\s+at\s+\d|\s*\.\s|\s*\.$|\s+\d{9,}|\s*\(|\s*,|\s+New\b|\s+Current|\s+Your\b|\s+Ref|\s+Reason|\s*$)"


def _counterparty(text: str, direction: str, category: str) -> tuple[str | None, str | None]:
    if category in {"airtime"}:
        return None, None
    if category == "withdraw":
        m = re.search(r"from\s+(?:\d+\s*-\s*)?(.+?)" + _COUNTERPARTY_STOP, text, re.I)
    elif category == "deposit":
        m = re.search(r"cash to\s+(?:\d+\s*-\s*)?(.+?)" + _COUNTERPARTY_STOP, text, re.I)
    elif category == "received" and re.search(r"TRF FROM|Desc\s*:", text, re.I):
        m = re.search(r"(?:TRF FROM|FROM)\s*:?\s*([A-Z][A-Z .'-]+?)(?=\s*(?:/|-|Avail|Bal|Date|Ref|\.|$))", text, re.I)
    elif direction == "in":
        m = re.search(r"\bfrom\s+(?:\+?\d{9,13}\s*,?\s*)?(.+?)" + _COUNTERPARTY_STOP, text, re.I)
    else:
        m = re.search(r"\b(?:sent to|paid to|payment made for .*? to|to)\s+(.+?)" + _COUNTERPARTY_STOP, text, re.I)
    name = None
    if m:
        name = re.sub(r"\s+", " ", m.group(1)).strip(" .-:")
        if MONEY_RE.fullmatch(name or "") or not re.search(r"[A-Za-z]", name or ""):
            name = None
        elif name:
            name = name[:60]
    phone = None
    pm = PHONE_RE.search(text)
    if pm:
        phone = re.sub(r"\D", "", pm.group(0))
    return name or None, phone


def parse_sms(text: str) -> MomoTxn | None:
    """Parse one forwarded SMS. Returns None when the text is not a money confirmation."""
    if not looks_like_momo(text):
        return None
    if re.search(r"failed|insufficient|not successful|declined|cancelled", text, re.I):
        return None
    classified = _classify(text)
    if classified is None:
        return None
    direction, category = classified
    amounts, balance, fee = _labelled_amounts(text)
    if not amounts:
        return None
    counterparty, phone = _counterparty(text, direction, category)
    return MomoTxn(
        provider=_detect_provider(text),
        ref=_ref(text),
        direction=direction,
        category=category,
        amount=to_minor(amounts[0]),
        fee=to_minor(fee) if fee is not None else 0,
        balance=to_minor(balance) if balance is not None else None,
        counterparty=counterparty,
        counterparty_phone=phone,
        occurred_at=_parse_date(text),
        raw=text.strip(),
    )


def split_messages(blob: str) -> list[str]:
    """Owners often paste several SMS at once; split them on blank lines or on new M-PESA codes."""
    parts = [p.strip() for p in re.split(r"\n\s*\n", blob or "") if p.strip()]
    out: list[str] = []
    for part in parts:
        pieces = re.split(r"(?=\b[A-Z0-9]{10}\s+Confirmed)", part)
        out.extend(p.strip() for p in pieces if p.strip())
    return out
