"""Summaries: what came in, what went out, what the business actually earned."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from ..timeutil import period_bounds
from . import customers as customers_svc
from . import inventory
from .businesses import Business


@dataclass
class Summary:
    label: str
    sales: int = 0
    sales_count: int = 0
    credit_given: int = 0
    collected_on_debts: int = 0
    cost_of_goods: int = 0
    expenses: int = 0
    stock_purchases: int = 0
    owner_draws: int = 0
    capital_in: int = 0
    expense_by_category: dict[str, int] = field(default_factory=dict)
    top_items: list[tuple[str, float, int]] = field(default_factory=list)  # (name, qty, revenue)
    receivables: int = 0
    stock_value: int = 0
    low_stock: list[str] = field(default_factory=list)

    @property
    def gross_profit(self) -> int:
        return self.sales - self.cost_of_goods

    @property
    def net_profit(self) -> int:
        return self.gross_profit - self.expenses

    @property
    def cash_in(self) -> int:
        return self.sales - self.credit_given + self.collected_on_debts + self.capital_in

    @property
    def cash_out(self) -> int:
        return self.expenses + self.stock_purchases + self.owner_draws

    @property
    def net_cash(self) -> int:
        return self.cash_in - self.cash_out

    @property
    def margin_pct(self) -> float | None:
        return (self.gross_profit / self.sales * 100) if self.sales else None

    @property
    def has_activity(self) -> bool:
        return any((self.sales, self.expenses, self.stock_purchases, self.collected_on_debts, self.owner_draws))


def summary(conn: sqlite3.Connection, business: Business, period: str = "today") -> Summary:
    start, end, label = period_bounds(period, business.timezone)
    s = Summary(label=label)
    rows = conn.execute(
        "SELECT kind, amount, paid_amount, cost_amount, category FROM transactions "
        "WHERE business_id = ? AND voided = 0 AND occurred_at >= ? AND occurred_at < ?",
        (business.id, start, end),
    ).fetchall()
    for r in rows:
        kind = r["kind"]
        if kind == "sale":
            s.sales += r["amount"]
            s.sales_count += 1
            s.credit_given += r["amount"] - r["paid_amount"]
            s.cost_of_goods += r["cost_amount"]
        elif kind == "payment_in":
            s.collected_on_debts += r["amount"]
        elif kind == "expense":
            s.expenses += r["amount"]
            cat = r["category"] or "other"
            s.expense_by_category[cat] = s.expense_by_category.get(cat, 0) + r["amount"]
        elif kind == "purchase":
            s.stock_purchases += r["amount"]
        elif kind == "owner_draw":
            s.owner_draws += r["amount"]
        elif kind == "capital_in":
            s.capital_in += r["amount"]
    s.top_items = [
        (r["description"], r["qty"], r["revenue"])
        for r in conn.execute(
            """
            SELECT l.description, SUM(l.quantity) AS qty, CAST(SUM(l.quantity * l.unit_price) AS INTEGER) AS revenue
            FROM transaction_lines l JOIN transactions t ON t.id = l.transaction_id
            WHERE t.business_id = ? AND t.kind = 'sale' AND t.voided = 0 AND t.occurred_at >= ? AND t.occurred_at < ?
            GROUP BY l.description ORDER BY revenue DESC LIMIT 5
            """,
            (business.id, start, end),
        )
    ]
    s.receivables = sum(d.balance for d in customers_svc.debtors(conn, business))
    items = inventory.list_items(conn, business)
    s.stock_value = sum(i.stock_value for i in items if i.tracked)
    s.low_stock = [i.name for i in items if i.low]
    return s


def daily_series(conn: sqlite3.Connection, business: Business, days: int = 30) -> list[tuple[str, int, int]]:
    """(local_date, sales, expenses+purchases) for the dashboard chart."""
    from datetime import timedelta

    from ..timeutil import local_today, parse_iso, tz

    zone = tz(business.timezone)
    today = local_today(business.timezone)
    buckets = {(today - timedelta(days=i)).isoformat(): [0, 0] for i in range(days - 1, -1, -1)}
    start, _, _ = period_bounds("last30", business.timezone) if days == 30 else period_bounds("all", business.timezone)
    for r in conn.execute(
        "SELECT kind, amount, occurred_at FROM transactions WHERE business_id = ? AND voided = 0 AND occurred_at >= ?",
        (business.id, start),
    ):
        day = parse_iso(r["occurred_at"]).astimezone(zone).date().isoformat()
        if day not in buckets:
            continue
        if r["kind"] == "sale":
            buckets[day][0] += r["amount"]
        elif r["kind"] in {"expense", "purchase"}:
            buckets[day][1] += r["amount"]
    return [(d, v[0], v[1]) for d, v in buckets.items()]
