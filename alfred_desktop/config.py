"""Where this computer's Alfred settings live, and how server addresses are cleaned up."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


def config_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "Alfred"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Alfred"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "alfred"


def config_path() -> Path:
    return Path(os.environ.get("ALFRED_CONFIG") or config_dir() / "config.json")


def normalize_server(text: str) -> str | None:
    """'alfred.tail1234.ts.net' → 'https://alfred.tail1234.ts.net'. None if it isn't usable."""
    text = text.strip()
    if not text or any(c.isspace() for c in text):
        return None
    if "://" not in text:
        text = "https://" + text
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if scheme not in ("http", "https") or not host:
        return None
    if "." not in host and host != "localhost" and ":" not in host:
        return None
    default = 443 if scheme == "https" else 80
    netloc = host if port in (None, default) else f"{host}:{port}"
    return f"{scheme}://{netloc}{parts.path.rstrip('/')}"


def ws_url(server: str) -> str:
    if server.startswith("https://"):
        return "wss://" + server[len("https://") :] + "/v1/voice"
    if server.startswith("http://"):
        return "ws://" + server[len("http://") :] + "/v1/voice"
    raise ValueError(f"not an http(s) address: {server}")


@dataclass
class Config:
    server: str = ""
    token: str = ""
    push_to_talk: bool = False
    barge_in: bool = False  # only with an echo-cancelling speakerphone or headset
    input_device: int | None = None
    output_device: int | None = None
    extra: dict[str, object] = field(default_factory=dict)

    @property
    def ready(self) -> bool:
        return bool(self.server and self.token)

    @classmethod
    def load(cls) -> Config:
        cfg = cls()
        path = config_path()
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            known = {
                k: v for k, v in data.items() if k in cls.__dataclass_fields__ and k != "extra"
            }
            cfg = cls(**known, extra={k: v for k, v in data.items() if k not in known})
        # environment wins, so scripts and services can configure it without a file
        cfg.server = normalize_server(os.environ.get("ALFRED_SERVER", "")) or cfg.server
        cfg.token = os.environ.get("ALFRED_TOKEN", "") or cfg.token
        return cfg

    def save(self) -> Path:
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {k: v for k, v in asdict(self).items() if k != "extra"} | self.extra
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        if os.name == "posix":
            os.chmod(tmp, 0o600)  # the token is a password
        tmp.replace(path)
        return path
