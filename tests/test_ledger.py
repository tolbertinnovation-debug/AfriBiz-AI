import pytest

from afribiz.services import customers, inventory, invoices, ledger, reports
from afribiz.services.ledger import SaleLineInput


def test_sale_reduces_stock_and_computes_profit(db, biz):
    with db.tx() as conn:
        ledger.record_purchase(conn, biz, "bread", 20, total_cost=100000)  # 50 each
        sale = ledger.record_sale(conn, biz, [SaleLineInput("Bread", 3, unit_price=6000)])
        assert sale.total == 18000 and sale.profit == 3000
        assert inventory.find(conn, biz, "bread").quantity == 17


def test_moving_average_cost(db, biz):
    with db.tx() as conn:
        ledger.record_purchase(conn, biz, "rice", 10, unit_cost=10000)
        ledger.record_purchase(conn, biz, "rice", 10, unit_cost=12000)
        assert inventory.find(conn, biz, "rice").unit_cost == 11000


def test_credit_sale_payment_and_balance(db, biz):
    with db.tx() as conn:
        sale = ledger.record_sale(conn, biz, [SaleLineInput("sugar", 2, unit_price=12000)], customer_name="Mary",
                                  on_credit=True)
        assert sale.owed == 24000
        result = ledger.record_payment(conn, biz, "mary", 10000)
        assert result.balance == 14000
        assert [d.customer.name for d in customers.debtors(conn, biz)] == ["Mary"]


def test_credit_sale_requires_customer(db, biz):
    with db.tx() as conn, pytest.raises(ledger.LedgerError):
        ledger.record_sale(conn, biz, [SaleLineInput("sugar", 1, unit_price=100)], on_credit=True)


def test_missing_price_is_reported(db, biz):
    with db.tx() as conn, pytest.raises(ledger.MissingPrice):
        ledger.record_sale(conn, biz, [SaleLineInput("mystery item", 1)])


def test_unknown_customer_payment(db, biz):
    with db.tx() as conn, pytest.raises(ledger.UnknownCustomer):
        ledger.record_payment(conn, biz, "Nobody", 100)


def test_undo_restores_stock(db, biz):
    with db.tx() as conn:
        ledger.record_purchase(conn, biz, "soap", 10, unit_cost=5000)
        ledger.record_sale(conn, biz, [SaleLineInput("soap", 4, unit_price=7000)])
        assert inventory.find(conn, biz, "soap").quantity == 6
        undone = ledger.undo_last(conn, biz)
        assert undone.kind == "sale"
        assert inventory.find(conn, biz, "soap").quantity == 10


def test_personal_spending_is_owner_draw(db, biz):
    with db.tx() as conn:
        _, kind, _ = ledger.record_expense(conn, biz, 50000, "school fees for the kids")
        assert kind == "owner_draw"
        s = reports.summary(conn, biz, "today")
        assert s.expenses == 0 and s.owner_draws == 50000


def test_report_profit_and_cash(db, biz):
    with db.tx() as conn:
        ledger.record_purchase(conn, biz, "bread", 10, unit_cost=5000)
        ledger.record_sale(conn, biz, [SaleLineInput("bread", 4, unit_price=6000)])
        ledger.record_sale(conn, biz, [SaleLineInput("bread", 2, unit_price=6000)], customer_name="Ali", on_credit=True)
        ledger.record_expense(conn, biz, 2000, "transport")
        s = reports.summary(conn, biz, "today")
    assert s.sales == 36000 and s.cost_of_goods == 30000 and s.gross_profit == 6000
    assert s.net_profit == 4000
    assert s.credit_given == 12000
    assert s.cash_in == 24000 and s.cash_out == 52000
    assert s.receivables == 12000


def test_invoice_status_follows_payments(db, biz, settings):
    with db.tx() as conn:
        inv = invoices.create(conn, biz, "Juma", [SaleLineInput("bread", 2, unit_price=6000)],
                              data_dir=settings.data_dir)
        assert inv.status == "unpaid" and inv.file_path and inv.file_path.endswith(".pdf")
        ledger.record_payment(conn, biz, "Juma", 5000)
        assert invoices.get(conn, biz, inv.number).status == "partial"
        ledger.record_payment(conn, biz, "Juma", 7000)
        assert invoices.get(conn, biz, inv.number).status == "paid"


def test_stock_count_records_shrinkage(db, biz):
    with db.tx() as conn:
        ledger.record_purchase(conn, biz, "sugar", 20, unit_cost=10000)
        item = inventory.set_count(conn, biz, inventory.find(conn, biz, "sugar"), 17)
        assert item.quantity == 17
