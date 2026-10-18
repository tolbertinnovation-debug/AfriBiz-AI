"""Business accounts. A business is identified by the owner's phone number."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from typing import Any

from ..money import guess_country
from ..timeutil import TIMEZONE_BY_COUNTRY


@dataclass
class Business:
    id: int
    phone: str
    name: str | None
    owner_name: str | None
    business_type: str | None
    country: str | None
    currency: str
    language: str
    timezone: str
    onboarding_step: str
    pending_action: dict[str, Any] | None
    daily_summary: bool
    created_at: str

    @property
    def onboarded(self) -> bool:
        return self.onboarding_step == "done"

    @property
    def display_name(self) -> str:
        return self.name or "your business"

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Business:
        pending = json.loads(row["pending_action"]) if row["pending_action"] else None
        return cls(
            id=row["id"],
            phone=row["phone"],
            name=row["name"],
            owner_name=row["owner_name"],
            business_type=row["business_type"],
            country=row["country"],
            currency=row["currency"],
            language=row["language"],
            timezone=row["timezone"],
            onboarding_step=row["onboarding_step"],
            pending_action=pending,
            daily_summary=bool(row["daily_summary"]),
            created_at=row["created_at"],
        )


def normalize_phone(phone: str) -> str:
    """Canonical international form without '+', e.g. 'whatsapp:+254 712 345678' -> '254712345678'."""
    return re.sub(r"\D", "", phone or "")


def get_by_phone(conn: sqlite3.Connection, phone: str) -> Business | None:
    row = conn.execute("SELECT * FROM businesses WHERE phone = ?", (normalize_phone(phone),)).fetchone()
    return Business.from_row(row) if row else None


def get(conn: sqlite3.Connection, business_id: int) -> Business:
    row = conn.execute("SELECT * FROM businesses WHERE id = ?", (business_id,)).fetchone()
    if row is None:
        raise LookupError(f"business {business_id} not found")
    return Business.from_row(row)


def get_or_create(conn: sqlite3.Connection, phone: str) -> tuple[Business, bool]:
    existing = get_by_phone(conn, phone)
    if existing:
        return existing, False
    number = normalize_phone(phone)
    country, currency, language = guess_country(number)
    timezone = TIMEZONE_BY_COUNTRY.get(country, "Africa/Nairobi")
    conn.execute(
        "INSERT INTO businesses (phone, country, currency, language, timezone) VALUES (?, ?, ?, ?, ?)",
        (number, country, currency, language, timezone),
    )
    created = get_by_phone(conn, number)
    assert created is not None
    return created, True


_UPDATABLE = {
    "name", "owner_name", "business_type", "currency", "language", "timezone",
    "onboarding_step", "daily_summary", "country",
}


def update(conn: sqlite3.Connection, business: Business, **fields: Any) -> Business:
    unknown = set(fields) - _UPDATABLE
    if unknown:
        raise ValueError(f"cannot update {sorted(unknown)}")
    if not fields:
        return business
    assignments = ", ".join(f"{key} = ?" for key in fields)
    conn.execute(f"UPDATE businesses SET {assignments} WHERE id = ?", (*fields.values(), business.id))
    return get(conn, business.id)


def set_pending(conn: sqlite3.Connection, business: Business, action: dict[str, Any] | None) -> None:
    conn.execute(
        "UPDATE businesses SET pending_action = ? WHERE id = ?",
        (json.dumps(action) if action else None, business.id),
    )
    business.pending_action = action


def all_active(conn: sqlite3.Connection) -> list[Business]:
    rows = conn.execute("SELECT * FROM businesses WHERE onboarding_step = 'done' ORDER BY id").fetchall()
    return [Business.from_row(r) for r in rows]


def delete_all_data(conn: sqlite3.Connection, business: Business) -> None:
    """Right to erasure (Kenya DPA 2019 s.40, Nigeria NDPA 2023 s.34): remove everything we hold."""
    bid = business.id
    conn.execute(
        "DELETE FROM transaction_lines WHERE transaction_id IN (SELECT id FROM transactions WHERE business_id = ?)",
        (bid,),
    )
    for table in (
        "stock_movements", "invoices", "reminders", "transactions", "items", "customers",
        "momo_transactions", "messages", "lesson_progress", "consents",
    ):
        conn.execute(f"DELETE FROM {table} WHERE business_id = ?", (bid,))
    conn.execute("DELETE FROM businesses WHERE id = ?", (bid,))
