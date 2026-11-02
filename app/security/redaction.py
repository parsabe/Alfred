"""Secret redaction for logs.

Two layers: exact values of every configured secret, and generic patterns for things that look
like credentials (API keys, bearer headers, JWTs) even if they are not ours.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from typing import Any

MASK = "[REDACTED]"

_PATTERNS = [
    re.compile(r"sk-(?:ant-|fish-|proj-)?[A-Za-z0-9_\-]{16,}"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-~+/=]{8,}"),
    re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)((?:api[_-]?key|token|secret|password)[\"']?\s*[:=]\s*[\"']?)[^\s\"',}]{6,}"),
]

SENSITIVE_KEYS = re.compile(r"(?i)(key|token|secret|password|authorization|cookie)")


class Redactor:
    def __init__(self, secrets: Iterable[str] = ()) -> None:
        # Longest first so a secret that contains another is masked whole.
        self._secrets = sorted({s for s in secrets if len(s) >= 6}, key=len, reverse=True)

    def redact(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, MASK)
        for pattern in _PATTERNS:
            if pattern.groups:
                text = pattern.sub(lambda m: m.group(1) + MASK, text)
            else:
                text = pattern.sub(MASK, text)
        return text

    def redact_obj(self, obj: Any) -> Any:
        """Recursively redact a JSON-like structure (used for audit args)."""
        if isinstance(obj, str):
            return self.redact(obj)
        if isinstance(obj, dict):
            return {
                k: (MASK if SENSITIVE_KEYS.search(str(k)) else self.redact_obj(v))
                for k, v in obj.items()
            }
        if isinstance(obj, list | tuple):
            return [self.redact_obj(v) for v in obj]
        return obj


class RedactingFilter(logging.Filter):
    def __init__(self, redactor: Redactor) -> None:
        super().__init__()
        self.redactor = redactor

    def filter(self, record: logging.LogRecord) -> bool:
        # Redact msg and each arg separately so formatters that rely on structured args
        # (e.g. uvicorn's access log) keep working.
        r = self.redactor
        if isinstance(record.msg, str):
            record.msg = r.redact(record.msg)
        if isinstance(record.args, dict):
            record.args = {
                k: r.redact(v) if isinstance(v, str) else v for k, v in record.args.items()
            }
        elif isinstance(record.args, tuple):
            record.args = tuple(r.redact(a) if isinstance(a, str) else a for a in record.args)
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = r.redact(record.exc_text)
        return True


def install(redactor: Redactor, level: str = "INFO", json_logs: bool = False) -> None:
    """Configure root logging with the redaction filter on every handler."""
    fmt = (
        '{"t":"%(asctime)s","lvl":"%(levelname)s","log":"%(name)s","msg":"%(message)s"}'
        if json_logs
        else "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    )
    root = logging.getLogger()
    root.setLevel(level.upper())
    if not root.handlers:
        root.addHandler(logging.StreamHandler())
    flt = RedactingFilter(redactor)
    for handler in root.handlers:
        handler.setFormatter(logging.Formatter(fmt))
        for existing in list(handler.filters):
            if isinstance(existing, RedactingFilter):
                handler.removeFilter(existing)
        handler.addFilter(flt)
    # Uvicorn installs its own handlers; cover them too.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "httpx", "anthropic", "openai"):
        for handler in logging.getLogger(name).handlers:
            handler.addFilter(flt)
