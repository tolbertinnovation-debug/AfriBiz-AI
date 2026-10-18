from __future__ import annotations

from pathlib import Path

import pytest

from afribiz.assistant import Assistant
from afribiz.config import Settings
from afribiz.db import Database
from afribiz.services import businesses

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        database_path=str(tmp_path / "test.db"),
        data_dir=str(tmp_path / "data"),
        content_dir=str(ROOT / "content"),
        secret_key="test-secret",
        public_base_url="https://afribiz.test",
        llm_enabled=False,
        wa_token="", wa_phone_number_id="", wa_verify_token="verify-me", wa_app_secret="",
        twilio_account_sid="", twilio_auth_token="", stt_url="",
    )


@pytest.fixture
def db(settings) -> Database:
    database = Database(settings.database_path)
    database.init()
    return database


@pytest.fixture
def assistant(db, settings) -> Assistant:
    return Assistant(db=db, settings=settings)


PHONE = "254712345678"


@pytest.fixture
def onboarded(assistant) -> Assistant:
    """An assistant whose test business has finished onboarding."""
    assistant.handle(PHONE, "hi")
    assistant.handle(PHONE, "Mama Mboga")
    assistant.handle(PHONE, "groceries")
    return assistant


@pytest.fixture
def biz(db, onboarded):
    with db.tx() as conn:
        return businesses.get_by_phone(conn, PHONE)


def say(assistant: Assistant, text: str) -> str:
    return "\n".join(r.text for r in assistant.handle(PHONE, text))
