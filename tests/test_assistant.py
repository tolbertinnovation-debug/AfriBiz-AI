from afribiz.services import businesses, customers

from .conftest import PHONE, say


def test_onboarding_flow(assistant, db):
    assert "name of your business" in say(assistant, "hello")
    assert "Mama Mboga" in say(assistant, "Mama Mboga")
    done = say(assistant, "groceries")
    assert "KES" in done and "📘" in done  # currency from +254, plus the first lesson
    with db.tx() as conn:
        biz = businesses.get_by_phone(conn, PHONE)
    assert biz.onboarded and biz.business_type == "groceries"


def test_missing_price_is_asked_then_resumed(onboarded, db):
    assert "How much is one" in say(onboarded, "sold 2 sugar to Mary on credit")
    reply = say(onboarded, "120")
    assert "Sale recorded" in reply and "owes" in reply
    with db.tx() as conn:
        biz = businesses.get_by_phone(conn, PHONE)
        assert customers.debtors(conn, biz)[0].balance == 24000


def test_bare_number_uses_known_price(onboarded):
    say(onboarded, "price bread 60")
    assert "KSh 180" in say(onboarded, "sold 3 bread 60")  # 60 matches the known unit price
    assert "KSh 150" in say(onboarded, "sold 3 bread 150")  # otherwise a bare number is the line total


def test_momo_sms_can_settle_a_debt(onboarded, db):
    say(onboarded, "sold 2 sugar @ 120 to Mary on credit")
    say(onboarded, "phone mary 0722111222")
    sms = ("QK12AB34CD Confirmed. You have received Ksh500.00 from MARY WANJIKU 0722111222 on 12/3/24 at 10:15 AM "
           "New M-PESA balance is Ksh1,234.00.")
    prompt = say(onboarded, sms)
    assert "Mary" in prompt and "Debt payment" in prompt
    reply = say(onboarded, "2")
    assert "fully paid" in reply and "new sale" in reply  # 240 owed, 260 extra booked as a sale
    with db.tx() as conn:
        biz = businesses.get_by_phone(conn, PHONE)
        assert customers.debtors(conn, biz) == []


def test_balance_mismatch_warning(onboarded):
    first = ("AB12CD34EF Confirmed. You have received Ksh500.00 from JOHN 0711000000 on 1/10/26 at 10:00 AM "
             "New M-PESA balance is Ksh1,000.00.")
    fake = ("AB12CD34EG Confirmed. You have received Ksh500.00 from PETER 0711000001 on 1/10/26 at 11:00 AM "
            "New M-PESA balance is Ksh9,000.00.")
    say(onboarded, first)
    say(onboarded, "3")
    assert "Check this payment" in say(onboarded, fake)


def test_quiz_answer(onboarded):
    say(onboarded, "lesson 1")  # money-01, correct answer B
    assert "Correct" in say(onboarded, "b")


def test_delete_requires_confirmation(onboarded, db):
    assert "YES DELETE" in say(onboarded, "delete my data")
    assert "Nothing was deleted" in say(onboarded, "no")
    say(onboarded, "delete my data")
    assert "deleted" in say(onboarded, "yes delete")
    with db.tx() as conn:
        assert businesses.get_by_phone(conn, PHONE) is None
        assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0


def test_duplicate_webhook_delivery_is_ignored(onboarded):
    first = onboarded.handle(PHONE, "spent 100 transport", message_id="wamid.1")
    again = onboarded.handle(PHONE, "spent 100 transport", message_id="wamid.1")
    assert first and again == []


def test_unknown_message_without_ai(onboarded):
    assert "didn't get that" in say(onboarded, "what should I do about my business")


def test_language_switch(onboarded):
    assert "Kiswahili" in say(onboarded, "language sw")
    assert "Samahani" in say(onboarded, "xyz abc")


def test_voice_note_without_stt(onboarded):
    replies = onboarded.handle(PHONE, None, audio=b"OggS...", audio_mime="audio/ogg")
    assert "voice" in replies[0].text.lower()


def test_voice_note_with_transcriber(db, settings):
    from afribiz.assistant import Assistant

    class FakeSTT:
        def transcribe(self, audio, mime_type, language=None):
            return "spent 300 transport"

    a = Assistant(db=db, settings=settings, transcriber=FakeSTT())
    for msg in ("hi", "Shop", "food"):
        a.handle(PHONE, msg)
    texts = [r.text for r in a.handle(PHONE, None, audio=b"OggS")]
    assert "spent 300 transport" in texts[0] and "Expense recorded" in texts[1]
