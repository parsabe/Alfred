from __future__ import annotations

import logging

import pytest

from app.config import Settings
from app.security.redaction import MASK, RedactingFilter, Redactor


def make(**kw: object) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg, arg-type]


def test_csv_lists_and_defaults() -> None:
    s = make(cors_origins="http://a, http://b", auto_approve="tasks,notes")
    assert s.cors_origins == ["http://a", "http://b"]
    assert s.auto_approve == ["tasks", "notes"]
    assert s.owner_timezone == "Europe/Berlin"


def test_bad_timezone_rejected() -> None:
    with pytest.raises(ValueError):
        make(owner_timezone="Mars/Olympus")


def test_startup_requires_long_token() -> None:
    assert make(alfred_api_token="short").validate_for_startup()
    assert not make(alfred_api_token="y" * 40).validate_for_startup()


def test_integrations_are_booleans_only() -> None:
    s = make(home_assistant_url="http://ha.local", home_assistant_token="ha-abcdefabcdefabcdef")
    flags = s.integrations()
    assert flags["home_assistant"] is True
    assert flags["llm.local"] is True  # Gemma via Ollama is the default
    assert flags["llm.anthropic"] is False
    assert all(isinstance(v, bool) for v in flags.values())
    assert "abcdefabcdef" not in repr(s)


def test_defaults_are_fully_local() -> None:
    s = make()
    assert s.llm_provider == "local" and s.local_llm_base_url == "http://localhost:11434/v1"
    assert s.stt_provider == "whisper" and s.tts_provider == "piper"
    assert s.secret_values() == []  # nothing secret is needed except the access token


def test_provider_configured() -> None:
    assert not make().provider_configured("anthropic")
    assert make(anthropic_api_key="sk-ant-xxxxxxxx").provider_configured("anthropic")
    assert make(local_llm_base_url="http://localhost:11434/v1").provider_configured("local")


def test_redactor_masks_configured_secrets_and_patterns() -> None:
    r = Redactor(["my-very-secret-value"])
    text = (
        "key=my-very-secret-value auth: Bearer abc.def.ghijklmnop "
        "sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAA sk-fish-EXAMPLEexampleEXAMPLE0000"
    )
    out = r.redact(text)
    assert "my-very-secret-value" not in out
    assert "abc.def.ghijklmnop" not in out
    assert "sk-ant-api03" not in out
    assert "sk-fish-EXAMPLE" not in out
    assert out.count(MASK) >= 4


def test_redact_obj_masks_sensitive_keys() -> None:
    r = Redactor()
    out = r.redact_obj({"api_key": "plain", "nested": [{"password": "p"}], "city": "Berlin"})
    assert out == {"api_key": MASK, "nested": [{"password": MASK}], "city": "Berlin"}


def test_logging_filter_redacts_args_and_exceptions(caplog: pytest.LogCaptureFixture) -> None:
    logger = logging.getLogger("redaction-test")
    flt = RedactingFilter(Redactor(["supersecret123"]))
    logger.addFilter(flt)
    try:
        with caplog.at_level(logging.INFO, logger="redaction-test"):
            logger.info("token is %s", "supersecret123")
            try:
                raise ValueError("boom supersecret123")
            except ValueError:
                logger.exception("failed")
    finally:
        logger.removeFilter(flt)
    joined = "\n".join(r.getMessage() + (r.exc_text or "") for r in caplog.records)
    assert "supersecret123" not in joined
    assert MASK in joined


def test_filter_keeps_structured_args_for_formatters() -> None:
    """uvicorn's access formatter indexes record.args; redaction must keep the tuple."""
    flt = RedactingFilter(Redactor(["supersecret123"]))
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s" %d',
        ("127.0.0.1", "GET", "/v1/x?t=supersecret123", 200),
        None,
    )
    flt.filter(record)
    assert isinstance(record.args, tuple) and record.args[3] == 200
    assert "supersecret123" not in record.getMessage()
