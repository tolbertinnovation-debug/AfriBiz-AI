"""Storing mobile-money history: forwarded SMS and CSV statement imports, de-duplicated by reference."""

from __future__ import annotations

import csv
import io
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from ..mobilemoney.parsers import MomoTxn, parse_sms, split_messages
from ..money import to_minor
from .businesses import Business


@dataclass
class ImportResult:
    added: list[MomoTxn]
    duplicates: int
    unparsed: int


def save(conn: sqlite3.Connection, business: Business, txn: MomoTxn) -> bool:
    """Insert one transaction; returns False if we already had it (same provider + reference)."""
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO momo_transactions
          (business_id, provider, ref, direction, category, amount, fee, balance, counterparty,
           counterparty_phone, occurred_at, raw)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (business.id, txn.provider, txn.ref, txn.direction, txn.category, txn.amount, txn.fee, txn.balance,
         txn.counterparty, txn.counterparty_phone, txn.occurred_at, txn.raw),
    )
    return cur.rowcount > 0


def import_sms_blob(conn: sqlite3.Connection, business: Business, blob: str) -> ImportResult:
    added: list[MomoTxn] = []
    dup = bad = 0
    for message in split_messages(blob):
        txn = parse_sms(message)
        if txn is None:
            bad += 1
            continue
        if save(conn, business, txn):
            added.append(txn)
        else:
            dup += 1
    return ImportResult(added, dup, bad)


_HEADER_ALIASES = {
    "ref": ("receipt no.", "receipt no", "receipt", "transaction id", "reference", "ref", "id", "txn id"),
    "date": ("completion time", "date", "transaction date", "time", "date/time", "initiation time"),
    "details": ("details", "description", "narration", "remarks", "particulars", "type"),
    "paid_in": ("paid in", "credit", "money in", "in", "amount in", "received"),
    "withdrawn": ("withdrawn", "debit", "money out", "out", "amount out", "paid out"),
    "amount": ("amount",),
    "balance": ("balance", "running balance", "available balance"),
    "status": ("transaction status", "status"),
}


def _map_headers(headers: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    lowered = {h.strip().lower(): h for h in headers if h}
    for field, aliases in _HEADER_ALIASES.items():
        for alias in aliases:
            if alias in lowered:
                mapping[field] = lowered[alias]
                break
    return mapping


def _money(value: str | None) -> Decimal | None:
    if value is None:
        return None
    cleaned = re.sub(r"[^\d.\-]", "", value)
    if cleaned in {"", "-", "."}:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _date(value: str) -> str:
    value = (value or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d-%m-%Y %H:%M",
                "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%d-%b-%Y"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            continue
    raise ValueError(f"unrecognised date {value!r}")


def _category_from_details(details: str, direction: str) -> str:
    d = details.lower()
    if "fuliza" in d or "loan" in d or "overdraft" in d:
        return "loan_repayment" if direction == "out" else "loan"
    if "revers" in d:
        return "reversal"
    if "withdraw" in d or "cash out" in d:
        return "withdraw"
    if "deposit" in d or "cash in" in d:
        return "deposit"
    if "airtime" in d:
        return "airtime"
    if "pay bill" in d or "paybill" in d:
        return "paybill"
    if "merchant" in d or "buy goods" in d or "till" in d:
        return "buygoods"
    if "charge" in d or "cost" in d or "fee" in d:
        return "fee"
    return "received" if direction == "in" else "sent"


def import_csv(conn: sqlite3.Connection, business: Business, data: str, provider: str = "statement") -> ImportResult:
    """Import a statement exported to CSV (M-PESA, MoMo, bank). Columns are matched by common names."""
    reader = csv.DictReader(io.StringIO(data.lstrip("﻿")))
    mapping = _map_headers(reader.fieldnames or [])
    if "date" not in mapping or not ({"paid_in", "withdrawn"} & mapping.keys() or "amount" in mapping):
        raise ValueError("CSV needs a date column and paid in / withdrawn (or amount) columns")
    added: list[MomoTxn] = []
    dup = bad = 0
    for index, row in enumerate(reader):
        try:
            status = row.get(mapping.get("status", ""), "") or ""
            if status and status.strip().lower() not in {"completed", "success", "successful"}:
                continue
            paid_in = _money(row.get(mapping.get("paid_in", ""))) or Decimal(0)
            withdrawn = _money(row.get(mapping.get("withdrawn", ""))) or Decimal(0)
            if "amount" in mapping and not paid_in and not withdrawn:
                amount = _money(row.get(mapping["amount"])) or Decimal(0)
                paid_in, withdrawn = (amount, Decimal(0)) if amount >= 0 else (Decimal(0), -amount)
            withdrawn = abs(withdrawn)
            if not paid_in and not withdrawn:
                continue
            direction = "in" if paid_in > 0 else "out"
            details = row.get(mapping.get("details", ""), "") or ""
            occurred = _date(row[mapping["date"]])
            ref = (row.get(mapping.get("ref", ""), "") or "").strip() or f"CSV{index}-{occurred}"
            balance = _money(row.get(mapping.get("balance", "")))
            counterparty = None
            m = re.search(r"(?:from|to)\s*[-:]?\s*(?:\d{6,}\s*-?\s*)?([A-Za-z][A-Za-z .'-]{2,})", details, re.I)
            if m:
                counterparty = m.group(1).strip()[:60]
            txn = MomoTxn(
                provider=provider, ref=ref, direction=direction,
                category=_category_from_details(details, direction),
                amount=to_minor(paid_in if direction == "in" else withdrawn), fee=0,
                balance=to_minor(balance) if balance is not None else None,
                counterparty=counterparty, counterparty_phone=None, occurred_at=occurred, raw=details[:300],
            )
        except (ValueError, KeyError):
            bad += 1
            continue
        if save(conn, business, txn):
            added.append(txn)
        else:
            dup += 1
    return ImportResult(added, dup, bad)


def history(conn: sqlite3.Connection, business: Business, since_iso: str | None = None) -> list[sqlite3.Row]:
    sql = "SELECT * FROM momo_transactions WHERE business_id = ?"
    params: list = [business.id]
    if since_iso:
        sql += " AND occurred_at >= ?"
        params.append(since_iso)
    return conn.execute(sql + " ORDER BY occurred_at, id", params).fetchall()
