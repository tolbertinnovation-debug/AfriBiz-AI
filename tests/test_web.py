import hashlib
import hmac
import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from afribiz.assistant import Assistant
from afribiz.channels import twilio as twilio_channel
from afribiz.channels.whatsapp_cloud import parse_webhook
from afribiz.replies import Button, Reply
from afribiz.security import sign
from afribiz.web.app import create_app

from .conftest import PHONE


@pytest.fixture
def client(db, settings):
    return TestClient(create_app(db=db, settings=settings, assistant=Assistant(db=db, settings=settings)))


def simulate(client, text, phone=PHONE):
    r = client.post("/api/simulate", json={"phone": phone, "text": text})
    assert r.status_code == 200
    return r.json()["replies"]


def test_health_and_pages(client):
    assert client.get("/health").json()["ok"]
    assert "WhatsApp" in client.get("/").text
    assert client.get("/simulator").status_code == 200


def test_simulator_conversation(client):
    simulate(client, "hi")
    simulate(client, "Duka")
    simulate(client, "shop")
    replies = simulate(client, "invoice Juma 2 rice @ 150")
    doc = next(r["document"] for r in replies if r.get("document"))
    assert doc["filename"].endswith(".pdf")
    path = doc["url"].replace("https://afribiz.test", "")
    pdf = client.get(path)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert client.get(path.replace("t=", "t=x")).status_code == 404


def test_dashboard_requires_valid_token(client, settings):
    simulate(client, "hi")
    simulate(client, "Duka")
    simulate(client, "shop")
    simulate(client, "sold 3 bread @ 60")
    token = sign(settings.secret_key, "dashboard", 1)
    page = client.get(f"/d/1/{token}")
    assert page.status_code == 200 and "Duka" in page.text and "KSh 180" in page.text
    assert client.get("/d/1/not-the-token").status_code == 404
    assert client.get(f"/d/2/{token}").status_code == 404


def test_dashboard_statement_upload(client, settings):
    simulate(client, "hi")
    token = sign(settings.secret_key, "dashboard", 1)
    csv = ("Receipt No.,Completion Time,Details,Transaction Status,Paid In,Withdrawn,Balance\n"
           "SK1,2026-09-01 10:00:00,Funds received from - 0712345678 MARY W,Completed,500.00,,1500.00\n")
    r = client.post(f"/d/1/{token}/import", files={"file": ("s.csv", csv, "text/csv")}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("imported=1")


def test_lender_view_needs_consent(client, settings):
    simulate(client, "hi")
    simulate(client, "Duka")
    simulate(client, "shop")
    token = sign(settings.secret_key, "lender", 1)
    assert client.get(f"/lender/1/{token}").status_code == 403
    share = simulate(client, "share score")[0]["text"]
    assert f"/lender/1/{token}" in share
    page = client.get(f"/lender/1/{token}")
    assert page.status_code == 200 and "consent" in page.text
    simulate(client, "stop sharing")
    assert client.get(f"/lender/1/{token}").status_code == 403


def test_ussd_menu_and_sale(client):
    menu = client.post("/webhooks/ussd", data={"phoneNumber": "+254700111222", "text": "", "sessionId": "s"}).text
    assert menu.startswith("CON") and "business name" in menu
    assert client.post("/webhooks/ussd", data={"phoneNumber": "+254700111222", "text": "Kibanda"}).text.startswith("END")
    assert "1. Today's summary" in client.post("/webhooks/ussd", data={"phoneNumber": "+254700111222", "text": ""}).text
    assert "CON Enter amount" in client.post("/webhooks/ussd", data={"phoneNumber": "+254700111222", "text": "2"}).text
    done = client.post("/webhooks/ussd", data={"phoneNumber": "+254700111222", "text": "2*500*2 bread"}).text
    assert done == "END Sale of KSh 500 recorded. Thank you!"
    assert "KSh 500" in client.post("/webhooks/ussd", data={"phoneNumber": "+254700111222", "text": "1"}).text


def test_whatsapp_verification(client):
    ok = client.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "verify-me",
                                                  "hub.challenge": "42"})
    assert ok.text == "42"
    assert client.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "nope",
                                                    "hub.challenge": "42"}).status_code == 403


WA_PAYLOAD = {
    "object": "whatsapp_business_account",
    "entry": [{"id": "1", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp",
        "contacts": [{"wa_id": PHONE, "profile": {"name": "Wanjiru"}}],
        "messages": [
            {"from": PHONE, "id": "wamid.A", "type": "text", "text": {"body": "sold 2 bread @ 50"}},
            {"from": PHONE, "id": "wamid.B", "type": "interactive",
             "interactive": {"type": "button_reply", "button_reply": {"id": "report", "title": "Report"}}},
            {"from": PHONE, "id": "wamid.C", "type": "audio", "audio": {"id": "media1", "mime_type": "audio/ogg; codecs=opus"}},
        ],
    }}]}],
}


def test_whatsapp_payload_parsing():
    parsed = parse_webhook(WA_PAYLOAD)
    assert [p.inbound.text for p in parsed[:2]] == ["sold 2 bread @ 50", "report"]
    assert parsed[0].inbound.profile_name == "Wanjiru"
    assert parsed[2].media_kind == "audio" and parsed[2].media_id == "media1"


def test_whatsapp_signature_enforced(db, settings):
    secured = replace(settings, wa_app_secret="app-secret")
    app = create_app(db=db, settings=secured, assistant=Assistant(db=db, settings=secured))
    c = TestClient(app)
    body = json.dumps({"entry": []}).encode()
    assert c.post("/webhooks/whatsapp", content=body, headers={"Content-Type": "application/json"}).status_code == 401
    sig = "sha256=" + hmac.new(b"app-secret", body, hashlib.sha256).hexdigest()
    ok = c.post("/webhooks/whatsapp", content=body,
                headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig})
    assert ok.status_code == 200


def test_twilio_twiml(client):
    r = client.post("/webhooks/twilio", data={"From": "whatsapp:+254799000111", "Body": "hi", "MessageSid": "SM1"})
    assert r.status_code == 200 and r.text.startswith("<?xml") and "Welcome" in r.text


def test_twiml_escapes_and_lists_buttons():
    xml = twilio_channel.twiml([Reply("a < b & c", [Button("remind", "Remind")])])
    assert "a &lt; b &amp; c" in xml and "*remind*" in xml
