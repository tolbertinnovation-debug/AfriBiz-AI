"""The Claude tool loop, exercised with a scripted fake client (no network)."""

from types import SimpleNamespace

from afribiz.assistant import Assistant
from afribiz.nlu.llm import TOOLS, ClaudeAgent, tool_to_intent
from afribiz.services import businesses, reports

from .conftest import PHONE


def block(**kw):
    return SimpleNamespace(**kw)


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def test_tool_schemas_are_strict_and_closed():
    for tool in TOOLS:
        assert tool["strict"] is True
        assert tool["input_schema"]["additionalProperties"] is False
        assert set(tool["input_schema"]["required"]) <= set(tool["input_schema"]["properties"])


def test_every_tool_maps_to_an_action():
    from afribiz.actions import HANDLERS

    samples = {"record_sale": {"lines": [{"item": "x", "quantity": 1, "unit_price": 1}]},
               "create_invoice": {"customer": "A", "lines": [{"item": "x"}]},
               "record_stock_purchase": {"item": "x", "quantity": 1}}
    for tool in TOOLS:
        intent = tool_to_intent(tool["name"], samples.get(tool["name"], {}))
        assert intent.name in HANDLERS


def test_agent_records_mixed_language_message(db, settings):
    client = FakeClient([
        block(stop_reason="tool_use", content=[
            block(type="thinking", thinking="", signature="sig"),
            block(type="tool_use", id="t1", name="record_sale",
                  input={"lines": [{"item": "chapati", "quantity": 10, "unit_price": 20}], "payment_method": "cash"}),
            block(type="tool_use", id="t2", name="record_expense",
                  input={"amount": 150, "description": "gas refill", "category": "utilities"}),
        ]),
        block(stop_reason="end_turn", content=[block(type="text", text="Biashara nzuri leo! 👏")]),
    ])
    agent = ClaudeAgent("claude-opus-5-5", client=client)
    a = Assistant(db=db, settings=settings, agent=agent)
    for msg in ("hi", "Chapati Corner", "food"):
        a.handle(PHONE, msg)
    replies = a.handle(PHONE, "leo nimeuza chapati kumi, kila moja ishirini, na gas ilikuwa 150")
    texts = [r.text for r in replies]
    assert any("Sale recorded" in t for t in texts) and any("Expense recorded" in t for t in texts)
    assert texts[-1] == "Biashara nzuri leo! 👏"

    # second request carries the assistant turn (with its thinking block) unchanged, then the tool results
    second = client.calls[1]["messages"]
    assert second[-2]["role"] == "assistant" and second[-2]["content"][0].type == "thinking"
    assert [r["tool_use_id"] for r in second[-1]["content"]] == ["t1", "t2"]
    first = client.calls[0]
    assert first["model"] == "claude-opus-5-5" and first["thinking"] == {"type": "adaptive"}
    assert first["fallbacks"] == "default"

    with db.tx() as conn:
        biz = businesses.get_by_phone(conn, PHONE)
        s = reports.summary(conn, biz, "today")
    assert s.sales == 20000 and s.expenses == 15000


def test_agent_ok_reply_is_suppressed(db, settings):
    client = FakeClient([
        block(stop_reason="tool_use", content=[block(type="tool_use", id="t1", name="get_debtors", input={})]),
        block(stop_reason="end_turn", content=[block(type="text", text="OK")]),
    ])
    a = Assistant(db=db, settings=settings, agent=ClaudeAgent("claude-opus-5-5", client=client))
    for msg in ("hi", "Shop", "food"):
        a.handle(PHONE, msg)
    texts = [r.text for r in a.handle(PHONE, "nani ananidai pesa?")]
    assert texts == ["🎉 Nobody owes you money right now."]


def test_failing_tool_is_rolled_back_and_reported(db, settings):
    client = FakeClient([
        block(stop_reason="tool_use", content=[block(type="tool_use", id="t1", name="record_customer_payment",
                                                     input={"customer": "Ghost", "amount": 100})]),
        block(stop_reason="end_turn", content=[block(type="text", text="Who is Ghost?")]),
    ])
    a = Assistant(db=db, settings=settings, agent=ClaudeAgent("claude-opus-5-5", client=client))
    for msg in ("hi", "Shop", "food"):
        a.handle(PHONE, msg)
    texts = [r.text for r in a.handle(PHONE, "the man from yesterday gave me 100")]
    assert texts[-1] == "Who is Ghost?"


def test_agent_errors_fall_back_gracefully(db, settings):
    class Broken:
        def run(self, *a, **k):
            raise RuntimeError("network down")

    a = Assistant(db=db, settings=settings, agent=Broken())
    for msg in ("hi", "Shop", "food"):
        a.handle(PHONE, msg)
    assert "didn't get that" in a.handle(PHONE, "tell me something")[0].text
