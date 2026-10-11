from __future__ import annotations

import json
import logging
from pathlib import Path

from usie.config import Settings
from usie.logging_utils import REDACTED, RedactingJsonFormatter, redact, redact_mapping, redact_url


def test_redact_text() -> None:
    for secret_text, leaked in [
        ("Authorization: Bearer abc123def", "abc123def"),
        ("Cookie: sid=SECRETVALUE; other=1", "SECRETVALUE"),
        ("Set-Cookie: auth=TOPSECRET; HttpOnly", "TOPSECRET"),
        ("password=hunter2&x=1", "hunter2"),
        ("api_key: sk-abcdefghijklmnop1234", "sk-abcdefghijklmnop1234"),
        ("fetching https://bob:pw123@example.com/a", "pw123"),
    ]:
        out = redact(secret_text)
        assert leaked not in out, out
        assert REDACTED in out


def test_redact_mapping_and_url() -> None:
    out = redact_mapping(
        {"cookies": [1], "nested": {"Authorization": "x"}, "ok": "fine", "msg": "token=abc"}
    )
    assert out["cookies"] == REDACTED and out["nested"]["Authorization"] == REDACTED
    assert out["ok"] == "fine" and "abc" not in out["msg"]
    assert "secret" not in redact_url("https://user:secret@example.com/p")


def test_json_formatter_redacts_message_fields_and_exceptions() -> None:
    fmt = RedactingJsonFormatter()
    try:
        raise RuntimeError("boom token=LEAKME")
    except RuntimeError:
        import sys

        rec = logging.LogRecord(
            "usie", logging.ERROR, __file__, 1, "got password=pw1", None, sys.exc_info()
        )
    rec.fields = {"session": "abc", "n": 1}
    line = fmt.format(rec)
    assert "pw1" not in line and "LEAKME" not in line and '"abc"' not in line
    assert json.loads(line)["level"] == "ERROR"


def test_settings_from_env_and_snapshot_hides_storage_path(tmp_path: Path) -> None:
    s = Settings.from_env(
        {
            "USIE_DATA_DIR": str(tmp_path),
            "USIE_STORAGE_STATE": "/s/secret.json",
            "USIE_MAX_CONCURRENCY": "0",
            "USIE_ALLOW_PRIVATE_HOSTS": "yes",
            "USIE_HOST_ALLOWLIST": "A.test, b.test",
        }
    )
    assert s.max_concurrency == 1 and s.allow_private_hosts
    assert s.host_allowlist == frozenset({"a.test", "b.test"})
    assert s.db_path == tmp_path / "usie.sqlite3"
    assert "secret" not in json.dumps(s.snapshot())
    assert s.snapshot()["authenticated_session"] is True
