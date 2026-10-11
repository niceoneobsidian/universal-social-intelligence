"""Structured logging with secret redaction. Uses only standard logging levels."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from typing import Any

REDACTED = "[REDACTED]"

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\b(set-cookie|cookie)\b(\s*[:=]\s*)[^\r\n]+"), rf"\1\2{REDACTED}"),
    (
        re.compile(r"(?i)(authorization\s*[:=]\s*)(bearer\s+|basic\s+)?[^\s,;\"']+"),
        rf"\1{REDACTED}",
    ),
    (
        re.compile(
            r"(?i)\b(password|passwd|secret|api[_-]?key|access[_-]?token|refresh[_-]?token"
            r"|token|sessionid|session_id|csrf[_-]?token)\b(\s*[=:]\s*)[^\s&,;\"']+"
        ),
        rf"\1\2{REDACTED}",
    ),
    (re.compile(r"(?i)(://)[^/\s:@]+:[^/\s@]*@"), rf"\1{REDACTED}@"),
    (re.compile(r"\b(sk|pk|ghp|gho|xox[bp])[-_][A-Za-z0-9_-]{16,}"), REDACTED),
)

_SENSITIVE_KEYS = re.compile(
    r"(?i)(cookie|authorization|password|passwd|secret|token|api[_-]?key|session)"
)


def redact(text: str) -> str:
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def redact_mapping(data: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in data.items():
        if _SENSITIVE_KEYS.search(key):
            out[key] = REDACTED
        elif isinstance(value, Mapping):
            out[key] = redact_mapping(value)
        elif isinstance(value, str):
            out[key] = redact(value)
        else:
            out[key] = value
    return out


def redact_url(url: str) -> str:
    """Strip userinfo from a URL so it is safe to log or store in an audit event."""
    return re.sub(r"(?i)^([a-z][a-z0-9+.-]*://)[^/?#]*@", rf"\1{REDACTED}@", url.strip())


class RedactingJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
        }
        extra = getattr(record, "fields", None)
        if isinstance(extra, Mapping):
            payload["fields"] = redact_mapping(extra)
        if record.exc_info:
            payload["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(RedactingJsonFormatter())
    root = logging.getLogger("usie")
    root.handlers[:] = [handler]
    root.setLevel(level)
    root.propagate = False
