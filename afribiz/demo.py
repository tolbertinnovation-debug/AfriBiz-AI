"""Seed a realistic demo business (a Nairobi grocery kiosk) with ~4 months of history."""

from __future__ import annotations

import random
from datetime import timedelta

from .db import Database
from .mobilemoney.parsers import MomoTxn
from .money import to_minor
from .services import businesses, customers, inventory, ledger, momo
from .timeutil import iso, utcnow

ITEMS = [  # name, unit cost, sell price, daily demand
    ("Bread", 50, 60, 12), ("Milk 500ml", 55, 65, 15), ("Sugar 1kg", 160, 190, 4), ("Unga 2kg", 190, 220, 5),
    ("Cooking oil 1L", 300, 350, 2), ("Eggs", 13, 17, 25), ("Tomatoes", 8, 12, 30), ("Soap bar", 70, 90, 3),
    ("Airtime 50", 48, 50, 6), ("Rice 1kg", 140, 170, 3),
]
CUSTOMERS = ["Mary Wanjiku", "John Otieno", "Grace Achieng", "Peter Kamau", "Faith Njeri", "Brian Mutua",
             "Mama Ciku", "Ali Hassan", "Esther Wambui", "Kevin Ochieng", "Lucy Akinyi", "Samuel Kiprop",
             "Joyce Chebet", "Daniel Mwangi", "Agnes Moraa", "Tom Owino", "Rose Nduta", "James Kariuki"]


def seed(db: Database, phone: str = "254700000001", days: int = 120, rng_seed: int = 7) -> int:
    rng = random.Random(rng_seed)
    now = utcnow()
    with db.tx() as conn:
        existing = businesses.get_by_phone(conn, phone)
        if existing:
            businesses.delete_all_data(conn, existing)
        biz, _ = businesses.get_or_create(conn, phone)
        biz = businesses.update(conn, biz, name="Mama Ciku's Kiosk", owner_name="Wanjiru",
                                business_type="groceries", onboarding_step="done")
        for name, cost, price, _ in ITEMS:
            item = inventory.find_or_create(conn, biz, name)
            inventory.set_price(conn, item, sell_price=to_minor(price), unit_cost=to_minor(cost))
        cust = [customers.find_or_create(conn, biz, n, f"2547{rng.randint(10000000, 99999999)}") for n in CUSTOMERS]
        balance = 1500.0
        ref_counter = 0

        def mpesa_ref() -> str:
            nonlocal ref_counter
            ref_counter += 1
            return f"SK{ref_counter:04d}{rng.choice('ABCDEFGHJKLMNPQRSTUVWXYZ')}{rng.randint(100, 999)}X"

        for day in range(days, -1, -1):
            date = now - timedelta(days=day)
            weekday = date.weekday()
            month_end = date.day >= 26 or date.day <= 3
            boost = (1.25 if month_end else 1.0) * (0.75 if weekday == 6 else 1.0)
            # Restock twice a week
            if weekday in (0, 3):
                for name, cost, _price, demand in ITEMS:
                    on_hand = inventory.find(conn, biz, name).quantity
                    qty = max(1, int(demand * 4.5 * rng.uniform(1.0, 1.2) - on_hand))  # top up to ~4 days of demand
                    stamp = iso(date.replace(hour=6, minute=30))
                    res = ledger.record_purchase(conn, biz, name, qty, unit_cost=to_minor(cost), source="demo")
                    if res.transaction_id:
                        conn.execute("UPDATE transactions SET occurred_at = ? WHERE id = ?", (stamp, res.transaction_id))
                spend = sum(c * d * 2.4 for _, c, _, d in ITEMS) * 0.55  # part of the restock paid by M-PESA
                balance -= spend
                momo.save(conn, biz, MomoTxn("mpesa", mpesa_ref(), "out", "buygoods", to_minor(spend), 0,
                                             to_minor(max(balance, 0)), "WHOLESALE DISTRIBUTORS LTD", None,
                                             iso(date.replace(hour=7)), "demo"))
            # Daily sales
            for hour in (8, 11, 14, 17, 19):
                basket = rng.sample(ITEMS, k=rng.randint(3, 5))
                lines = [ledger.SaleLineInput(name=n, quantity=max(1, int(d / 3 * boost * rng.uniform(0.6, 1.5))),
                                              unit_price=to_minor(p)) for n, _, p, d in basket]
                credit_sale = rng.random() < 0.05
                buyer = rng.choice(cust)
                mm = rng.random() < 0.55
                stamp = iso(date.replace(hour=hour, minute=rng.randint(0, 59)))
                sale = ledger.record_sale(conn, biz, lines, customer_name=buyer.name if (credit_sale or mm) else None,
                                          on_credit=credit_sale, method="mobile_money" if mm else "cash",
                                          occurred_at=stamp, source="demo")
                if mm and not credit_sale:
                    balance += sale.total / 100
                    momo.save(conn, biz, MomoTxn("mpesa", mpesa_ref(), "in", "received", sale.total, 0,
                                                 to_minor(balance), buyer.name.upper(), buyer.phone, stamp, "demo"))
            # Expenses
            stamp = iso(date.replace(hour=20))
            if rng.random() < 0.6:
                ledger.record_expense(conn, biz, to_minor(rng.choice([100, 150, 200])), "transport", occurred_at=stamp,
                                      source="demo")
            if date.day == 1:
                ledger.record_expense(conn, biz, to_minor(6000), "shop rent", occurred_at=stamp, source="demo")
                balance -= 6000
                momo.save(conn, biz, MomoTxn("mpesa", mpesa_ref(), "out", "paybill", to_minor(6000), 0,
                                             to_minor(max(balance, 0)), "LANDLORD PROPERTIES", None, stamp, "demo"))
            if weekday == 5:
                ledger.record_expense(conn, biz, to_minor(1500), "for home", personal=True, occurred_at=stamp,
                                      source="demo")
            if weekday == 4 and rng.random() < 0.7:
                withdraw = rng.choice([1000, 2000, 3000])
                balance -= withdraw
                momo.save(conn, biz, MomoTxn("mpesa", mpesa_ref(), "out", "withdraw", to_minor(withdraw), to_minor(29),
                                             to_minor(max(balance, 0)), "AGENT", None, stamp, "demo"))
            # Debt repayments
            if rng.random() < 0.6:
                debtors = customers.debtors(conn, biz)
                if debtors:
                    d = rng.choice(debtors)
                    amount = min(d.balance, to_minor(rng.choice([100, 200, 300, 500])))
                    ledger.record_payment(conn, biz, d.customer.name, amount, method="mobile_money",
                                          occurred_at=iso(date.replace(hour=12)), source="demo")
            while balance < 1500:
                balance += 3000
                momo.save(conn, biz, MomoTxn("mpesa", mpesa_ref(), "in", "deposit", to_minor(3000), 0,
                                             to_minor(balance), "AGENT", None, iso(date.replace(hour=9)), "demo"))
        # One small digital loan, repaid on time
        loan_day = now - timedelta(days=40)
        momo.save(conn, biz, MomoTxn("mpesa", "LOAN0001", "in", "loan", to_minor(5000), 0, None, "M-SHWARI", None,
                                     iso(loan_day), "demo"))
        momo.save(conn, biz, MomoTxn("mpesa", "LOAN0002", "out", "loan_repayment", to_minor(5375), 0, None,
                                     "M-SHWARI", None, iso(loan_day + timedelta(days=28)), "demo"))
        conn.execute("INSERT INTO messages (business_id, direction, channel, body) VALUES (?, 'in', 'demo', 'hi')",
                     (biz.id,))
        return biz.id
