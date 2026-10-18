from decimal import Decimal

import pytest

from afribiz.nlu.rules import parse


def test_sale_with_unit_price():
    i = parse("sold 3 bread @ 60")
    assert i.name == "record_sale"
    line = i.args["lines"][0]
    assert (line.name, line.quantity, line.price, line.price_kind) == ("bread", 3, Decimal("60"), "unit")


def test_multi_item_credit_sale_with_part_payment():
    i = parse("sold 3 bags rice @ 1500, 2 soap at 50 to Mama Ngozi paid 2000")
    assert [(ln.name, ln.quantity, ln.unit) for ln in i.args["lines"]] == [("rice", 3, "bags"), ("soap", 2, None)]
    assert i.args["customer"] == "Mama Ngozi"
    assert i.args["amount_paid"] == Decimal("2000")


@pytest.mark.parametrize(
    "text,customer,credit",
    [
        ("sold 2 sugar to Mary on credit", "Mary", True),
        ("I don sell 4 bread for 400 to Emeka for credit", "Emeka", True),
        ("sold soap 50 to Juma on credit", "Juma", True),
    ],
)
def test_credit_sales(text, customer, credit):
    i = parse(text)
    assert i.name == "record_sale"
    assert i.args["customer"] == customer
    assert i.args["on_credit"] is credit


def test_swahili_number_after_noun():
    i = parse("nimeuza mkate 3 @ 60")
    line = i.args["lines"][0]
    assert (line.name, line.quantity, line.price) == ("mkate", 3, Decimal("60"))


def test_french_sale():
    line = parse("vendu 3 pains à 60").args["lines"][0]
    assert (line.name, line.quantity, line.price_kind) == ("pains", 3, "unit")


def test_total_price_and_method():
    i = parse("sold 5 tomatoes for 1.5k by mpesa")
    line = i.args["lines"][0]
    assert line.price == Decimal("1500.0") and line.price_kind == "total"
    assert i.args["method"] == "mobile_money"


@pytest.mark.parametrize(
    "text,amount,desc",
    [("spent 200 transport", "200", "transport"), ("nimetumia 200 nauli", "200", "nauli"),
     ("rent 5000", "5000", "rent"), ("paid rent 3000", "3000", "rent")],
)
def test_expenses(text, amount, desc):
    i = parse(text)
    assert i.name == "record_expense"
    assert i.args["amount"] == Decimal(amount)
    assert i.args["description"] == desc


def test_owner_draw_is_personal():
    i = parse("took 500 for home")
    assert i.name == "record_expense" and i.args["personal"] is True


def test_purchase_with_selling_price():
    i = parse("bought 10 rice for 1200 sell at 160")
    assert i.name == "record_purchase"
    assert i.args["line"].quantity == 10 and i.args["line"].price_kind == "total"
    assert i.args["sell_price"] == Decimal("160")


@pytest.mark.parametrize(
    "text,name,amount",
    [("Mary paid 300", "Mary", "300"), ("Juma amelipa 200", "Juma", "200"),
     ("Emeka don pay 500 via opay", "Emeka", "500"), ("received 300 from Mary", "Mary", "300")],
)
def test_payments(text, name, amount):
    i = parse(text)
    assert i.name == "record_payment"
    assert i.args["customer"] == name and i.args["amount"] == Decimal(amount)


@pytest.mark.parametrize(
    "text,intent",
    [("report", "report"), ("report week", "report"), ("stock", "stock"), ("debtors", "debtors"),
     ("who owes me", "debtors"), ("remind", "remind"), ("score", "credit_score"), ("lesson 3", "lesson"),
     ("undo", "undo"), ("help", "help"), ("count sugar 12", "stock_count"), ("reorder rice at 5", "reorder_level"),
     ("price rice 160", "set_price"), ("phone Juma 0712345678", "set_customer_phone"), ("b", "quiz_answer")],
)
def test_commands(text, intent):
    assert parse(text).name == intent


def test_invoice():
    i = parse("invoice Juma 2 rice @ 150 due 14 days")
    assert i.name == "invoice" and i.args["customer"] == "Juma" and i.args["due_days"] == 14


@pytest.mark.parametrize("text", ["what should I do about my business", "Blah blah how are you", "the weather is hot"])
def test_unknown_goes_to_ai(text):
    assert parse(text) is None
