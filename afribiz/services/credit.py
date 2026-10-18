"""A simple, explainable credit profile built from mobile-money history and the business's own records.

Why this design:
- Thin-file borrowers have no bureau history, but mobile money leaves a verifiable cash-flow
  trail. Lenders across the continent (digital lenders, MFIs, SACCOs) underwrite on the same
  signals: how much comes in, how steadily, from how many different payers, whether there is a
  buffer, and how earlier loans were repaid.
- Every point in the score maps to a factor the owner can see and improve. A black-box score
  that the owner can't act on does not help them build credit.
- This is a *pre-qualification profile* the owner chooses to share, not a lending decision.
  Lenders should calibrate it on their own repayment outcomes before relying on it.

Score: 0-100, grade A (strong) to E (not enough evidence yet).
"""

from __future__ import annotations

import sqlite3
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from ..timeutil import iso, parse_iso, utcnow
from .businesses import Business

WINDOW_DAYS = 180
MIN_DAYS = 30
MIN_TXNS = 15

INFLOW_CATEGORIES = {"received", "deposit"}
EXCLUDED_OUT = {"withdraw"}  # converting to cash is not spending


@dataclass
class Factor:
    key: str
    label: str
    points: float
    max_points: float
    detail: str
    tip: str | None = None

    @property
    def ratio(self) -> float:
        return self.points / self.max_points if self.max_points else 0.0


@dataclass
class CreditProfile:
    score: int
    grade: str
    sufficient_data: bool
    days_of_history: int
    txn_count: int
    avg_monthly_inflow: int
    avg_monthly_outflow: int
    avg_monthly_business_income: int
    unique_payers_90d: int
    suggested_loan: int
    suggested_monthly_repayment: int
    factors: list[Factor] = field(default_factory=list)
    as_of: str = ""

    @property
    def strengths(self) -> list[Factor]:
        return [f for f in self.factors if f.ratio >= 0.7]

    @property
    def improvements(self) -> list[Factor]:
        return sorted((f for f in self.factors if f.ratio < 0.6 and f.tip), key=lambda f: f.ratio)


def _grade(score: int, sufficient: bool) -> str:
    if not sufficient:
        return "E"
    if score >= 75:
        return "A"
    if score >= 60:
        return "B"
    if score >= 45:
        return "C"
    return "D"


def _scale(value: float, lo: float, hi: float) -> float:
    """Linear 0..1 between lo and hi (inverted if lo > hi)."""
    if hi == lo:
        return 1.0 if value >= hi else 0.0
    t = (value - lo) / (hi - lo)
    return max(0.0, min(1.0, t))


def build_profile(conn: sqlite3.Connection, business: Business, as_of: datetime | None = None) -> CreditProfile:
    now = as_of or utcnow()
    start = now - timedelta(days=WINDOW_DAYS)
    rows = conn.execute(
        "SELECT * FROM momo_transactions WHERE business_id = ? AND occurred_at >= ? AND occurred_at <= ? ORDER BY occurred_at",
        (business.id, iso(start), iso(now)),
    ).fetchall()
    ledger_days = conn.execute(
        "SELECT COUNT(DISTINCT substr(occurred_at, 1, 10)) AS d FROM transactions "
        "WHERE business_id = ? AND voided = 0 AND occurred_at >= ?",
        (business.id, iso(now - timedelta(days=30))),
    ).fetchone()["d"]
    collections = conn.execute(
        """
        SELECT COALESCE(SUM(CASE WHEN kind = 'sale' THEN amount - paid_amount END), 0) AS credit,
               COALESCE(SUM(CASE WHEN kind = 'payment_in' THEN amount END), 0) AS collected
        FROM transactions WHERE business_id = ? AND voided = 0 AND occurred_at >= ?
        """,
        (business.id, iso(start)),
    ).fetchone()

    txns = [dict(r) for r in rows]
    for t in txns:
        t["dt"] = parse_iso(t["occurred_at"])

    first = txns[0]["dt"] if txns else now
    days_history = max((now - first).days, 0) if txns else 0
    months = max(days_history / 30.0, 1.0)

    inflows = [t for t in txns if t["direction"] == "in" and t["category"] in INFLOW_CATEGORIES]
    business_in = [t for t in txns if t["direction"] == "in" and t["category"] == "received"]
    outflows = [t for t in txns if t["direction"] == "out" and t["category"] not in EXCLUDED_OUT]
    total_in = sum(t["amount"] for t in inflows)
    total_out = sum(t["amount"] + (t["fee"] or 0) for t in outflows)
    avg_in = int(total_in / months)
    avg_out = int(total_out / months)
    avg_business_in = int(sum(t["amount"] for t in business_in) / months)

    sufficient = days_history >= MIN_DAYS and len(txns) >= MIN_TXNS
    factors: list[Factor] = []

    # 1. History length (10)
    factors.append(Factor(
        "history", "Length of money history", 10 * _scale(days_history, 14, 180), 10,
        f"{days_history} days of mobile-money records",
        "Keep forwarding your M-PESA/MoMo messages, or import a statement. Six months of history is ideal.",
    ))

    # 2. Activity: share of weeks with income (15)
    weeks_total = max(len({((first + timedelta(days=d)).isocalendar()[0], (first + timedelta(days=d)).isocalendar()[1])
                           for d in range(days_history + 1)}), 1) if txns else 1
    weeks_active = len({(t["dt"].isocalendar()[0], t["dt"].isocalendar()[1]) for t in inflows})
    activity = min(weeks_active / weeks_total, 1.0) if txns else 0.0
    factors.append(Factor(
        "activity", "Regular income", 15 * _scale(activity, 0.3, 0.95), 15,
        f"money came in during {weeks_active} of {weeks_total} weeks",
        "Ask customers to pay by mobile money (your till/paybill or number) so every sale leaves a record.",
    ))

    # 3. Stability: coefficient of variation of weekly inflow (15)
    weekly: dict[tuple[int, int], int] = {}
    for t in inflows:
        k = (t["dt"].isocalendar()[0], t["dt"].isocalendar()[1])
        weekly[k] = weekly.get(k, 0) + t["amount"]
    if len(weekly) >= 3:
        values = list(weekly.values()) + [0] * max(weeks_total - len(weekly), 0)
        mean = statistics.mean(values)
        cv = statistics.pstdev(values) / mean if mean else 3.0
    else:
        cv = 3.0
    factors.append(Factor(
        "stability", "Steady income", 15 * _scale(cv, 1.5, 0.35), 15,
        f"weekly income varies by about {min(cv, 3.0) * 100:.0f}%",
        "Smooth out slow weeks: keep a small stock of fast-moving items and remind regular customers.",
    ))

    # 4. Cash-flow surplus: in vs out (20)
    ratio = (total_in / total_out) if total_out else (2.0 if total_in else 0.0)
    factors.append(Factor(
        "surplus", "Money in vs money out", 20 * _scale(ratio, 0.9, 1.4), 20,
        f"for every 100 that went out, {ratio * 100:.0f} came in",
        "Cut non-essential spending from the business line and track every expense here.",
    ))

    # 5. Customer diversity: unique payers in last 90 days + concentration (15)
    since90 = now - timedelta(days=90)
    payers: dict[str, int] = {}
    for t in business_in:
        if t["dt"] >= since90:
            key = (t["counterparty_phone"] or t["counterparty"] or "unknown").lower()
            payers[key] = payers.get(key, 0) + t["amount"]
    unique_payers = len(payers)
    top_share = (max(payers.values()) / sum(payers.values())) if payers else 1.0
    diversity = 0.7 * _scale(unique_payers, 2, 25) + 0.3 * _scale(top_share, 0.8, 0.25)
    factors.append(Factor(
        "diversity", "Many different customers", 15 * diversity, 15,
        f"{unique_payers} different payers in 90 days; the biggest is {top_share * 100:.0f}% of income",
        "Depending on one or two customers is risky. Reach new ones through WhatsApp Status and referrals.",
    ))

    # 6. Buffer: average balance vs monthly spending (10)
    balances = [t["balance"] for t in txns if t["balance"] is not None]
    avg_balance = max(statistics.mean(balances), 0) if balances else 0
    buffer_months = (avg_balance / avg_out) if avg_out else (1.0 if avg_balance else 0.0)
    factors.append(Factor(
        "buffer", "Savings buffer", 10 * _scale(buffer_months, 0.05, 0.5), 10,
        f"your balance usually covers about {buffer_months * 30:.0f} days of spending",
        "Leave a little money in the account each day; a buffer of two weeks of spending is a good target.",
    ))

    # 7. Loan behaviour (10): repaid vs borrowed; neutral when no loans.
    borrowed = sum(t["amount"] for t in txns if t["category"] == "loan")
    repaid = sum(t["amount"] for t in txns if t["category"] == "loan_repayment")
    if borrowed:
        repay_ratio = repaid / borrowed
        loan_points = 10 * _scale(repay_ratio, 0.5, 1.0)
        detail = f"repaid {repay_ratio * 100:.0f}% of {len([t for t in txns if t['category'] == 'loan'])} recent loan(s)"
    else:
        loan_points = 6.0
        detail = "no mobile loans seen (neutral)"
    factors.append(Factor(
        "loans", "Loan repayment", loan_points, 10, detail,
        "Repay small digital loans on time; each one repaid on time builds your record.",
    ))

    # 8. Record keeping in AfriBiz (5) + debt collection (bonus within cap)
    credit_given = collections["credit"] or 0
    collected = collections["collected"] or 0
    keeping = _scale(ledger_days, 3, 20)
    collection = (collected / credit_given) if credit_given else 1.0
    factors.append(Factor(
        "records", "Good records", 5 * (0.7 * keeping + 0.3 * min(collection, 1.0)), 5,
        f"you recorded business on {ledger_days} of the last 30 days",
        "Record sales and expenses every day; lenders trust businesses that know their numbers.",
    ))

    score = int(round(sum(f.points for f in factors)))
    if not sufficient:
        score = min(score, 40)

    surplus = max(avg_in - avg_out, 0)
    monthly_repayment = int(min(surplus * 0.35, avg_business_in * 0.15 if avg_business_in else surplus * 0.35))
    multiplier = {"A": 4, "B": 3, "C": 2, "D": 1}.get(_grade(score, sufficient), 0)
    suggested = int(monthly_repayment * multiplier * 0.9)  # leave room for interest/fees
    return CreditProfile(
        score=score, grade=_grade(score, sufficient), sufficient_data=sufficient,
        days_of_history=days_history, txn_count=len(txns), avg_monthly_inflow=avg_in, avg_monthly_outflow=avg_out,
        avg_monthly_business_income=avg_business_in, unique_payers_90d=unique_payers,
        suggested_loan=suggested if sufficient else 0,
        suggested_monthly_repayment=monthly_repayment if sufficient else 0,
        factors=factors, as_of=iso(now.astimezone(timezone.utc)),
    )


def grant_consent(conn: sqlite3.Connection, business: Business, kind: str = "credit_share") -> None:
    conn.execute("INSERT INTO consents (business_id, kind) VALUES (?, ?)", (business.id, kind))


def revoke_consent(conn: sqlite3.Connection, business: Business, kind: str = "credit_share") -> None:
    conn.execute(
        "UPDATE consents SET revoked_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') "
        "WHERE business_id = ? AND kind = ? AND revoked_at IS NULL",
        (business.id, kind),
    )


def has_consent(conn: sqlite3.Connection, business: Business, kind: str = "credit_share") -> bool:
    return conn.execute(
        "SELECT 1 FROM consents WHERE business_id = ? AND kind = ? AND revoked_at IS NULL LIMIT 1",
        (business.id, kind),
    ).fetchone() is not None
