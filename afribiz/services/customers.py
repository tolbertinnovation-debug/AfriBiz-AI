"""Customers and what they owe (accounts receivable / 'deni' / 'credit book')."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .businesses import Business, normalize_phone
from .matching import best_match, tidy_display


@dataclass
class Customer:
    id: int
    name: str
    phone: str | None


@dataclass
class Debtor:
    customer: Customer
    balance: int  # minor units owed to the business
    oldest_unpaid: str | None  # occurred_at of the oldest sale still (partly) unpaid
    last_payment: str | None


def get(conn: sqlite3.Connection, customer_id: int) -> Customer:
    row = conn.execute("SELECT id, name, phone FROM customers WHERE id = ?", (customer_id,)).fetchone()
    if row is None:
        raise LookupError(f"customer {customer_id} not found")
    return Customer(row["id"], row["name"], row["phone"])


def find(conn: sqlite3.Connection, business: Business, name: str) -> Customer | None:
    rows = conn.execute("SELECT id, name FROM customers WHERE business_id = ?", (business.id,)).fetchall()
    match = best_match(name, {r["id"]: r["name"] for r in rows}, cutoff=0.85)
    return get(conn, match) if match else None


def find_by_phone(conn: sqlite3.Connection, business: Business, phone: str) -> Customer | None:
    number = normalize_phone(phone)
    if not number:
        return None
    rows = conn.execute(
        "SELECT id, phone FROM customers WHERE business_id = ? AND phone IS NOT NULL", (business.id,)
    ).fetchall()
    for r in rows:
        stored = normalize_phone(r["phone"])
        # Match on the last 9 digits so 0712.. and 254712.. are the same person.
        if stored and stored[-9:] == number[-9:]:
            return get(conn, r["id"])
    return None


def find_or_create(conn: sqlite3.Connection, business: Business, name: str, phone: str | None = None) -> Customer:
    customer = find(conn, business, name)
    if customer is None and phone:
        customer = find_by_phone(conn, business, phone)
    if customer:
        if phone and not customer.phone:
            conn.execute("UPDATE customers SET phone = ? WHERE id = ?", (normalize_phone(phone), customer.id))
            customer.phone = normalize_phone(phone)
        return customer
    cur = conn.execute(
        "INSERT INTO customers (business_id, name, phone) VALUES (?, ?, ?)",
        (business.id, tidy_display(name), normalize_phone(phone) if phone else None),
    )
    return get(conn, int(cur.lastrowid))


def set_phone(conn: sqlite3.Connection, customer: Customer, phone: str) -> Customer:
    conn.execute("UPDATE customers SET phone = ? WHERE id = ?", (normalize_phone(phone), customer.id))
    return get(conn, customer.id)


def balance(conn: sqlite3.Connection, customer_id: int) -> int:
    row = conn.execute(
        """
        SELECT
          COALESCE(SUM(CASE WHEN kind = 'sale' THEN amount - paid_amount ELSE 0 END), 0)
          - COALESCE(SUM(CASE WHEN kind = 'payment_in' THEN amount ELSE 0 END), 0) AS owed
        FROM transactions WHERE customer_id = ? AND voided = 0
        """,
        (customer_id,),
    ).fetchone()
    return int(row["owed"])


def oldest_unpaid_date(conn: sqlite3.Connection, customer_id: int) -> str | None:
    """Walk credit sales oldest-first, applying payments FIFO, and return the first still unpaid."""
    rows = conn.execute(
        "SELECT kind, amount, paid_amount, occurred_at FROM transactions "
        "WHERE customer_id = ? AND voided = 0 AND kind IN ('sale','payment_in') ORDER BY occurred_at, id",
        (customer_id,),
    ).fetchall()
    credit = sum(r["amount"] for r in rows if r["kind"] == "payment_in")
    for r in rows:
        if r["kind"] != "sale":
            continue
        owed = r["amount"] - r["paid_amount"]
        if owed <= 0:
            continue
        if credit >= owed:
            credit -= owed
            continue
        return r["occurred_at"]
    return None


def debtors(conn: sqlite3.Connection, business: Business) -> list[Debtor]:
    rows = conn.execute("SELECT id FROM customers WHERE business_id = ?", (business.id,)).fetchall()
    out: list[Debtor] = []
    for r in rows:
        owed = balance(conn, r["id"])
        if owed <= 0:
            continue
        last = conn.execute(
            "SELECT MAX(occurred_at) AS t FROM transactions WHERE customer_id = ? AND kind = 'payment_in' AND voided = 0",
            (r["id"],),
        ).fetchone()["t"]
        out.append(Debtor(get(conn, r["id"]), owed, oldest_unpaid_date(conn, r["id"]), last))
    out.sort(key=lambda d: d.balance, reverse=True)
    return out
