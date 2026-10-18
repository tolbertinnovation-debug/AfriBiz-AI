"""Server-rendered pages. No JavaScript framework: fast on cheap phones and slow networks."""

from __future__ import annotations

import sqlite3
from html import escape

from ..config import Settings
from ..money import format_money
from ..services import credit, customers, inventory, ledger, reminders, reports
from ..services.businesses import Business


def _layout(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<link rel="stylesheet" href="/static/style.css">
</head><body><main class="wrap">{body}</main></body></html>"""


def home(settings: Settings) -> str:
    sim = '<a class="btn" href="/simulator">Try the WhatsApp simulator</a>' if settings.enable_simulator else ""
    return _layout("AfriBiz AI", f"""
<header class="hero">
  <p class="eyebrow">AfriBiz AI</p>
  <h1>A business assistant that lives in WhatsApp</h1>
  <p class="lede">Records, stock, invoices, debt reminders and a credit profile built from mobile money,
  for the market trader, the kiosk, the tailor and the salon. Works by text, voice note and USSD.</p>
  {sim}
</header>
<section class="grid3">
  <div class="card"><h3>Talk, don't type forms</h3><p>“sold 3 bread @ 60”, “Mary paid 300”, “nimetumia 200 nauli”, or a voice note.</p></div>
  <div class="card"><h3>Get paid</h3><p>Polite reminders with one-tap WhatsApp links, and PDF invoices to forward.</p></div>
  <div class="card"><h3>Build credit</h3><p>Forward M-PESA / MoMo / Airtel SMS to build an explainable credit profile you control.</p></div>
</section>""")


def _bar_chart(series: list[tuple[str, int, int]], currency: str) -> str:
    if not series:
        return ""
    peak = max(max(s, c) for _, s, c in series) or 1
    w, h, pad = 640, 180, 24
    bw = (w - pad) / len(series)
    bars = []
    for i, (day, sales, costs) in enumerate(series):
        x = pad + i * bw
        sh = (sales / peak) * (h - 30)
        ch = (costs / peak) * (h - 30)
        bars.append(
            f'<rect x="{x + 1:.1f}" y="{h - sh:.1f}" width="{bw * 0.45:.1f}" height="{sh:.1f}" class="b-sales">'
            f'<title>{day}: sales {escape(format_money(sales, currency))}</title></rect>'
            f'<rect x="{x + 1 + bw * 0.45:.1f}" y="{h - ch:.1f}" width="{bw * 0.45:.1f}" height="{ch:.1f}" class="b-costs">'
            f'<title>{day}: spent {escape(format_money(costs, currency))}</title></rect>'
        )
    first, last = series[0][0][5:], series[-1][0][5:]
    return (f'<svg viewBox="0 0 {w} {h + 18}" class="chart" role="img" aria-label="Daily sales and spending, last 30 days">'
            f'<line x1="{pad}" y1="{h}" x2="{w}" y2="{h}" class="axis"/>{"".join(bars)}'
            f'<text x="{pad}" y="{h + 14}" class="tick">{first}</text>'
            f'<text x="{w}" y="{h + 14}" class="tick" text-anchor="end">{last}</text></svg>'
            '<p class="legend"><span class="sw sw-sales"></span>Sales <span class="sw sw-costs"></span>Spending</p>')


def _factor_rows(profile: credit.CreditProfile) -> str:
    rows = []
    for f in profile.factors:
        pct = int(round(f.ratio * 100))
        rows.append(f'<div class="factor"><div class="factor-head"><span>{escape(f.label)}</span>'
                    f'<span class="muted">{f.points:.0f}/{f.max_points:.0f}</span></div>'
                    f'<div class="meter"><span style="width:{pct}%"></span></div>'
                    f'<p class="muted small">{escape(f.detail)}</p></div>')
    return "".join(rows)


def dashboard(conn: sqlite3.Connection, biz: Business, settings: Settings, token: str, imported: str | None) -> str:
    cur = biz.currency

    def m(v: int) -> str:
        return escape(format_money(v, cur))

    month = reports.summary(conn, biz, "month")
    today = reports.summary(conn, biz, "today")
    profile = credit.build_profile(conn, biz)
    debtors = customers.debtors(conn, biz)
    items = inventory.list_items(conn, biz)
    recent = ledger.recent(conn, biz, 12)
    series = reports.daily_series(conn, biz, 30)

    notice = f'<p class="notice">Imported {escape(imported)} new transactions.</p>' if imported else ""
    tiles = f"""
<section class="tiles">
  <div class="tile"><p class="label">Sales today</p><p class="value">{m(today.sales)}</p></div>
  <div class="tile"><p class="label">Sales this month</p><p class="value">{m(month.sales)}</p></div>
  <div class="tile"><p class="label">Profit this month</p><p class="value {'neg' if month.net_profit < 0 else ''}">{m(month.net_profit)}</p></div>
  <div class="tile"><p class="label">Customers owe you</p><p class="value">{m(month.receivables)}</p></div>
  <div class="tile"><p class="label">Stock value</p><p class="value">{m(month.stock_value)}</p></div>
  <div class="tile"><p class="label">Credit profile</p><p class="value">{profile.score}<span class="unit">/100 · {profile.grade}</span></p></div>
</section>"""

    debtor_parts = []
    for d in debtors[:20]:
        draft = reminders.draft_for(conn, biz, d)
        action = (f'<a href="{escape(draft.link)}" target="_blank" rel="noopener">Remind</a>' if draft.link
                  else '<span class="muted">no number</span>')
        debtor_parts.append(f"<tr><td>{escape(d.customer.name)}</td><td class='num'>{m(d.balance)}</td>"
                            f"<td class='muted'>{draft.days_outstanding}d</td><td>{action}</td></tr>")
    debtor_rows = "".join(debtor_parts) or "<tr><td colspan=4 class='muted'>Nobody owes you money.</td></tr>"

    stock_rows = "".join(
        f"<tr class='{'low' if i.low else ''}'><td>{escape(i.name)}</td>"
        f"<td class='num'>{f'{i.quantity:g}' if i.tracked else '–'}</td>"
        f"<td class='num'>{m(i.sell_price) if i.sell_price else '–'}</td>"
        f"<td class='num'>{m(i.unit_cost) if i.unit_cost else '–'}</td></tr>"
        for i in sorted(items, key=lambda x: (not x.low, x.name.lower()))[:40]
    ) or "<tr><td colspan=4 class='muted'>No items yet.</td></tr>"

    kinds = {"sale": "Sale", "expense": "Expense", "purchase": "Stock", "payment_in": "Payment",
             "owner_draw": "Personal", "capital_in": "Capital"}
    recent_rows = "".join(
        f"<tr><td class='nowrap'>{escape(r['occurred_at'][5:10])}</td><td>{kinds.get(r['kind'], r['kind'])}</td>"
        f"<td>{escape(r['items'] or r['note'] or r['category'] or '')}{(' · ' + escape(r['customer_name'])) if r['customer_name'] else ''}</td>"
        f"<td class='num'>{m(r['amount'])}</td></tr>"
        for r in recent
    ) or "<tr><td colspan=4 class='muted'>No records yet.</td></tr>"

    expense_rows = "".join(
        f"<tr><td>{escape(k.replace('_', ' '))}</td><td class='num'>{m(v)}</td></tr>"
        for k, v in sorted(month.expense_by_category.items(), key=lambda kv: kv[1], reverse=True)
    ) or "<tr><td colspan=2 class='muted'>No expenses this month.</td></tr>"

    body = f"""
<header class="bar"><div><p class="eyebrow">AfriBiz dashboard</p><h1>{escape(biz.display_name)}</h1>
<p class="muted">{escape(biz.business_type or '')} · {escape(cur)}</p></div></header>
{notice}{tiles}
<section class="card"><h2>Last 30 days</h2>{_bar_chart(series, cur)}</section>
<section class="cols">
  <div class="card"><h2>This month</h2><table>
    <tr><td>Sales</td><td class="num">{m(month.sales)}</td></tr>
    <tr><td>Cost of goods sold</td><td class="num">{m(month.cost_of_goods)}</td></tr>
    <tr><td>Gross profit</td><td class="num">{m(month.gross_profit)}</td></tr>
    <tr><td>Expenses</td><td class="num">{m(month.expenses)}</td></tr>
    <tr class="total"><td>Profit</td><td class="num">{m(month.net_profit)}</td></tr>
    <tr><td>Cash in / out</td><td class="num">{m(month.cash_in)} / {m(month.cash_out)}</td></tr>
    <tr><td>Taken for personal use</td><td class="num">{m(month.owner_draws)}</td></tr>
  </table><h3>Expenses by type</h3><table>{expense_rows}</table></div>
  <div class="card"><h2>Who owes you</h2><table><tr><th>Customer</th><th class="num">Balance</th><th>Age</th><th></th></tr>{debtor_rows}</table></div>
</section>
<section class="cols">
  <div class="card"><h2>Stock</h2><table><tr><th>Item</th><th class="num">Qty</th><th class="num">Price</th><th class="num">Cost</th></tr>{stock_rows}</table></div>
  <div class="card"><h2>Recent records</h2><table>{recent_rows}</table></div>
</section>
<section class="cols">
  <div class="card"><h2>Credit profile</h2>
    <p class="big">{profile.score}<span class="unit">/100 · grade {profile.grade}</span></p>
    <p class="muted">{'Based on' if profile.sufficient_data else 'Still building:'} {profile.txn_count} mobile money records over {profile.days_of_history} days.</p>
    {_factor_rows(profile)}
  </div>
  <div class="card"><h2>Add mobile money history</h2>
    <form method="post" action="/d/{biz.id}/{escape(token)}/import" enctype="multipart/form-data">
      <label>Statement CSV (M-PESA, MoMo, bank)<input type="file" name="file" accept=".csv,text/csv"></label>
      <label>…or paste SMS messages<textarea name="sms_text" rows="6" placeholder="QK12AB34CD Confirmed. You have received Ksh500.00 from …"></textarea></label>
      <button class="btn" type="submit">Import</button>
    </form>
    <p class="muted small">Duplicates are skipped automatically. Your history stays private unless you send <b>share score</b>.</p>
  </div>
</section>"""
    return _layout(f"{biz.display_name}: AfriBiz", body)


def lender(conn: sqlite3.Connection, biz: Business) -> str:
    p = credit.build_profile(conn, biz)
    cur = biz.currency

    def m(v: int) -> str:
        return escape(format_money(v, cur))

    body = f"""
<header class="bar"><div><p class="eyebrow">Shared credit profile</p><h1>{escape(biz.display_name)}</h1>
<p class="muted">{escape(biz.business_type or '')} · {escape(biz.country or '')} · generated {escape(p.as_of[:10])}</p></div></header>
<section class="tiles">
  <div class="tile"><p class="label">AfriBiz score</p><p class="value">{p.score}<span class="unit">/100 · {p.grade}</span></p></div>
  <div class="tile"><p class="label">Avg monthly inflow</p><p class="value">{m(p.avg_monthly_inflow)}</p></div>
  <div class="tile"><p class="label">Avg monthly outflow</p><p class="value">{m(p.avg_monthly_outflow)}</p></div>
  <div class="tile"><p class="label">Distinct payers (90d)</p><p class="value">{p.unique_payers_90d}</p></div>
  <div class="tile"><p class="label">History</p><p class="value">{p.days_of_history}<span class="unit"> days</span></p></div>
  <div class="tile"><p class="label">Indicative affordable loan</p><p class="value">{m(p.suggested_loan)}</p></div>
</section>
<section class="card"><h2>Factors</h2>{_factor_rows(p)}</section>
<section class="card"><p class="muted small">Shared by the business owner with explicit consent, which they can withdraw at any time.
This profile summarises self-reported records and forwarded mobile-money confirmations. It is an indicator for
pre-qualification, not a credit decision; verify with your own checks (e.g. a provider statement) and calibrate
on your portfolio's repayment outcomes.</p></section>"""
    return _layout(f"{biz.display_name}: credit profile", body)


def error_page(message: str) -> str:
    return _layout("Error", f'<section class="card"><h2>Something went wrong</h2><p>{escape(message)}</p>'
                            '<p><a href="javascript:history.back()">Go back</a></p></section>')
