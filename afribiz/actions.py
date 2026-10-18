"""Execute intents against the books and phrase the result for a chat screen.

Both the rule engine and the AI layer end up here, so every write path is validated the same
way and every confirmation looks the same no matter how the message was understood.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any

from . import learning
from .config import Settings
from .i18n import LANGUAGES, t
from .money import CURRENCIES, format_money, from_minor, to_minor
from .nlu.intents import Intent, LineSpec
from .replies import Document, Reply
from .security import sign
from .services import businesses, credit, customers, inventory, invoices, ledger, momo, reminders, reports
from .services.businesses import Business
from .services.inventory import fmt_qty


@dataclass
class Context:
    conn: sqlite3.Connection
    business: Business
    settings: Settings
    channel: str = "chat"

    def money(self, amount_minor: int) -> str:
        return format_money(amount_minor, self.business.currency)

    @property
    def lang(self) -> str:
        return self.business.language


# ---------------------------------------------------------------------------------------------
# Helpers


def _to_minor(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return to_minor(Decimal(str(value)))


def _line_inputs(ctx: Context, lines: list[LineSpec]) -> list[ledger.SaleLineInput]:
    out: list[ledger.SaleLineInput] = []
    for line in lines:
        qty = float(line.quantity or 1)
        price = _to_minor(line.price)
        unit_price = line_total = None
        if price is not None:
            if line.price_kind == "unit":
                unit_price = price
            elif line.price_kind == "total":
                line_total = price
            else:  # bare number: use what we know about the item to decide what it means
                known = inventory.find(ctx.conn, ctx.business, line.name)
                if known and known.sell_price and qty > 1 and price == known.sell_price:
                    unit_price = price
                elif (known and known.sell_price and qty == 1 and line.price is not None
                      and Decimal(str(line.price)) == int(Decimal(str(line.price)))
                      and 1 < Decimal(str(line.price)) <= 100 and price * 2 < known.sell_price):
                    # "nimeuza mkate 3": the number is a quantity, not a price
                    qty, unit_price = float(line.price), known.sell_price
                elif qty > 1:
                    line_total = price
                else:
                    unit_price = price
        out.append(ledger.SaleLineInput(name=line.name, quantity=qty, unit_price=unit_price, line_total=line_total))
    return out


def _serialize_lines(lines: list[LineSpec]) -> list[dict[str, Any]]:
    return [{**asdict(line), "price": str(line.price) if line.price is not None else None} for line in lines]


def _deserialize_lines(raw: list[dict[str, Any]]) -> list[LineSpec]:
    return [LineSpec(**{**r, "price": Decimal(r["price"]) if r.get("price") is not None else None}) for r in raw]


def link(ctx: Context, kind: str, business_id: int) -> str:
    token = sign(ctx.settings.secret_key, kind, business_id)
    base = ctx.settings.public_base_url.rstrip("/")
    return f"{base}/{'d' if kind == 'dashboard' else 'lender'}/{business_id}/{token}"


def file_link(ctx: Context, path: str) -> str:
    token = sign(ctx.settings.secret_key, "file", path, 32)
    from urllib.parse import quote

    return f"{ctx.settings.public_base_url.rstrip('/')}/files?p={quote(path)}&t={token}"


# ---------------------------------------------------------------------------------------------
# Recording


def record_sale(ctx: Context, intent: Intent) -> list[Reply]:
    lines: list[LineSpec] = intent.get("lines") or []
    try:
        result = ledger.record_sale(
            ctx.conn, ctx.business, _line_inputs(ctx, lines),
            customer_name=intent.get("customer"), customer_phone=intent.get("customer_phone"),
            amount_paid=_to_minor(intent.get("amount_paid")), on_credit=bool(intent.get("on_credit")),
            method=intent.get("method") or "cash", total_override=_to_minor(intent.get("total")),
            source=ctx.channel,
        )
    except ledger.MissingPrice as exc:
        businesses.set_pending(ctx.conn, ctx.business, {
            "type": "need_price", "item": exc.item_name, "intent": {
                **{k: v for k, v in intent.args.items() if k != "lines"},
                "amount_paid": str(intent.get("amount_paid")) if intent.get("amount_paid") is not None else None,
                "total": str(intent.get("total")) if intent.get("total") is not None else None,
                "lines": _serialize_lines(lines),
            },
        })
        return [Reply(f"💬 How much is one *{exc.item_name}*? Reply with the price, e.g. *120*")]
    except ledger.LedgerError:
        return [Reply("👤 Who is this credit sale for? Say e.g. _sold 2 sugar to Mary on credit_")]

    items = "\n".join(
        f"• {fmt_qty(line.quantity)} × {line.item.name} @ {ctx.money(line.unit_price)} = {ctx.money(line.total)}"
        for line in result.lines
    )
    msg = [f"✅ *Sale recorded*: {ctx.money(result.total)}", items]
    if result.lines and all(line.unit_cost for line in result.lines):
        msg.append(f"📈 Profit on this sale: {ctx.money(result.profit)}")
    if result.customer:
        if result.owed > 0:
            msg.append(
                f"📒 {result.customer.name} paid {ctx.money(result.paid)}, owes {ctx.money(result.owed)} "
                f"(total balance {ctx.money(result.customer_balance)})"
            )
        else:
            msg.append(f"👤 Customer: {result.customer.name}")
    for item in result.low_stock:
        msg.append(f"⚠️ *{item.name}* is running low: {fmt_qty(item.quantity)} left")
    for item in result.negative_stock:
        msg.append(f"❗ Stock for *{item.name}* is below zero ({fmt_qty(item.quantity)}). "
                   f"Did you restock? Send _count {item.name.lower()} <number>_")
    msg.append("_Wrong? Send *undo*_")
    return [Reply("\n".join(msg))]


def resume_with_price(ctx: Context, pending: dict[str, Any], price: Decimal) -> list[Reply]:
    data = pending["intent"]
    lines = _deserialize_lines(data["lines"])
    for line in lines:
        if line.price is None and line.name.lower().startswith(pending["item"].lower()[:3]):
            line.price, line.price_kind = price, "unit"
            break
    else:
        for line in lines:
            if line.price is None:
                line.price, line.price_kind = price, "unit"
                break
    args = {**data, "lines": lines}
    args["amount_paid"] = Decimal(data["amount_paid"]) if data.get("amount_paid") else None
    args["total"] = Decimal(data["total"]) if data.get("total") else None
    businesses.set_pending(ctx.conn, ctx.business, None)
    return record_sale(ctx, Intent("record_sale", args))


def record_expense(ctx: Context, intent: Intent) -> list[Reply]:
    amount = _to_minor(intent.get("amount"))
    if not amount or amount <= 0:
        return [Reply("How much was it? e.g. _spent 200 transport_")]
    description = intent.get("description") or "expense"
    _, kind, category = ledger.record_expense(
        ctx.conn, ctx.business, amount, description, category=intent.get("category"),
        personal=intent.get("personal"), method=intent.get("method") or "cash", source=ctx.channel,
    )
    if kind == "owner_draw":
        month = reports.summary(ctx.conn, ctx.business, "month")
        text = (f"🏠 Recorded {ctx.money(amount)} taken for personal use (owner's drawing, not a business cost).\n"
                f"This month you have taken {ctx.money(month.owner_draws)} in total.")
        if month.gross_profit > 0 and month.owner_draws > month.net_profit:
            text += "\n⚠️ That is more than the business earned this month, so you are eating into stock money."
        return [Reply(text + "\n_Wrong? Send *undo*_")]
    return [Reply(f"🧾 *Expense recorded*: {ctx.money(amount)} for {description} _({category.replace('_', ' ')})_\n"
                  "_Wrong? Send *undo*_")]


def record_purchase(ctx: Context, intent: Intent) -> list[Reply]:
    line: LineSpec = intent.get("line")
    price = _to_minor(line.price)
    unit_cost = total_cost = None
    if price is not None:
        if line.price_kind == "unit":
            unit_cost = price
        else:
            total_cost = price
    result = ledger.record_purchase(
        ctx.conn, ctx.business, line.name, float(line.quantity or 1), unit_cost=unit_cost, total_cost=total_cost,
        sell_price=_to_minor(intent.get("sell_price")), unit=line.unit, supplier=intent.get("supplier"),
        method=intent.get("method") or "cash", source=ctx.channel,
    )
    item = result.item
    msg = [f"📦 *Stock added*: {fmt_qty(result.quantity)} {item.name}"
           + (f" for {ctx.money(result.total)}" if result.total else "")]
    if item.unit_cost:
        msg.append(f"Cost per unit: {ctx.money(item.unit_cost)}")
    if item.sell_price:
        margin = item.sell_price - item.unit_cost
        msg.append(f"Selling price: {ctx.money(item.sell_price)}"
                   + (f" → profit {ctx.money(margin)} each ({margin / item.sell_price * 100:.0f}% margin)"
                      if item.unit_cost else ""))
        if item.unit_cost and margin <= 0:
            msg.append("⚠️ You are selling at or below cost! Send _price "
                       f"{item.name.lower()} <new price>_")
    else:
        msg.append(f"💡 Set a selling price: _price {item.name.lower()} <amount>_")
    msg.append(f"Now in stock: {fmt_qty(item.quantity)}")
    return [Reply("\n".join(msg))]


def record_payment(ctx: Context, intent: Intent) -> list[Reply]:
    amount = _to_minor(intent.get("amount"))
    if not amount:
        return [Reply("How much did they pay? e.g. _Mary paid 300_")]
    try:
        result = ledger.record_payment(
            ctx.conn, ctx.business, intent.get("customer") or "", amount,
            method=intent.get("method") or "cash", customer_phone=intent.get("customer_phone"), source=ctx.channel,
        )
    except ledger.UnknownCustomer as exc:
        hint = f" People who owe you: {', '.join(exc.suggestions)}." if exc.suggestions else ""
        return [Reply(f"🤔 I don't have a customer called *{exc.name}*.{hint}")]
    if result.balance > 0:
        status = f"Still owes {ctx.money(result.balance)}."
    elif result.balance == 0:
        status = "🎉 Fully paid up!"
    else:
        status = f"They have paid {ctx.money(-result.balance)} in advance."
    return [Reply(f"💰 *Payment recorded*: {result.customer.name} paid {ctx.money(amount)}. {status}\n"
                  "_Wrong? Send *undo*_")]


def capital(ctx: Context, intent: Intent) -> list[Reply]:
    amount = _to_minor(intent.get("amount"))
    if not amount:
        return [Reply("How much did you put into the business? e.g. _capital 5000_")]
    ledger.record_capital(ctx.conn, ctx.business, amount, source=ctx.channel)
    return [Reply(f"🏦 Recorded {ctx.money(amount)} of your own money put into the business (capital).")]


def undo(ctx: Context, intent: Intent) -> list[Reply]:
    result = ledger.undo_last(ctx.conn, ctx.business)
    if result is None:
        return [Reply(t("undo_none", ctx.lang))]
    label = {"sale": "sale", "expense": "expense", "purchase": "stock purchase", "payment_in": "payment",
             "owner_draw": "personal withdrawal", "capital_in": "capital"}.get(result.kind, result.kind)
    return [Reply(f"↩️ Removed the last {label}: {ctx.money(result.amount)}" + (f" ({result.note})" if result.note else ""))]


# ---------------------------------------------------------------------------------------------
# Stock


def stock(ctx: Context, intent: Intent) -> list[Reply]:
    if intent.get("item"):
        item = inventory.find(ctx.conn, ctx.business, intent.get("item"))
        if item is None:
            return [Reply(f"I don't have *{intent.get('item')}* yet. Add it: _bought 10 {intent.get('item')} for 1000_")]
        lines = [f"📦 *{item.name}*: {fmt_qty(item.quantity)} {item.unit} in stock"]
        if item.unit_cost:
            lines.append(f"Cost: {ctx.money(item.unit_cost)} each")
        if item.sell_price:
            lines.append(f"Price: {ctx.money(item.sell_price)} each")
        lines.append(f"Reorder level: {fmt_qty(item.reorder_level)}")
        return [Reply("\n".join(lines))]
    items = inventory.list_items(ctx.conn, ctx.business)
    if intent.get("low_only"):
        items = [i for i in items if i.low]
        if not items:
            return [Reply("✅ Nothing is running low.")]
    if not items:
        return [Reply(t("no_stock", ctx.lang))]
    tracked = [i for i in items if i.tracked]
    rows = []
    for item in sorted(items, key=lambda i: (not i.low, i.name.lower())):
        flag = "⚠️ " if item.low else ""
        qty = fmt_qty(item.quantity) if item.tracked else "–"
        price = f" @ {ctx.money(item.sell_price)}" if item.sell_price else ""
        rows.append(f"{flag}{item.name}: {qty}{price}")
    value = sum(i.stock_value for i in tracked)
    footer = f"\n💼 Stock value at cost: {ctx.money(value)}" if value else ""
    return [Reply("📦 *Your stock*\n" + "\n".join(rows[:40]) + footer)]


def stock_count(ctx: Context, intent: Intent) -> list[Reply]:
    item = inventory.find(ctx.conn, ctx.business, intent.get("item"))
    if item is None:
        item = inventory.find_or_create(ctx.conn, ctx.business, intent.get("item"))
    before = item.quantity
    counted = float(intent.get("quantity"))
    item = inventory.set_count(ctx.conn, ctx.business, item, counted)
    diff = counted - before
    if not before and not diff:
        return [Reply(f"📋 {item.name}: counted {fmt_qty(counted)}.")]
    if abs(diff) < 1e-9:
        return [Reply(f"✅ {item.name}: count matches the records ({fmt_qty(counted)}).")]
    if diff < 0 and before > 0:
        loss = int(round(-diff * item.unit_cost))
        extra = f" That's about {ctx.money(loss)} at cost." if loss else ""
        return [Reply(f"📋 {item.name}: records said {fmt_qty(before)}, you counted {fmt_qty(counted)}. "
                      f"*{fmt_qty(-diff)} missing.*{extra}\nCheck for unrecorded sales, damage or theft.")]
    return [Reply(f"📋 {item.name}: stock updated to {fmt_qty(counted)} (was {fmt_qty(before)}).")]


def reorder_level(ctx: Context, intent: Intent) -> list[Reply]:
    item = inventory.find_or_create(ctx.conn, ctx.business, intent.get("item"))
    item = inventory.set_reorder_level(ctx.conn, item, float(intent.get("level")))
    return [Reply(f"🔔 I'll warn you when *{item.name}* drops to {fmt_qty(item.reorder_level)}.")]


def set_price(ctx: Context, intent: Intent) -> list[Reply]:
    price = _to_minor(intent.get("price"))
    if not price:
        return [Reply("What price? e.g. _price rice 160_")]
    item = inventory.find_or_create(ctx.conn, ctx.business, intent.get("item"))
    item = inventory.set_price(ctx.conn, item, sell_price=price)
    extra = ""
    if item.unit_cost:
        margin = price - item.unit_cost
        extra = f"\nProfit each: {ctx.money(margin)} ({margin / price * 100:.0f}% margin)"
        if margin <= 0:
            extra += "\n⚠️ This is at or below your cost price."
    return [Reply(f"🏷️ *{item.name}* now sells at {ctx.money(price)}.{extra}")]


# ---------------------------------------------------------------------------------------------
# Customers, debts, invoices


def debtors(ctx: Context, intent: Intent) -> list[Reply]:
    rows = customers.debtors(ctx.conn, ctx.business)
    if not rows:
        return [Reply(t("no_debtors", ctx.lang))]
    lines = []
    for d in rows[:25]:
        draft = reminders.draft_for(ctx.conn, ctx.business, d)
        age = f", {draft.days_outstanding} days" if draft.days_outstanding else ""
        lines.append(f"• {d.customer.name}: {ctx.money(d.balance)}{age}")
    total = sum(d.balance for d in rows)
    return [Reply.of(f"📒 *Who owes you* ({len(rows)})\n" + "\n".join(lines) + f"\n\n*Total: {ctx.money(total)}*",
                     ("remind", "Remind them"), ("report", "Report"))]


def remind(ctx: Context, intent: Intent) -> list[Reply]:
    drafts = reminders.drafts(ctx.conn, ctx.business, intent.get("customer"))
    if not drafts:
        if intent.get("customer"):
            return [Reply(f"*{intent.get('customer')}* doesn't owe you anything. 🎉")]
        return [Reply(t("no_debtors", ctx.lang))]
    out: list[Reply] = []
    header = "📨 *Reminders ready.* Tap a link to send from your own WhatsApp, or copy the message:"
    out.append(Reply(header))
    for draft in drafts[:8]:
        note = " _(reminded in the last 3 days)_" if draft.recently_reminded else ""
        body = f"*{draft.debtor.customer.name}* · {ctx.money(draft.debtor.balance)}{note}\n\n{draft.message}"
        if draft.link:
            body += f"\n\n👉 {draft.link}"
        else:
            body += (f"\n\n💡 Add their number to get a one-tap link: "
                     f"_phone {draft.debtor.customer.name.split()[0].lower()} 07XXXXXXXX_")
        out.append(Reply(body))
        reminders.log(ctx.conn, ctx.business, draft, "draft")
    if len(drafts) > 8:
        out.append(Reply(f"…and {len(drafts) - 8} more. Send _remind <name>_ for one person."))
    return out


def set_customer_phone(ctx: Context, intent: Intent) -> list[Reply]:
    phone = businesses.normalize_phone(intent.get("phone") or "")
    if len(phone) < 9:
        return [Reply("That number looks too short. Use e.g. _phone Juma 0712345678_")]
    if phone.startswith("0") and ctx.business.phone:
        # local format -> international using the owner's country code
        from .money import COUNTRY_BY_DIAL_CODE

        for code in sorted(COUNTRY_BY_DIAL_CODE, key=len, reverse=True):
            if ctx.business.phone.startswith(code):
                phone = code + phone[1:]
                break
    customer = customers.find_or_create(ctx.conn, ctx.business, intent.get("customer"))
    customers.set_phone(ctx.conn, customer, phone)
    return [Reply(f"📇 Saved +{phone} for {customer.name}.")]


def invoice(ctx: Context, intent: Intent) -> list[Reply]:
    try:
        inv = invoices.create(
            ctx.conn, ctx.business, intent.get("customer"), _line_inputs(ctx, intent.get("lines") or []),
            customer_phone=intent.get("phone"), due_in_days=int(intent.get("due_days") or 7),
            amount_paid=_to_minor(intent.get("amount_paid")) or 0, data_dir=ctx.settings.data_dir,
        )
    except ledger.MissingPrice as exc:
        return [Reply(f"💬 What is the price of *{exc.item_name}*? Try again like _invoice Juma 2 {exc.item_name} @ 150_")]
    text = invoices.as_text(ctx.business, inv)
    replies = [Reply(text)]
    if inv.file_path:
        replies.append(Reply(
            text=f"📄 Invoice {inv.number} as PDF",
            document=Document(path=inv.file_path, filename=f"{inv.number}.pdf", caption=f"Invoice {inv.number}",
                              url=file_link(ctx, inv.file_path)),
        ))
    if inv.customer.phone:
        from urllib.parse import quote

        replies.append(Reply(f"👉 Send it to {inv.customer.name}: https://wa.me/{inv.customer.phone}?text={quote(text)}"))
    return replies


# ---------------------------------------------------------------------------------------------
# Reports


CATEGORY_LABELS = {"loan_repayment": "loan repayment", "mobile_money_fees": "mobile money fees"}


def report(ctx: Context, intent: Intent) -> list[Reply]:
    period = intent.get("period") or "today"
    s = reports.summary(ctx.conn, ctx.business, period)
    title = f"📊 *{ctx.business.display_name}: {s.label}*"
    if not s.has_activity:
        return [Reply(f"{title}\nNo records yet for {s.label}. Start with _sold 2 bread @ 50_.")]
    lines = [title, f"Sales: *{ctx.money(s.sales)}* ({s.sales_count})"]
    if s.credit_given:
        lines.append(f"  of which on credit: {ctx.money(s.credit_given)}")
    if s.cost_of_goods:
        lines.append(f"Cost of goods sold: {ctx.money(s.cost_of_goods)}")
        lines.append(f"Gross profit: {ctx.money(s.gross_profit)}" + (f" ({s.margin_pct:.0f}%)" if s.margin_pct is not None else ""))
    if s.expenses:
        top = sorted(s.expense_by_category.items(), key=lambda kv: kv[1], reverse=True)[:3]
        detail = ", ".join(f"{CATEGORY_LABELS.get(k, k)} {ctx.money(v)}" for k, v in top)
        lines.append(f"Expenses: {ctx.money(s.expenses)} ({detail})")
    if s.cost_of_goods or s.expenses:
        emoji = "🟢" if s.net_profit >= 0 else "🔴"
        lines.append(f"{emoji} *Profit: {ctx.money(s.net_profit)}*")
    lines.append("")
    lines.append(f"💵 Cash in: {ctx.money(s.cash_in)} · Cash out: {ctx.money(s.cash_out)}")
    if s.stock_purchases:
        lines.append(f"  (stock bought: {ctx.money(s.stock_purchases)})")
    if s.collected_on_debts:
        lines.append(f"  (debts collected: {ctx.money(s.collected_on_debts)})")
    if s.owner_draws:
        lines.append(f"  (taken for personal use: {ctx.money(s.owner_draws)})")
    if s.top_items:
        best = ", ".join(f"{name} ({fmt_qty(qty)})" for name, qty, _ in s.top_items[:3])
        lines.append(f"🏆 Best sellers: {best}")
    if s.receivables:
        lines.append(f"📒 Customers owe you: {ctx.money(s.receivables)}")
    if s.low_stock:
        lines.append(f"⚠️ Running low: {', '.join(s.low_stock[:5])}")
    if not s.cost_of_goods and s.sales:
        lines.append("💡 Tip: record stock purchases with their cost so I can show your real profit.")
    return [Reply.of("\n".join(lines), ("report week", "This week"), ("report month", "This month"))]


def recent(ctx: Context, intent: Intent) -> list[Reply]:
    rows = ledger.recent(ctx.conn, ctx.business, 10)
    if not rows:
        return [Reply("No records yet.")]
    icons = {"sale": "🛒", "expense": "🧾", "purchase": "📦", "payment_in": "💰", "owner_draw": "🏠", "capital_in": "🏦"}
    out = []
    for r in rows:
        who = f" · {r['customer_name']}" if r["customer_name"] else ""
        what = r["items"] or r["note"] or r["category"] or ""
        out.append(f"{icons.get(r['kind'], '•')} {r['occurred_at'][5:10]} {ctx.money(r['amount'])} {what}{who}")
    return [Reply("🕘 *Recent records*\n" + "\n".join(out))]


def dashboard(ctx: Context, intent: Intent) -> list[Reply]:
    return [Reply(f"🖥️ Your private dashboard (don't share this link):\n{link(ctx, 'dashboard', ctx.business.id)}")]


# ---------------------------------------------------------------------------------------------
# Mobile money and credit


PROVIDER_NAMES = {
    "mpesa": "M-PESA", "mtn_momo": "MTN MoMo", "airtel_money": "Airtel Money", "telecel_cash": "Telecel Cash",
    "tigo_pesa": "Mixx by Yas", "orange_money": "Orange Money", "wave": "Wave", "opay": "OPay",
    "moniepoint": "Moniepoint", "palmpay": "PalmPay", "bank_alert": "Bank alert", "mobile_money": "Mobile money",
}


def momo_import(ctx: Context, intent: Intent) -> list[Reply]:
    text = intent.get("text") or ""
    result = momo.import_sms_blob(ctx.conn, ctx.business, text)
    if not result.added:
        if result.duplicates:
            return [Reply("📩 I already have this mobile money message. Nothing new added.")]
        return [Reply("I couldn't read that as a mobile money message. Forward the full SMS, unedited.")]
    replies: list[Reply] = []
    if len(result.added) > 1:
        ins = sum(x.amount for x in result.added if x.direction == "in")
        outs = sum(x.amount for x in result.added if x.direction == "out")
        replies.append(Reply(
            f"📩 Added *{len(result.added)}* mobile money records (in {ctx.money(ins)}, out {ctx.money(outs)})."
            + (f" Skipped {result.duplicates} duplicates." if result.duplicates else "")
            + "\nSend *score* to see your credit profile."
        ))
        return replies
    txn = result.added[0]
    labels = {"received": "received from", "sent": "sent to", "paybill": "paid to", "buygoods": "paid to",
              "withdraw": "withdrawn at", "deposit": "deposited at", "airtime": "airtime", "loan": "loan from",
              "loan_repayment": "loan repaid", "reversal": "reversal"}
    who = f" {txn.counterparty}" if txn.counterparty else ""
    msg = [f"📩 *{PROVIDER_NAMES.get(txn.provider, txn.provider.replace('_', ' ').title())}*: "
           f"{ctx.money(txn.amount)} {labels.get(txn.category, '')}{who}"]
    warning = _balance_check(ctx, txn)
    if warning:
        msg.append(warning)
    if txn.category == "received":
        debtor = None
        if txn.counterparty_phone:
            debtor = customers.find_by_phone(ctx.conn, ctx.business, txn.counterparty_phone)
        if debtor is None and txn.counterparty:
            debtor = customers.find(ctx.conn, ctx.business, txn.counterparty)
        owes = customers.balance(ctx.conn, debtor.id) if debtor else 0
        businesses.set_pending(ctx.conn, ctx.business, {
            "type": "momo_classify", "amount": txn.amount, "counterparty": txn.counterparty,
            "phone": txn.counterparty_phone, "ref": txn.ref,
            "debtor_id": debtor.id if debtor and owes > 0 else None,
        })
        msg.append("\nWhat was this money for?")
        buttons = [("momo_sale", "1 A sale")]
        if debtor and owes > 0:
            msg.append(f"(*{debtor.name}* owes you {ctx.money(owes)})")
            buttons.append(("momo_debt", "2 Debt payment"))
        buttons.append(("momo_skip", "3 Not business"))
        msg.append("Reply " + " · ".join(title for _, title in buttons))
        replies.append(Reply.of("\n".join(msg), *buttons))
    else:
        msg.append("Saved to your mobile money history ✅")
        if txn.category in {"paybill", "buygoods", "sent"}:
            msg.append("If this was a business cost, also send e.g. _spent "
                       f"{from_minor(txn.amount):g} {(txn.counterparty or 'supplier').lower()}_")
        replies.append(Reply("\n".join(msg)))
    return replies


def _balance_check(ctx: Context, txn) -> str | None:
    if txn.balance is None:
        return None
    prev = ctx.conn.execute(
        "SELECT balance, occurred_at FROM momo_transactions WHERE business_id = ? AND provider = ? AND ref != ? "
        "AND balance IS NOT NULL AND occurred_at <= ? ORDER BY occurred_at DESC, id DESC LIMIT 1",
        (ctx.business.id, txn.provider, txn.ref, txn.occurred_at),
    ).fetchone()
    if prev is None:
        return None
    from .timeutil import parse_iso

    gap_hours = (parse_iso(txn.occurred_at) - parse_iso(prev["occurred_at"])).total_seconds() / 3600
    if gap_hours > 24:
        return None
    sign_ = 1 if txn.direction == "in" else -1
    expected = prev["balance"] + sign_ * txn.amount - (txn.fee or 0)
    if abs(expected - txn.balance) > 100:  # more than 1 unit off
        return (f"⚠️ *Check this payment.* Your last balance was {ctx.money(prev['balance'])}, so after this it should be "
                f"about {ctx.money(expected)}, but the message says {ctx.money(txn.balance)}. If you had no other "
                "transactions in between, confirm on your own line before handing over goods.")
    return None


def momo_classify(ctx: Context, pending: dict[str, Any], choice: str) -> list[Reply]:
    businesses.set_pending(ctx.conn, ctx.business, None)
    amount = int(pending["amount"])
    if choice == "momo_debt" and pending.get("debtor_id"):
        customer = customers.get(ctx.conn, pending["debtor_id"])
        owed = max(customers.balance(ctx.conn, customer.id), 0)
        applied = min(amount, owed) if owed else amount
        result = ledger.record_payment(ctx.conn, ctx.business, customer.name, applied, method="mobile_money",
                                       source="momo", external_ref=pending.get("ref"))
        status = f"still owes {ctx.money(result.balance)}" if result.balance > 0 else "fully paid 🎉"
        text = f"💰 Recorded as {customer.name}'s payment of {ctx.money(applied)}: {status}."
        extra = amount - applied
        if extra > 0:
            ledger.record_sale(ctx.conn, ctx.business,
                               [ledger.SaleLineInput(name="mobile money sale", quantity=1, unit_price=extra)],
                               method="mobile_money", source="momo", customer_name=customer.name)
            text += f"\nThe extra {ctx.money(extra)} was recorded as a new sale to {customer.name}."
        return [Reply(text)]
    if choice == "momo_sale":
        lines = [ledger.SaleLineInput(name="mobile money sale", quantity=1, unit_price=amount)]
        result = ledger.record_sale(ctx.conn, ctx.business, lines, method="mobile_money", source="momo",
                                    external_ref=pending.get("ref"),
                                    customer_name=pending.get("counterparty"), customer_phone=pending.get("phone"))
        return [Reply(f"✅ Recorded a sale of {ctx.money(result.total)} paid by mobile money. "
                      "Tip: next time say what was sold, e.g. _sold 2 rice @ 150 by mpesa_.")]
    return [Reply("👍 Kept in your mobile money history only (not counted as a sale).")]


def credit_score(ctx: Context, intent: Intent) -> list[Reply]:
    p = credit.build_profile(ctx.conn, ctx.business)
    if not p.sufficient_data:
        lines = [
            "📈 *Your AfriBiz credit profile is still growing*",
            f"I have {p.txn_count} mobile money records covering {p.days_of_history} days. "
            f"I need at least {credit.MIN_TXNS} records over {credit.MIN_DAYS} days to give a reliable score.",
            "",
            "*How to build it:*",
            "1. Forward your M-PESA / MoMo / Airtel messages here (you can paste many at once)",
            "2. Or upload a statement on your dashboard (send *dashboard*)",
            "3. Keep recording your sales and expenses daily",
        ]
        if p.txn_count:
            lines.insert(2, f"Preliminary score: {p.score}/100")
        return [Reply("\n".join(lines))]
    bar = "▰" * round(p.score / 10) + "▱" * (10 - round(p.score / 10))
    lines = [
        f"📈 *Your AfriBiz credit profile*: {p.score}/100 (grade *{p.grade}*)",
        bar,
        f"Average money in: {ctx.money(p.avg_monthly_inflow)}/month · out: {ctx.money(p.avg_monthly_outflow)}/month",
        f"Customers paying you (90 days): {p.unique_payers_90d}",
        "",
    ]
    if p.strengths:
        lines.append("💪 *Strengths:* " + "; ".join(f.label for f in p.strengths[:3]))
    if p.improvements:
        lines.append("🛠️ *To improve:*")
        for f in p.improvements[:3]:
            lines.append(f"• {f.label}: {f.detail}. {f.tip}")
    if p.suggested_loan:
        lines.append("")
        lines.append(f"💡 A loan you could likely afford: up to *{ctx.money(p.suggested_loan)}* "
                     f"(about {ctx.money(p.suggested_monthly_repayment)}/month to repay).")
    lines.append("\nSend *share score* to get a link you can give a lender. Only you decide who sees it.")
    return [Reply("\n".join(lines))]


def share_score(ctx: Context, intent: Intent) -> list[Reply]:
    credit.grant_consent(ctx.conn, ctx.business)
    return [Reply(
        "🔐 You agreed to share your credit profile. Give this link only to a lender you trust:\n"
        f"{link(ctx, 'lender', ctx.business.id)}\n\nIt shows your score, cash-flow summary and factors, "
        "not your individual transactions. Send *stop sharing* to switch the link off at any time."
    )]


def unshare_score(ctx: Context, intent: Intent) -> list[Reply]:
    credit.revoke_consent(ctx.conn, ctx.business)
    return [Reply("🔒 Sharing stopped. The lender link no longer works.")]


# ---------------------------------------------------------------------------------------------
# Learning


def send_lesson(ctx: Context, lesson: learning.Lesson) -> Reply:
    learning.mark_sent(ctx.conn, ctx.business, lesson)
    if lesson.quiz:
        businesses.set_pending(ctx.conn, ctx.business, {"type": "quiz", "lesson_id": lesson.id})
    number, total = learning.lesson_position(ctx.settings.content_dir, ctx.lang, lesson)
    return Reply(lesson.as_message(number, total))


def lesson(ctx: Context, intent: Intent) -> list[Reply]:
    query = intent.get("query")
    if query:
        found = learning.find_lesson(ctx.settings.content_dir, ctx.lang, query)
        if found is None:
            return [Reply(f"I couldn't find a lesson about *{query}*. Send *lesson* for the next one.")]
        return [send_lesson(ctx, found)]
    for signal in learning.signals(ctx.conn, ctx.business):
        timely = learning.lesson_for_trigger(ctx.conn, ctx.business, ctx.settings.content_dir, signal)
        if timely:
            return [send_lesson(ctx, timely)]
    nxt = learning.next_lesson(ctx.conn, ctx.business, ctx.settings.content_dir)
    if nxt is None:
        return [Reply(t("lessons_done", ctx.lang))]
    return [send_lesson(ctx, nxt)]


def quiz_answer(ctx: Context, intent: Intent) -> list[Reply]:
    pending = ctx.business.pending_action or {}
    if pending.get("type") != "quiz":
        return [Reply(t("not_understood", ctx.lang))]
    found = learning.get_lesson(ctx.settings.content_dir, pending["lesson_id"])
    businesses.set_pending(ctx.conn, ctx.business, None)
    if not found or not found.quiz:
        return [Reply(t("not_understood", ctx.lang))]
    correct = learning.record_answer(ctx.conn, ctx.business, found, intent.get("letter"))
    key = "quiz_right" if correct else "quiz_wrong"
    return [Reply.of(t(key, ctx.lang, why=found.quiz.explanation, answer=found.quiz.answer),
                     ("lesson", "Next lesson"))]


def progress(ctx: Context, intent: Intent) -> list[Reply]:
    seen, total, right = learning.progress(ctx.conn, ctx.business, ctx.settings.content_dir)
    bar = "▰" * round(10 * seen / max(total, 1)) + "▱" * (10 - round(10 * seen / max(total, 1)))
    return [Reply(f"🎓 *Your learning*: {seen}/{total} lessons\n{bar}\nQuizzes correct: {right}")]


# ---------------------------------------------------------------------------------------------
# Settings and data rights


def help_(ctx: Context, intent: Intent) -> list[Reply]:
    return [Reply(t("help", ctx.lang))]


def set_language(ctx: Context, intent: Intent) -> list[Reply]:
    raw = (intent.get("language") or "").lower().strip()
    aliases = {"english": "en", "swahili": "sw", "kiswahili": "sw", "french": "fr", "français": "fr",
               "francais": "fr", "pidgin": "pcm", "naija": "pcm"}
    code = aliases.get(raw, raw)
    if code not in LANGUAGES:
        return [Reply("Languages: " + ", ".join(f"{k} ({v})" for k, v in LANGUAGES.items()))]
    ctx.business = businesses.update(ctx.conn, ctx.business, language=code)
    return [Reply(t("language_set", code))]


def set_currency(ctx: Context, intent: Intent) -> list[Reply]:
    code = (intent.get("currency") or "").upper().strip()
    if code not in CURRENCIES:
        return [Reply("Unknown currency. Examples: KES, NGN, GHS, UGX, TZS, RWF, ZAR, XOF")]
    ctx.business = businesses.update(ctx.conn, ctx.business, currency=code)
    return [Reply(f"💱 Currency set to {CURRENCIES[code].name} ({code}).")]


def daily_summary(ctx: Context, intent: Intent) -> list[Reply]:
    on = bool(intent.get("on"))
    ctx.business = businesses.update(ctx.conn, ctx.business, daily_summary=int(on))
    return [Reply("🌙 Evening summary switched " + ("on." if on else "off."))]


def delete_data(ctx: Context, intent: Intent) -> list[Reply]:
    businesses.set_pending(ctx.conn, ctx.business, {"type": "confirm_delete"})
    return [Reply(t("confirm_delete", ctx.lang))]


def confirm_delete(ctx: Context, intent: Intent) -> list[Reply]:
    if (ctx.business.pending_action or {}).get("type") != "confirm_delete":
        return [Reply("Send *delete my data* first if you want to delete everything.")]
    lang = ctx.lang
    businesses.delete_all_data(ctx.conn, ctx.business)
    return [Reply(t("deleted", lang))]


HANDLERS = {
    "record_sale": record_sale,
    "record_expense": record_expense,
    "record_purchase": record_purchase,
    "record_payment": record_payment,
    "capital": capital,
    "undo": undo,
    "stock": stock,
    "stock_count": stock_count,
    "reorder_level": reorder_level,
    "set_price": set_price,
    "debtors": debtors,
    "remind": remind,
    "set_customer_phone": set_customer_phone,
    "invoice": invoice,
    "report": report,
    "recent": recent,
    "dashboard": dashboard,
    "momo_import": momo_import,
    "credit_score": credit_score,
    "share_score": share_score,
    "unshare_score": unshare_score,
    "lesson": lesson,
    "quiz_answer": quiz_answer,
    "progress": progress,
    "help": help_,
    "set_language": set_language,
    "set_currency": set_currency,
    "daily_summary": daily_summary,
    "delete_data": delete_data,
    "confirm_delete": confirm_delete,
}


def execute(ctx: Context, intent: Intent) -> list[Reply]:
    handler = HANDLERS.get(intent.name)
    if handler is None:
        return [Reply(t("not_understood", ctx.lang))]
    return handler(ctx, intent)
