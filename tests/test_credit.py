from afribiz.demo import seed
from afribiz.mobilemoney.parsers import MomoTxn
from afribiz.services import businesses, credit, momo


def test_demo_business_gets_a_full_profile(db):
    business_id = seed(db)
    with db.tx() as conn:
        biz = businesses.get(conn, business_id)
        p = credit.build_profile(conn, biz)
    assert p.sufficient_data
    assert 40 <= p.score <= 100
    assert p.grade in {"A", "B", "C"}
    assert p.suggested_loan > 0
    assert {f.key for f in p.factors} == {"history", "activity", "stability", "surplus", "diversity", "buffer",
                                          "loans", "records"}
    assert sum(f.max_points for f in p.factors) == 100


def test_thin_file_is_capped_and_graded_e(db, biz):
    with db.tx() as conn:
        momo.save(conn, biz, MomoTxn("mpesa", "R1", "in", "received", 100000, 0, 100000, "A", None,
                                     "2026-10-10T10:00:00Z", ""))
        p = credit.build_profile(conn, biz)
    assert not p.sufficient_data and p.grade == "E" and p.score <= 40 and p.suggested_loan == 0


def test_consent_lifecycle(db, biz):
    with db.tx() as conn:
        assert not credit.has_consent(conn, biz)
        credit.grant_consent(conn, biz)
        assert credit.has_consent(conn, biz)
        credit.revoke_consent(conn, biz)
        assert not credit.has_consent(conn, biz)


def test_duplicate_sms_is_ignored(db, biz):
    sms = ("QK12AB34CD Confirmed. You have received Ksh500.00 from JOHN DOE 0712345678 on 12/3/24 at 10:15 AM "
           "New M-PESA balance is Ksh1,234.00.")
    with db.tx() as conn:
        assert len(momo.import_sms_blob(conn, biz, sms).added) == 1
        again = momo.import_sms_blob(conn, biz, sms)
    assert not again.added and again.duplicates == 1


def test_csv_statement_import(db, biz):
    data = (
        "Receipt No.,Completion Time,Details,Transaction Status,Paid In,Withdrawn,Balance\n"
        "SK1,2026-09-01 10:00:00,Funds received from - 0712345678 MARY W,Completed,500.00,,1500.00\n"
        "SK2,2026-09-01 12:00:00,Customer Withdrawal At Agent Till 1234,Completed,,-1000.00,500.00\n"
        "SK3,2026-09-02 09:00:00,Pay Bill to 888880 - KPLC,Failed,,-200.00,500.00\n"
    )
    with db.tx() as conn:
        result = momo.import_csv(conn, biz, data, provider="mpesa")
    assert len(result.added) == 2
    received, withdraw = result.added
    assert received.category == "received" and received.amount == 50000 and received.counterparty == "MARY W"
    assert withdraw.category == "withdraw" and withdraw.direction == "out" and withdraw.amount == 100000
