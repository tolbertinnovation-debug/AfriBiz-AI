import pytest

from afribiz.mobilemoney.parsers import parse_sms, split_messages

CASES = [
    ("QK12AB34CD Confirmed. You have received Ksh500.00 from JOHN DOE 0712345678 on 12/3/24 at 10:15 AM New M-PESA "
     "balance is Ksh1,234.00. Separate personal and business funds through Pochi la Biashara on *334#.",
     dict(provider="mpesa", ref="QK12AB34CD", direction="in", category="received", amount=50000, balance=123400,
          counterparty="JOHN DOE", counterparty_phone="0712345678", occurred_at="2024-03-12T10:15:00Z")),
    ("QK12AB34CE Confirmed. Ksh150.00 sent to JANE DOE 0722000000 on 23/6/23 at 3:41 PM. New M-PESA balance is "
     "Ksh1,205.10. Transaction cost, Ksh6.00.",
     dict(direction="out", category="sent", amount=15000, fee=600, balance=120510, counterparty="JANE DOE",
          occurred_at="2023-06-23T15:41:00Z")),
    ("QK12AB34CF Confirmed. Ksh1,000.00 sent to KPLC PREPAID for account 12345678 on 1/2/24 at 9:00 AM New M-PESA "
     "balance is Ksh205.10. Transaction cost, Ksh0.00.",
     dict(category="paybill", amount=100000, counterparty="KPLC PREPAID")),
    ("QK12AB34CG Confirmed. Ksh250.00 paid to NAIVAS SUPERMARKET. on 1/2/24 at 9:05 AM.New M-PESA balance is "
     "Ksh2,000.00. Transaction cost, Ksh0.00.",
     dict(category="buygoods", amount=25000, counterparty="NAIVAS SUPERMARKET")),
    ("QK12AB34CH Confirmed.on 1/2/24 at 9:00 AMWithdraw Ksh2,000.00 from 123456 - JOSEPH AGENT SHOP New M-PESA "
     "balance is Ksh500.00. Transaction cost, Ksh29.00.",
     dict(direction="out", category="withdraw", amount=200000, fee=2900)),
    ("QK12AB34CI Confirmed. On 1/2/24 at 9:00 AM Give Ksh1,000.00 cash to MAMA AGENT New M-PESA balance is Ksh1,500.00.",
     dict(direction="in", category="deposit", amount=100000)),
    ("You have received UGX 50,000 from JOHN OKELLO (256772123456) on 2024-03-12 10:15:00. Reason: rice. "
     "Your new balance: UGX 120,500. Transaction ID: 12345678901.",
     dict(ref="12345678901", direction="in", amount=5000000, balance=12050000, counterparty="JOHN OKELLO",
          counterparty_phone="256772123456")),
    ("Payment received for GHS 50.00 from JOHN MENSAH Current Balance: GHS 120.50 . Available Balance: GHS 120.50. "
     "Reference: rice. Transaction ID: 41234567890. Fee charged: GHS0.00",
     dict(provider="mtn_momo", amount=5000, balance=12050, counterparty="JOHN MENSAH")),
    ("Acct:XXX123 Amt:NGN5,000.00 CR Desc:TRF FROM ADA OBI/OPAY Avail Bal:NGN20,500.00 Date:12-03-2024 14:20",
     dict(direction="in", category="received", amount=500000, balance=2050000, counterparty="ADA OBI")),
    ("TID: MP240312.1015.A12345. You have received UGX 20,000 from 256752123456, JANE NAMULI. Bal UGX 50,000. Airtel Money",
     dict(provider="airtel_money", ref="MP240312.1015.A12345", amount=2000000, counterparty="JANE NAMULI")),
]


@pytest.mark.parametrize("sms,expected", CASES)
def test_parse_sms(sms, expected):
    txn = parse_sms(sms)
    assert txn is not None
    for key, value in expected.items():
        assert getattr(txn, key) == value, key


@pytest.mark.parametrize("text", [
    "Hello how are you",
    "Failed. Insufficient funds in your M-PESA account to send Ksh500.00.",
    "sold 3 bread @ 60",
])
def test_not_money_messages(text):
    assert parse_sms(text) is None


def test_split_pasted_batch():
    blob = CASES[0][0] + " " + CASES[1][0] + "\n\n" + CASES[6][0]
    assert len(split_messages(blob)) == 3


def test_reference_without_id_is_stable():
    sms = "You have received UGX 5,000 from PAUL. Your new balance: UGX 9,000."
    assert parse_sms(sms).ref == parse_sms(sms + "  ").ref
