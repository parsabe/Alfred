from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from alfred_desktop import cli, window
from alfred_desktop.chat import describe
from alfred_desktop.config import Config, normalize_server, ws_url


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("ALFRED_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.delenv("ALFRED_SERVER", raising=False)
    monkeypatch.delenv("ALFRED_TOKEN", raising=False)
    return tmp_path


@pytest.mark.parametrize(
    ("typed", "clean"),
    [
        ("alfred.tail1234.ts.net", "https://alfred.tail1234.ts.net"),
        ("  https://Alfred.Example.com/ ", "https://alfred.example.com"),
        ("http://192.168.1.5:8000", "http://192.168.1.5:8000"),
        ("https://example.com:443", "https://example.com"),
        ("https://example.com/alfred/", "https://example.com/alfred"),
        ("http://localhost:8000", "http://localhost:8000"),
    ],
)
def test_normalize_server(typed: str, clean: str) -> None:
    assert normalize_server(typed) == clean


@pytest.mark.parametrize("bad", ["", "not an address", "ftp://example.com", "https://", "alfred"])
def test_normalize_rejects(bad: str) -> None:
    assert normalize_server(bad) is None


def test_ws_url() -> None:
    assert ws_url("https://a.b") == "wss://a.b/v1/voice"
    assert ws_url("http://a.b:8000") == "ws://a.b:8000/v1/voice"


def test_config_round_trip_is_private(isolated: Path) -> None:
    cfg = Config(server="https://a.b", token="secret-token", barge_in=True)
    path = cfg.save()
    loaded = Config.load()
    assert loaded.server == "https://a.b" and loaded.token == "secret-token" and loaded.barge_in
    assert json.loads(path.read_text())["token"] == "secret-token"
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_environment_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    Config(server="https://a.b", token="file").save()
    monkeypatch.setenv("ALFRED_SERVER", "c.d")
    monkeypatch.setenv("ALFRED_TOKEN", "env")
    cfg = Config.load()
    assert cfg.server == "https://c.d" and cfg.token == "env"


def test_unconfigured_non_interactive_explains(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    assert cli.main(["status"]) == 2
    assert "alfred setup" in capsys.readouterr().err


def test_status_reports_unreachable_server(capsys: pytest.CaptureFixture[str]) -> None:
    Config(server="http://127.0.0.1:9", token="t").save()
    assert cli.main(["status"]) == 1
    assert "can't reach" in capsys.readouterr().err


def test_describe_health() -> None:
    body = {
        "llm": {"configured": True, "model": "gemma3:12b"},
        "voice": {"stt": "whisper", "stt_status": "ready", "tts": None},
    }
    assert describe(body) == "model: gemma3:12b · hearing: whisper (ready) · voice: off"


def test_app_browser_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    if sys.platform.startswith("linux"):
        monkeypatch.setattr(
            window.shutil, "which", lambda name: "/usr/bin/chromium" if name == "chromium" else None
        )
        assert window.find_app_browser() == "/usr/bin/chromium"
        monkeypatch.setattr(window.shutil, "which", lambda name: None)
        assert window.find_app_browser() is None
    elif sys.platform == "win32":
        assert window._windows_candidates()


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    assert "alfred 0.1.0" in capsys.readouterr().out
