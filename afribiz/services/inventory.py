"""Stock: items, quantities, cost and selling prices, low-stock alerts."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .businesses import Business
from .matching import best_match, tidy_display


@dataclass
class Item:
    id: int
    name: str
    unit: str
    quantity: float
    unit_cost: int
    sell_price: int
    reorder_level: float
    tracked: bool = False

    @property
    def low(self) -> bool:
        return self.tracked and self.quantity <= self.reorder_level

    @property
    def stock_value(self) -> int:
        return int(round(max(self.quantity, 0) * self.unit_cost))


def _from_row(conn: sqlite3.Connection, row: sqlite3.Row) -> Item:
    tracked = conn.execute(
        "SELECT 1 FROM stock_movements WHERE item_id = ? AND reason IN ('restock', 'count') LIMIT 1",
        (row["id"],),
    ).fetchone() is not None
    return Item(
        id=row["id"], name=row["name"], unit=row["unit"], quantity=row["quantity"],
        unit_cost=row["unit_cost"], sell_price=row["sell_price"], reorder_level=row["reorder_level"],
        tracked=tracked,
    )


def get(conn: sqlite3.Connection, item_id: int) -> Item:
    row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        raise LookupError(f"item {item_id} not found")
    return _from_row(conn, row)


def list_items(conn: sqlite3.Connection, business: Business) -> list[Item]:
    rows = conn.execute("SELECT * FROM items WHERE business_id = ? ORDER BY name", (business.id,)).fetchall()
    return [_from_row(conn, r) for r in rows]


def find(conn: sqlite3.Connection, business: Business, name: str) -> Item | None:
    rows = conn.execute("SELECT id, name FROM items WHERE business_id = ?", (business.id,)).fetchall()
    match = best_match(name, {r["id"]: r["name"] for r in rows})
    return get(conn, match) if match else None


def find_or_create(conn: sqlite3.Connection, business: Business, name: str, unit: str | None = None) -> Item:
    item = find(conn, business, name)
    if item:
        return item
    cur = conn.execute(
        "INSERT INTO items (business_id, name, unit) VALUES (?, ?, ?)",
        (business.id, tidy_display(name), unit or "pcs"),
    )
    return get(conn, int(cur.lastrowid))


def move(
    conn: sqlite3.Connection, business: Business, item: Item, delta: float, reason: str,
    transaction_id: int | None = None,
) -> Item:
    conn.execute(
        "INSERT INTO stock_movements (business_id, item_id, transaction_id, delta, reason) VALUES (?, ?, ?, ?, ?)",
        (business.id, item.id, transaction_id, delta, reason),
    )
    conn.execute("UPDATE items SET quantity = quantity + ? WHERE id = ?", (delta, item.id))
    return get(conn, item.id)


def restock(
    conn: sqlite3.Connection, business: Business, item: Item, quantity: float, unit_cost: int | None,
    transaction_id: int | None,
) -> Item:
    """Add stock and update the moving-average unit cost."""
    if unit_cost is not None and unit_cost > 0:
        on_hand = max(item.quantity, 0)
        total_qty = on_hand + quantity
        new_cost = unit_cost if total_qty <= 0 else round((on_hand * item.unit_cost + quantity * unit_cost) / total_qty)
        conn.execute("UPDATE items SET unit_cost = ? WHERE id = ?", (int(new_cost), item.id))
    return move(conn, business, get(conn, item.id), quantity, "restock", transaction_id)


def set_count(conn: sqlite3.Connection, business: Business, item: Item, counted: float) -> Item:
    """Physical stock count: record the difference as an adjustment (shrinkage shows up here)."""
    return move(conn, business, item, counted - item.quantity, "count")


def set_price(conn: sqlite3.Connection, item: Item, sell_price: int | None = None, unit_cost: int | None = None) -> Item:
    if sell_price is not None:
        conn.execute("UPDATE items SET sell_price = ? WHERE id = ?", (sell_price, item.id))
    if unit_cost is not None:
        conn.execute("UPDATE items SET unit_cost = ? WHERE id = ?", (unit_cost, item.id))
    return get(conn, item.id)


def set_reorder_level(conn: sqlite3.Connection, item: Item, level: float) -> Item:
    conn.execute("UPDATE items SET reorder_level = ? WHERE id = ?", (level, item.id))
    return get(conn, item.id)


def low_stock(conn: sqlite3.Connection, business: Business) -> list[Item]:
    return [i for i in list_items(conn, business) if i.low]


def fmt_qty(quantity: float) -> str:
    return f"{quantity:g}"
