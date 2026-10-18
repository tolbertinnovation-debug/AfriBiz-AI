"""The ledger: sales (cash and credit), expenses, stock purchases, customer payments, undo.

Accounting model (kept deliberately simple, cash-basis with cost of goods):
- sale        -> revenue; `paid_amount` of it was collected now, the rest is owed by the customer
- payment_in  -> money collected later against a customer's balance
- purchase    -> stock bought (cash out, not an expense: it becomes cost of goods when sold)
- expense     -> operating cost (rent, transport, wages...)
- owner_draw  -> money the owner took for personal use (not a business expense!)
- capital_in  -> money the owner put into the business
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from ..timeutil import iso, utcnow
from . import customers as customers_svc
from . import inventory
from .businesses import Business
from .customers import Customer
from .inventory import Item


class LedgerError(Exception):
    """A problem the user can fix (shown back to them as a question)."""


class MissingPrice(LedgerError):
    def __init__(self, item_name: str):
        super().__init__(item_name)
        self.item_name = item_name


class UnknownCustomer(LedgerError):
    def __init__(self, name: str, suggestions: list[str]):
        super().__init__(name)
        self.name = name
        self.suggestions = suggestions


@dataclass
class SaleLineInput:
    name: str
    quantity: float = 1
    unit_price: int | None = None  # minor units
    line_total: int | None = None  # minor units


@dataclass
class SaleLine:
    item: Item
    quantity: float
    unit_price: int
    unit_cost: int

    @property
    def total(self) -> int:
        return int(round(self.quantity * self.unit_price))


@dataclass
class SaleResult:
    transaction_id: int
    total: int
    paid: int
    customer: Customer | None
    lines: list[SaleLine]
    customer_balance: int = 0
    low_stock: list[Item] = field(default_factory=list)
    negative_stock: list[Item] = field(default_factory=list)

    @property
    def owed(self) -> int:
        return self.total - self.paid

    @property
    def profit(self) -> int:
        return self.total - sum(int(round(line.quantity * line.unit_cost)) for line in self.lines)


@dataclass
class PurchaseResult:
    transaction_id: int
    item: Item
    quantity: float
    total: int


@dataclass
class PaymentResult:
    transaction_id: int
    customer: Customer
    amount: int
    balance: int


EXPENSE_CATEGORIES: dict[str, tuple[str, ...]] = {
    "rent": ("rent", "kodi", "shop rent", "stall"),
    "transport": ("transport", "fare", "matatu", "boda", "okada", "keke", "taxi", "fuel", "petrol", "diesel",
                  "nauli", "danfo", "trotro", "delivery", "uber", "bolt"),
    "wages": ("wage", "wages", "salary", "salaries", "worker", "staff", "mshahara", "casual", "helper"),
    "utilities": ("electricity", "power", "light", "token", "tokens", "water", "maji", "umeme", "nepa", "gas"),
    "airtime": ("airtime", "data", "bundle", "bundles", "credit", "recharge", "internet", "wifi"),
    "supplies": ("packaging", "bags", "nylon", "paper", "supplies", "cleaning", "soap"),
    "repairs": ("repair", "repairs", "maintenance", "fix", "mechanic"),
    "fees": ("license", "licence", "permit", "levy", "tax", "council", "market fee", "ticket", "kra", "firs", "gra"),
    "marketing": ("advert", "advertising", "marketing", "flyer", "promotion", "printing"),
    "food": ("food", "lunch", "chakula", "breakfast", "tea"),
    "loan_repayment": ("loan", "repayment", "fuliza", "chama", "susu", "esusu", "ajo"),
    "mobile_money_fees": ("charges", "transaction cost", "withdrawal fee", "mpesa fee"),
}

PERSONAL_WORDS = ("personal", "house", "home", "family", "school fees", "kids", "children", "self", "mimi")


def categorize_expense(text: str) -> str:
    t = (text or "").lower()
    for category, words in EXPENSE_CATEGORIES.items():
        if any(w in t for w in words):
            return category
    return "other"


def looks_personal(text: str) -> bool:
    t = (text or "").lower()
    return any(w in t for w in PERSONAL_WORDS)


def _insert_tx(conn: sqlite3.Connection, business: Business, **values) -> int:
    values.setdefault("occurred_at", iso(utcnow()))
    values["business_id"] = business.id
    cols = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    cur = conn.execute(f"INSERT INTO transactions ({cols}) VALUES ({marks})", tuple(values.values()))
    return int(cur.lastrowid)


def record_sale(
    conn: sqlite3.Connection,
    business: Business,
    lines: list[SaleLineInput],
    *,
    customer_name: str | None = None,
    customer_phone: str | None = None,
    amount_paid: int | None = None,
    on_credit: bool = False,
    method: str = "cash",
    total_override: int | None = None,
    due_date: str | None = None,
    occurred_at: str | None = None,
    source: str = "chat",
    external_ref: str | None = None,
    note: str | None = None,
) -> SaleResult:
    if not lines:
        raise LedgerError("no items")
    resolved: list[SaleLine] = []
    for line in lines:
        qty = line.quantity if line.quantity and line.quantity > 0 else 1
        item = inventory.find_or_create(conn, business, line.name)
        price = line.unit_price
        if price is None and line.line_total is not None:
            price = int(round(line.line_total / qty))
        if price is None and total_override is not None and len(lines) == 1:
            price = int(round(total_override / qty))
        if price is None and item.sell_price:
            price = item.sell_price
        if price is None:
            raise MissingPrice(item.name)
        if not item.sell_price:
            item = inventory.set_price(conn, item, sell_price=price)
        resolved.append(SaleLine(item=item, quantity=qty, unit_price=price, unit_cost=item.unit_cost))

    total = sum(line.total for line in resolved)
    if total_override is not None and len(resolved) > 1:
        total = total_override  # a bundle/discount price for several items

    customer = None
    if customer_name or customer_phone:
        customer = customers_svc.find_or_create(conn, business, customer_name or customer_phone or "", customer_phone)

    if on_credit and customer is None:
        raise LedgerError("credit sale needs a customer name")
    if amount_paid is None:
        paid = 0 if on_credit else total
    else:
        paid = max(0, min(amount_paid, total))
    if paid < total and customer is None:
        raise LedgerError("credit sale needs a customer name")

    cost = sum(int(round(line.quantity * line.unit_cost)) for line in resolved)
    tx_id = _insert_tx(
        conn, business, kind="sale", amount=total, paid_amount=paid, cost_amount=cost,
        customer_id=customer.id if customer else None, method="credit" if paid == 0 else method,
        source=source, external_ref=external_ref, due_date=due_date, note=note,
        **({"occurred_at": occurred_at} if occurred_at else {}),
    )
    low: list[Item] = []
    negative: list[Item] = []
    for line in resolved:
        conn.execute(
            "INSERT INTO transaction_lines (transaction_id, item_id, description, quantity, unit_price, unit_cost) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (tx_id, line.item.id, line.item.name, line.quantity, line.unit_price, line.unit_cost),
        )
        updated = inventory.move(conn, business, line.item, -line.quantity, "sale", tx_id)
        if updated.tracked and updated.quantity < 0:
            negative.append(updated)
        elif updated.low:
            low.append(updated)
    result = SaleResult(tx_id, total, paid, customer, resolved, low_stock=low, negative_stock=negative)
    if customer:
        result.customer_balance = customers_svc.balance(conn, customer.id)
    return result


def record_expense(
    conn: sqlite3.Connection, business: Business, amount: int, description: str, *,
    category: str | None = None, method: str = "cash", personal: bool | None = None,
    occurred_at: str | None = None, source: str = "chat", external_ref: str | None = None,
) -> tuple[int, str, str]:
    """Returns (transaction_id, kind, category). Personal spending is booked as an owner draw."""
    if amount <= 0:
        raise LedgerError("amount must be positive")
    is_personal = looks_personal(description) if personal is None else personal
    kind = "owner_draw" if is_personal else "expense"
    cat = "personal" if is_personal else (category or categorize_expense(description))
    tx_id = _insert_tx(
        conn, business, kind=kind, amount=amount, category=cat, method=method, note=description.strip() or None,
        source=source, external_ref=external_ref, **({"occurred_at": occurred_at} if occurred_at else {}),
    )
    return tx_id, kind, cat


def record_purchase(
    conn: sqlite3.Connection, business: Business, item_name: str, quantity: float, *,
    unit_cost: int | None = None, total_cost: int | None = None, sell_price: int | None = None,
    unit: str | None = None, supplier: str | None = None, method: str = "cash", source: str = "chat",
) -> PurchaseResult:
    if quantity <= 0:
        raise LedgerError("quantity must be positive")
    item = inventory.find_or_create(conn, business, item_name, unit)
    if unit_cost is None and total_cost is not None:
        unit_cost = int(round(total_cost / quantity))
    total = int(round((unit_cost or 0) * quantity)) if total_cost is None else total_cost
    tx_id = None
    if total > 0:
        tx_id = _insert_tx(
            conn, business, kind="purchase", amount=total, cost_amount=total, category="stock", method=method,
            note=f"{inventory.fmt_qty(quantity)} {item.name}" + (f" from {supplier}" if supplier else ""),
            source=source,
        )
        conn.execute(
            "INSERT INTO transaction_lines (transaction_id, item_id, description, quantity, unit_price, unit_cost) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (tx_id, item.id, item.name, quantity, unit_cost or 0, unit_cost or 0),
        )
    item = inventory.restock(conn, business, item, quantity, unit_cost, tx_id)
    if sell_price:
        item = inventory.set_price(conn, item, sell_price=sell_price)
    return PurchaseResult(tx_id or 0, item, quantity, total)


def record_payment(
    conn: sqlite3.Connection, business: Business, customer_name: str, amount: int, *,
    method: str = "cash", customer_phone: str | None = None, occurred_at: str | None = None,
    source: str = "chat", external_ref: str | None = None,
) -> PaymentResult:
    if amount <= 0:
        raise LedgerError("amount must be positive")
    customer = customers_svc.find(conn, business, customer_name) if customer_name else None
    if customer is None and customer_phone:
        customer = customers_svc.find_by_phone(conn, business, customer_phone)
    if customer is None:
        names = [d.customer.name for d in customers_svc.debtors(conn, business)][:5]
        raise UnknownCustomer(customer_name, names)
    tx_id = _insert_tx(
        conn, business, kind="payment_in", amount=amount, customer_id=customer.id, method=method,
        source=source, external_ref=external_ref, **({"occurred_at": occurred_at} if occurred_at else {}),
    )
    _refresh_invoice_status(conn, business, customer.id)
    return PaymentResult(tx_id, customer, amount, customers_svc.balance(conn, customer.id))


def record_capital(conn: sqlite3.Connection, business: Business, amount: int, note: str = "", source: str = "chat") -> int:
    return _insert_tx(conn, business, kind="capital_in", amount=amount, note=note or None, source=source)


def _refresh_invoice_status(conn: sqlite3.Connection, business: Business, customer_id: int) -> None:
    """Apply the customer's payments to their invoices oldest-first and update each status."""
    invoices = conn.execute(
        "SELECT id, total, transaction_id FROM invoices WHERE business_id = ? AND customer_id = ? ORDER BY created_at, id",
        (business.id, customer_id),
    ).fetchall()
    if not invoices:
        return
    paid_at_sale = {
        r["id"]: r["paid_amount"]
        for r in conn.execute(
            "SELECT id, paid_amount FROM transactions WHERE customer_id = ? AND kind = 'sale' AND voided = 0",
            (customer_id,),
        )
    }
    pool = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS s FROM transactions WHERE customer_id = ? AND kind = 'payment_in' AND voided = 0",
        (customer_id,),
    ).fetchone()["s"]
    for inv in invoices:
        already = paid_at_sale.get(inv["transaction_id"], 0)
        due = max(inv["total"] - already, 0)
        applied = min(pool, due)
        pool -= applied
        if due == 0 or applied >= due:
            status = "paid"
        elif applied > 0 or already > 0:
            status = "partial"
        else:
            status = "unpaid"
        conn.execute("UPDATE invoices SET status = ? WHERE id = ?", (status, inv["id"]))


@dataclass
class UndoResult:
    kind: str
    amount: int
    note: str | None


def undo_last(conn: sqlite3.Connection, business: Business) -> UndoResult | None:
    row = conn.execute(
        "SELECT * FROM transactions WHERE business_id = ? AND voided = 0 ORDER BY id DESC LIMIT 1",
        (business.id,),
    ).fetchone()
    if row is None:
        return None
    conn.execute("UPDATE transactions SET voided = 1 WHERE id = ?", (row["id"],))
    for mv in conn.execute(
        "SELECT item_id, delta FROM stock_movements WHERE transaction_id = ?", (row["id"],)
    ).fetchall():
        item = inventory.get(conn, mv["item_id"])
        inventory.move(conn, business, item, -mv["delta"], "undo", row["id"])
    conn.execute("DELETE FROM invoices WHERE transaction_id = ?", (row["id"],))
    if row["customer_id"]:
        _refresh_invoice_status(conn, business, row["customer_id"])
    note = row["note"]
    if row["kind"] == "sale":
        names = [r["description"] for r in conn.execute(
            "SELECT description FROM transaction_lines WHERE transaction_id = ?", (row["id"],))]
        note = ", ".join(names)
    return UndoResult(row["kind"], row["amount"], note)


def recent(conn: sqlite3.Connection, business: Business, limit: int = 10) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT t.*, c.name AS customer_name,
               (SELECT GROUP_CONCAT(description, ', ') FROM transaction_lines l WHERE l.transaction_id = t.id) AS items
        FROM transactions t LEFT JOIN customers c ON c.id = t.customer_id
        WHERE t.business_id = ? AND t.voided = 0 ORDER BY t.occurred_at DESC, t.id DESC LIMIT ?
        """,
        (business.id, limit),
    ).fetchall()
