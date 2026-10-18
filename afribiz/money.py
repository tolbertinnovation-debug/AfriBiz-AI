"""Money handling: currencies, parsing amounts the way people type them, formatting.

All amounts are stored as integers in *minor units* (cents/kobo/pesewas) so that
sums never drift. Many currencies are quoted without decimals in daily life
(UGX, TZS, RWF, XOF), which the formatting respects.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation


@dataclass(frozen=True)
class Currency:
    code: str
    symbol: str
    decimals: int  # decimals shown to users (storage is always x100)
    name: str


CURRENCIES: dict[str, Currency] = {
    c.code: c
    for c in [
        Currency("KES", "KSh", 0, "Kenyan shilling"),
        Currency("NGN", "₦", 0, "Nigerian naira"),
        Currency("GHS", "GH₵", 2, "Ghanaian cedi"),
        Currency("UGX", "USh", 0, "Ugandan shilling"),
        Currency("TZS", "TSh", 0, "Tanzanian shilling"),
        Currency("RWF", "RWF", 0, "Rwandan franc"),
        Currency("ZAR", "R", 2, "South African rand"),
        Currency("ZMW", "K", 2, "Zambian kwacha"),
        Currency("MWK", "MK", 0, "Malawian kwacha"),
        Currency("ETB", "Br", 2, "Ethiopian birr"),
        Currency("XOF", "CFA", 0, "West African CFA franc"),
        Currency("XAF", "FCFA", 0, "Central African CFA franc"),
        Currency("EGP", "E£", 2, "Egyptian pound"),
        Currency("MAD", "DH", 2, "Moroccan dirham"),
        Currency("CDF", "FC", 0, "Congolese franc"),
        Currency("SLE", "Le", 2, "Sierra Leonean leone"),
        Currency("LRD", "L$", 2, "Liberian dollar"),
        Currency("GMD", "D", 2, "Gambian dalasi"),
        Currency("MZN", "MT", 2, "Mozambican metical"),
        Currency("BWP", "P", 2, "Botswana pula"),
        Currency("NAD", "N$", 2, "Namibian dollar"),
        Currency("ZWG", "ZiG", 2, "Zimbabwe gold"),
        Currency("SSP", "SSP", 0, "South Sudanese pound"),
        Currency("SOS", "Sh.So.", 0, "Somali shilling"),
        Currency("USD", "$", 2, "US dollar"),
    ]
}

# Dialling code -> (ISO country, currency, default language)
COUNTRY_BY_DIAL_CODE: dict[str, tuple[str, str, str]] = {
    "254": ("KE", "KES", "en"),
    "234": ("NG", "NGN", "en"),
    "233": ("GH", "GHS", "en"),
    "256": ("UG", "UGX", "en"),
    "255": ("TZ", "TZS", "sw"),
    "250": ("RW", "RWF", "en"),
    "27": ("ZA", "ZAR", "en"),
    "260": ("ZM", "ZMW", "en"),
    "265": ("MW", "MWK", "en"),
    "251": ("ET", "ETB", "en"),
    "221": ("SN", "XOF", "fr"),
    "225": ("CI", "XOF", "fr"),
    "223": ("ML", "XOF", "fr"),
    "226": ("BF", "XOF", "fr"),
    "228": ("TG", "XOF", "fr"),
    "229": ("BJ", "XOF", "fr"),
    "227": ("NE", "XOF", "fr"),
    "237": ("CM", "XAF", "fr"),
    "241": ("GA", "XAF", "fr"),
    "242": ("CG", "XAF", "fr"),
    "243": ("CD", "CDF", "fr"),
    "20": ("EG", "EGP", "en"),
    "212": ("MA", "MAD", "fr"),
    "232": ("SL", "SLE", "en"),
    "231": ("LR", "LRD", "en"),
    "220": ("GM", "GMD", "en"),
    "258": ("MZ", "MZN", "en"),
    "267": ("BW", "BWP", "en"),
    "264": ("NA", "NAD", "en"),
    "263": ("ZW", "ZWG", "en"),
    "211": ("SS", "SSP", "en"),
    "252": ("SO", "SOS", "en"),
}

# Words/symbols people put in front of or after amounts.
_CURRENCY_TOKENS = (
    r"ksh|kshs|kes|sh|shs|tsh|tzs|ush|ugx|ugsh|ngn|naira|n|₦|ghs|gh₵|ghc|cedis?|₵|rwf|frw|zar|r|"
    r"zmw|k|mwk|mk|etb|birr|xof|xaf|cfa|fcfa|f|usd|\$|bob|le|sle|egp|mad|dh|cdf|fc"
)

_AMOUNT_RE = re.compile(
    rf"(?<![\w.])(?:(?:{_CURRENCY_TOKENS})\.?\s?)?"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"\s?(?P<suffix>k|m|thousand|million|elfu|/=|/-)?(?![\w])",
    re.IGNORECASE,
)


def currency_for(code: str | None) -> Currency:
    if code and code.upper() in CURRENCIES:
        return CURRENCIES[code.upper()]
    return CURRENCIES["USD"]


def guess_country(phone: str) -> tuple[str, str, str]:
    """Best-effort (country, currency, language) from an international phone number."""
    digits = re.sub(r"\D", "", phone or "")
    for length in (3, 2):
        prefix = digits[:length]
        if prefix in COUNTRY_BY_DIAL_CODE:
            return COUNTRY_BY_DIAL_CODE[prefix]
    return ("", "USD", "en")


def to_minor(value: Decimal | int | float | str) -> int:
    """Convert a major-unit amount (e.g. 150.50) into stored minor units (15050)."""
    try:
        dec = Decimal(str(value))
    except InvalidOperation as exc:  # pragma: no cover - defensive
        raise ValueError(f"not an amount: {value!r}") from exc
    return int((dec * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def from_minor(amount: int) -> Decimal:
    return (Decimal(amount) / 100).quantize(Decimal("0.01"))


def parse_amount(text: str) -> Decimal | None:
    """Parse a single amount the way it is typed in chats.

    Handles "1,500", "1.5k", "2k", "KSh 300", "₦5,000", "500/=", "3 elfu".
    Returns the amount in major units, or None when nothing looks like money.
    """
    found = find_amounts(text)
    return found[0] if found else None


def find_amounts(text: str) -> list[Decimal]:
    results: list[Decimal] = []
    for match in _AMOUNT_RE.finditer(text or ""):
        results.append(_match_value(match))
    return results


def _match_value(match: re.Match[str]) -> Decimal:
    raw = match.group("num").replace(",", "")
    value = Decimal(raw)
    suffix = (match.group("suffix") or "").lower()
    if suffix in {"k", "thousand", "elfu"}:
        value *= 1000
    elif suffix in {"m", "million"}:
        value *= 1_000_000
    return value


def format_money(amount_minor: int, currency_code: str | None) -> str:
    cur = currency_for(currency_code)
    major = from_minor(amount_minor)
    negative = major < 0
    major = abs(major)
    if cur.decimals == 0 or major == major.to_integral_value():
        body = f"{major.quantize(Decimal('1'), rounding=ROUND_HALF_UP):,}"
    else:
        body = f"{major:,.2f}"
    return f"{'-' if negative else ''}{cur.symbol} {body}"
