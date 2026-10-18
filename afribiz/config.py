"""Runtime settings, read from environment variables (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_bool(name: str, default: bool = False) -> bool:
    value = _env(name)
    if not value:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    database_path: str = field(default_factory=lambda: _env("AFRIBIZ_DB", "afribiz.db"))
    public_base_url: str = field(default_factory=lambda: _env("AFRIBIZ_PUBLIC_URL", "http://localhost:8000"))
    data_dir: str = field(default_factory=lambda: _env("AFRIBIZ_DATA_DIR", "data"))
    content_dir: str = field(
        default_factory=lambda: _env("AFRIBIZ_CONTENT_DIR", str(Path(__file__).resolve().parent.parent / "content"))
    )
    # Shared secret that protects admin/dashboard endpoints and signs dashboard links.
    secret_key: str = field(default_factory=lambda: _env("AFRIBIZ_SECRET", "change-me"))
    enable_simulator: bool = field(default_factory=lambda: _env_bool("AFRIBIZ_ENABLE_SIMULATOR", True))

    # Claude (optional - the rule engine works without it)
    llm_enabled: bool = field(default_factory=lambda: _env_bool("AFRIBIZ_LLM_ENABLED", True))
    llm_model: str = field(default_factory=lambda: _env("AFRIBIZ_LLM_MODEL", "claude-opus-5-5"))
    llm_effort: str = field(default_factory=lambda: _env("AFRIBIZ_LLM_EFFORT", "low"))
    llm_history_turns: int = field(default_factory=lambda: int(_env("AFRIBIZ_LLM_HISTORY_TURNS", "6")))

    # WhatsApp Cloud API (Meta)
    wa_token: str = field(default_factory=lambda: _env("WHATSAPP_TOKEN"))
    wa_phone_number_id: str = field(default_factory=lambda: _env("WHATSAPP_PHONE_NUMBER_ID"))
    wa_verify_token: str = field(default_factory=lambda: _env("WHATSAPP_VERIFY_TOKEN"))
    wa_app_secret: str = field(default_factory=lambda: _env("WHATSAPP_APP_SECRET"))
    wa_graph_version: str = field(default_factory=lambda: _env("WHATSAPP_GRAPH_VERSION", "v21.0"))
    wa_reminder_template: str = field(default_factory=lambda: _env("WHATSAPP_REMINDER_TEMPLATE"))

    # Twilio (WhatsApp sandbox / SMS)
    twilio_account_sid: str = field(default_factory=lambda: _env("TWILIO_ACCOUNT_SID"))
    twilio_auth_token: str = field(default_factory=lambda: _env("TWILIO_AUTH_TOKEN"))
    twilio_from: str = field(default_factory=lambda: _env("TWILIO_FROM"))

    # Africa's Talking (USSD + SMS for feature phones)
    at_username: str = field(default_factory=lambda: _env("AT_USERNAME"))
    at_api_key: str = field(default_factory=lambda: _env("AT_API_KEY"))
    at_sender_id: str = field(default_factory=lambda: _env("AT_SENDER_ID"))

    # Speech-to-text for voice notes: any OpenAI-compatible /audio/transcriptions endpoint
    # (self-hosted whisper.cpp / faster-whisper servers expose this shape).
    stt_url: str = field(default_factory=lambda: _env("AFRIBIZ_STT_URL"))
    stt_api_key: str = field(default_factory=lambda: _env("AFRIBIZ_STT_API_KEY"))
    stt_model: str = field(default_factory=lambda: _env("AFRIBIZ_STT_MODEL", "whisper-1"))

    @property
    def whatsapp_configured(self) -> bool:
        return bool(self.wa_token and self.wa_phone_number_id)

    @property
    def africastalking_configured(self) -> bool:
        return bool(self.at_username and self.at_api_key)

    @property
    def twilio_configured(self) -> bool:
        return bool(self.twilio_account_sid and self.twilio_auth_token)


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings(settings: Settings | None = None) -> None:
    """Replace the cached settings (used by tests and the CLI)."""
    global _settings
    _settings = settings
