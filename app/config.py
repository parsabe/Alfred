"""Settings loaded from the environment (and `.env`).

Feature flags are *derived* from which keys are present: a missing optional key disables that
feature; it never prevents startup. Every secret is a `SecretStr`, so it cannot leak via `repr`.
"""

from __future__ import annotations

from datetime import time
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

ProviderName = Literal["anthropic", "openai", "local", "fake"]

ROOT_DIR = Path(__file__).resolve().parent.parent

MIN_TOKEN_LENGTH = 32


def _split_csv(value: object) -> object:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        validate_assignment=True,
    )

    # --- Core -----------------------------------------------------------------------------
    alfred_api_token: SecretStr = SecretStr("")
    owner_name: str = ""
    owner_address_as: str = ""
    owner_profile: str = ""
    owner_timezone: str = "Europe/Berlin"
    owner_location: str = ""
    database_url: str = "sqlite+aiosqlite:///./data/alfred.db"
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:3000"]
    ip_allowlist: Annotated[list[str], NoDecode] = []
    log_level: str = "INFO"
    log_json: bool = False
    persona_path: Path = ROOT_DIR / "prompts" / "alfred_system.md"

    # --- LLM ------------------------------------------------------------------------------
    llm_provider: ProviderName = "anthropic"
    llm_model: str = ""
    llm_fallback_provider: ProviderName | Literal[""] = ""
    llm_fallback_model: str = ""
    llm_effort: Literal["", "low", "medium", "high", "xhigh", "max"] = ""
    llm_max_output_tokens: int = 16000
    llm_timeout_s: float = 120.0
    llm_utility_model: str = ""
    llm_price_overrides: str = ""
    anthropic_api_key: SecretStr = SecretStr("")
    anthropic_refusal_fallback: bool = True
    openai_api_key: SecretStr = SecretStr("")
    local_llm_base_url: str = ""
    local_llm_api_key: SecretStr = SecretStr("")
    embeddings_provider: str = ""
    embeddings_model: str = ""

    # --- Voice ----------------------------------------------------------------------------
    fish_audio_api_key: SecretStr = SecretStr("")
    fish_audio_voice_id: str = ""
    fish_audio_model: str = "s2.1-pro-free"
    fish_audio_format: Literal["mp3", "wav", "opus"] = "mp3"
    web_ui_enabled: bool = True

    # --- Integrations (all optional) ------------------------------------------------------
    web_search_provider: str = ""
    web_search_api_key: SecretStr = SecretStr("")
    weather_api_key: SecretStr = SecretStr("")
    google_client_id: str = ""
    google_client_secret: SecretStr = SecretStr("")
    home_assistant_url: str = ""
    home_assistant_token: SecretStr = SecretStr("")
    telegram_bot_token: SecretStr = SecretStr("")
    push_webhook_url: str = ""

    # --- Behaviour ------------------------------------------------------------------------
    auto_approve: Annotated[list[str], NoDecode] = []
    max_agent_steps: int = Field(default=8, ge=1, le=50)
    turn_timeout_s: float = 120.0
    context_budget_tokens: int = 60000
    recent_turns: int = 20
    daily_briefing_time: str = "07:30"
    quiet_hours: str = "22:00-07:00"
    files_sandbox_dir: Path = Path("./data/files")
    code_exec_enabled: bool = False

    # --- Validators -----------------------------------------------------------------------
    _csv = field_validator("cors_origins", "ip_allowlist", "auto_approve", mode="before")(
        _split_csv
    )

    @field_validator("owner_timezone")
    @classmethod
    def _valid_tz(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone {value!r}") from exc
        return value

    @field_validator("daily_briefing_time")
    @classmethod
    def _valid_time(cls, value: str) -> str:
        time.fromisoformat(value)
        return value

    @field_validator("quiet_hours")
    @classmethod
    def _valid_quiet(cls, value: str) -> str:
        if value:
            start, _, end = value.partition("-")
            time.fromisoformat(start)
            time.fromisoformat(end)
        return value

    # --- Derived --------------------------------------------------------------------------
    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.owner_timezone)

    def provider_configured(self, provider: str) -> bool:
        match provider:
            case "anthropic":
                return bool(self.anthropic_api_key.get_secret_value())
            case "openai":
                return bool(self.openai_api_key.get_secret_value())
            case "local":
                return bool(self.local_llm_base_url)
            case "fake":
                return True
        return False

    @property
    def voice_enabled(self) -> bool:
        return bool(self.fish_audio_api_key.get_secret_value() and self.fish_audio_voice_id)

    def integrations(self) -> dict[str, bool]:
        """Which integrations are configured. Names and booleans only, never values."""
        return {
            "llm.anthropic": self.provider_configured("anthropic"),
            "llm.openai": self.provider_configured("openai"),
            "llm.local": self.provider_configured("local"),
            "voice.fish_audio": self.voice_enabled,
            "web_search": bool(
                self.web_search_provider and self.web_search_api_key.get_secret_value()
            ),
            "weather": True,  # Open-Meteo needs no key; WEATHER_API_KEY upgrades the provider
            "google": bool(self.google_client_id and self.google_client_secret.get_secret_value()),
            "home_assistant": bool(
                self.home_assistant_url and self.home_assistant_token.get_secret_value()
            ),
            "telegram": bool(self.telegram_bot_token.get_secret_value()),
            "push_webhook": bool(self.push_webhook_url),
            "code_exec": self.code_exec_enabled,
        }

    def secret_values(self) -> list[str]:
        """All configured secret values, for the log-redaction filter."""
        values = []
        for name in type(self).model_fields:
            value = getattr(self, name)
            if isinstance(value, SecretStr) and value.get_secret_value():
                values.append(value.get_secret_value())
        return values

    def validate_for_startup(self) -> list[str]:
        """Hard errors that should stop the service. Returns a list of messages."""
        errors = []
        token = self.alfred_api_token.get_secret_value()
        if len(token) < MIN_TOKEN_LENGTH:
            errors.append(
                f"ALFRED_API_TOKEN must be set and at least {MIN_TOKEN_LENGTH} characters "
                "(generate one with: python -c 'import secrets; print(secrets.token_urlsafe(32))')"
            )
        return errors


@lru_cache
def get_settings() -> Settings:
    return Settings()
