"""Invoices: recorded as a credit sale, rendered as WhatsApp text and as a PDF to forward."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from ..money import format_money
from ..timeutil import local_today
from . import customers as customers_svc
from . import ledger
from .businesses import Business
from .customers import Customer


@dataclass
class Invoice:
    id: int
    number: str
    customer: Customer
    total: int
    due_date: str | None
    status: str
    lines: list[tuple[str, float, int, int]]  # description, qty, unit_price, line_total
    paid_now: int = 0
    file_path: str | None = None
    created_at: str = ""


def _next_number(conn: sqlite3.Connection, business: Business) -> str:
    count = conn.execute("SELECT COUNT(*) AS n FROM invoices WHERE business_id = ?", (business.id,)).fetchone()["n"]
    return f"INV-{business.id:03d}-{count + 1:04d}"


def create(
    conn: sqlite3.Connection, business: Business, customer_name: str, lines: list[ledger.SaleLineInput], *,
    customer_phone: str | None = None, due_in_days: int = 7, amount_paid: int = 0, data_dir: str | None = None,
) -> Invoice:
    due = (local_today(business.timezone) + timedelta(days=max(due_in_days, 0))).isoformat()
    sale = ledger.record_sale(
        conn, business, lines, customer_name=customer_name, customer_phone=customer_phone,
        amount_paid=amount_paid, on_credit=amount_paid == 0, due_date=due, note="invoice",
    )
    assert sale.customer is not None
    number = _next_number(conn, business)
    status = "paid" if sale.paid >= sale.total else ("partial" if sale.paid else "unpaid")
    cur = conn.execute(
        "INSERT INTO invoices (business_id, number, customer_id, transaction_id, total, due_date, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (business.id, number, sale.customer.id, sale.transaction_id, sale.total, due, status),
    )
    invoice = Invoice(
        id=int(cur.lastrowid), number=number, customer=sale.customer, total=sale.total, due_date=due,
        status=status, lines=[(ln.item.name, ln.quantity, ln.unit_price, ln.total) for ln in sale.lines],
        paid_now=sale.paid,
    )
    if data_dir:
        path = render_pdf(business, invoice, data_dir)
        if path:
            conn.execute("UPDATE invoices SET file_path = ? WHERE id = ?", (path, invoice.id))
            invoice.file_path = path
    return invoice


def get(conn: sqlite3.Connection, business: Business, number: str) -> Invoice | None:
    row = conn.execute(
        "SELECT * FROM invoices WHERE business_id = ? AND number = ? COLLATE NOCASE", (business.id, number)
    ).fetchone()
    if row is None:
        return None
    lines = [
        (r["description"], r["quantity"], r["unit_price"], int(round(r["quantity"] * r["unit_price"])))
        for r in conn.execute("SELECT * FROM transaction_lines WHERE transaction_id = ?", (row["transaction_id"],))
    ]
    paid = conn.execute("SELECT paid_amount FROM transactions WHERE id = ?", (row["transaction_id"],)).fetchone()
    return Invoice(
        id=row["id"], number=row["number"], customer=customers_svc.get(conn, row["customer_id"]),
        total=row["total"], due_date=row["due_date"], status=row["status"], lines=lines,
        paid_now=paid["paid_amount"] if paid else 0, file_path=row["file_path"], created_at=row["created_at"],
    )


def overdue(conn: sqlite3.Connection, business: Business) -> list[Invoice]:
    today = local_today(business.timezone).isoformat()
    rows = conn.execute(
        "SELECT number FROM invoices WHERE business_id = ? AND status != 'paid' AND due_date < ? ORDER BY due_date",
        (business.id, today),
    ).fetchall()
    return [inv for r in rows if (inv := get(conn, business, r["number"]))]


def as_text(business: Business, invoice: Invoice) -> str:
    cur = business.currency
    out = [
        f"*INVOICE {invoice.number}*",
        f"From: {business.display_name}" + (f" ({business.phone})" if business.phone else ""),
        f"To: {invoice.customer.name}",
        "",
    ]
    for desc, qty, price, total in invoice.lines:
        out.append(f"• {qty:g} x {desc} @ {format_money(price, cur)} = {format_money(total, cur)}")
    out.append("")
    out.append(f"*Total: {format_money(invoice.total, cur)}*")
    if invoice.paid_now:
        out.append(f"Paid: {format_money(invoice.paid_now, cur)}")
        out.append(f"Balance: {format_money(invoice.total - invoice.paid_now, cur)}")
    if invoice.due_date:
        out.append(f"Due: {invoice.due_date}")
    out.append("Thank you for your business!")
    return "\n".join(out)


def _pdf_money(amount: int, currency: str) -> str:
    # The built-in PDF fonts lack the naira and cedi glyphs, so spell those currencies out.
    return format_money(amount, currency).replace("₦", "NGN").replace("GH₵", "GHS").replace("₵", "C")


def render_pdf(business: Business, invoice: Invoice, data_dir: str) -> str | None:
    """Render a one-page PDF. Returns the path, or None if reportlab is not installed."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A5
        from reportlab.lib.units import mm
        from reportlab.pdfgen import canvas
    except ImportError:
        return None

    folder = Path(data_dir) / "invoices" / str(business.id)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{invoice.number}.pdf"
    cur = business.currency
    width, height = A5
    c = canvas.Canvas(str(path), pagesize=A5)
    c.setTitle(f"Invoice {invoice.number}")
    c.setFillColor(colors.HexColor("#0F766E"))
    c.rect(0, height - 28 * mm, width, 28 * mm, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(12 * mm, height - 13 * mm, business.display_name[:40])
    c.setFont("Helvetica", 9)
    c.drawString(12 * mm, height - 19 * mm, f"Tel: +{business.phone}")
    c.setFont("Helvetica-Bold", 14)
    c.drawRightString(width - 12 * mm, height - 13 * mm, "INVOICE")
    c.setFont("Helvetica", 9)
    c.drawRightString(width - 12 * mm, height - 19 * mm, invoice.number)

    c.setFillColor(colors.black)
    y = height - 40 * mm
    c.setFont("Helvetica-Bold", 10)
    c.drawString(12 * mm, y, "Bill to:")
    c.setFont("Helvetica", 10)
    c.drawString(30 * mm, y, invoice.customer.name)
    if invoice.customer.phone:
        c.drawString(30 * mm, y - 5 * mm, f"+{invoice.customer.phone}")
    if invoice.due_date:
        c.drawRightString(width - 12 * mm, y, f"Due: {invoice.due_date}")

    y -= 16 * mm
    c.setFont("Helvetica-Bold", 9)
    cols = (12 * mm, 70 * mm, 95 * mm, width - 12 * mm)
    c.drawString(cols[0], y, "Item")
    c.drawRightString(cols[1] + 10 * mm, y, "Qty")
    c.drawRightString(cols[2] + 15 * mm, y, "Price")
    c.drawRightString(cols[3], y, "Amount")
    c.line(12 * mm, y - 2 * mm, width - 12 * mm, y - 2 * mm)
    c.setFont("Helvetica", 9)
    for desc, qty, price, total in invoice.lines:
        y -= 7 * mm
        c.drawString(cols[0], y, desc[:32])
        c.drawRightString(cols[1] + 10 * mm, y, f"{qty:g}")
        c.drawRightString(cols[2] + 15 * mm, y, _pdf_money(price, cur))
        c.drawRightString(cols[3], y, _pdf_money(total, cur))
    y -= 5 * mm
    c.line(12 * mm, y, width - 12 * mm, y)
    y -= 8 * mm
    c.setFont("Helvetica-Bold", 11)
    c.drawRightString(width - 12 * mm, y, "Total: " + _pdf_money(invoice.total, cur))
    if invoice.paid_now:
        y -= 6 * mm
        c.setFont("Helvetica", 9)
        balance = _pdf_money(invoice.total - invoice.paid_now, cur)
        c.drawRightString(width - 12 * mm, y, f"Balance due: {balance}")
    c.setFont("Helvetica-Oblique", 8)
    c.setFillColor(colors.HexColor("#555555"))
    c.drawString(12 * mm, 12 * mm, "Thank you for your business. Generated with AfriBiz AI.")
    c.showPage()
    c.save()
    return str(path)
