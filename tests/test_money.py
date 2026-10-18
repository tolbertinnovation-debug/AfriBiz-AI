from decimal import Decimal

import pytest

from afribiz.money import find_amounts, format_money, guess_country, parse_amount, to_minor


@pytest.mark.parametrize(
    "text,expected",
    [
        ("1,500", Decimal("1500")),
        ("1.5k", Decimal("1500.0")),
        ("2k", Decimal("2000")),
        ("KSh 300", Decimal("300")),
        ("Ksh1,234.50", Decimal("1234.50")),
        ("₦5,000", Decimal("5000")),
        ("500/=", Decimal("500")),
        ("3 elfu", Decimal("3000")),
        ("spent 200 on transport", Decimal("200")),
        ("no money here", None),
    ],
)
def test_parse_amount(text, expected):
    assert parse_amount(text) == expected


def test_kg_is_not_thousands():
    assert find_amounts("500 kg") == [Decimal("500")]


def test_space_is_not_a_thousands_separator():
    # "sold 5 200" is five items at 200, never 5,200
    assert find_amounts("5 200") == [Decimal("5"), Decimal("200")]


def test_minor_units_round_trip():
    assert to_minor("150.50") == 15050
    assert to_minor(Decimal("0.005")) == 1


def test_format_money():
    assert format_money(150000, "KES") == "KSh 1,500"
    assert format_money(12050, "GHS") == "GH₵ 120.50"
    assert format_money(-5000, "NGN") == "-₦ 50"


@pytest.mark.parametrize(
    "phone,currency",
    [("254712345678", "KES"), ("+234 803 000 0000", "NGN"), ("233241234567", "GHS"), ("27821234567", "ZAR"),
     ("221771234567", "XOF"), ("15551234567", "USD")],
)
def test_guess_country(phone, currency):
    assert guess_country(phone)[1] == currency
