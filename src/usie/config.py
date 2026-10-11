"""Environment-based configuration. Safe defaults; nothing here reads secrets into logs."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    storage_state: Path | None = None
    navigation_timeout_ms: int = 30_000
    wait_after_navigation_ms: int = 1_200
    max_concurrency: int = 2
    delay_ms_between_jobs: int = 500
    max_attempts: int = 3
    retry_base_delay_s: float = 2.0
    max_artifact_bytes: int = 50 * 1024 * 1024
    max_url_length: int = 2048
    allow_private_hosts: bool = False
    host_allowlist: frozenset[str] = frozenset()

    @property
    def db_path(self) -> Path:
        return self.data_dir / "usie.sqlite3"

    @property
    def evidence_root(self) -> Path:
        return self.data_dir / "evidence"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        e = os.environ if env is None else env
        state = e.get("USIE_STORAGE_STATE")
        allow = e.get("USIE_HOST_ALLOWLIST", "")
        return cls(
            data_dir=Path(e.get("USIE_DATA_DIR", "~/.usie")).expanduser(),
            storage_state=Path(state).expanduser() if state else None,
            navigation_timeout_ms=int(e.get("USIE_NAVIGATION_TIMEOUT_MS", "30000")),
            wait_after_navigation_ms=int(e.get("USIE_WAIT_AFTER_NAVIGATION_MS", "1200")),
            max_concurrency=max(1, int(e.get("USIE_MAX_CONCURRENCY", "2"))),
            delay_ms_between_jobs=max(0, int(e.get("USIE_DELAY_MS_BETWEEN_JOBS", "500"))),
            max_attempts=max(1, int(e.get("USIE_MAX_ATTEMPTS", "3"))),
            allow_private_hosts=_bool(e.get("USIE_ALLOW_PRIVATE_HOSTS"), False),
            host_allowlist=frozenset(h.strip().lower() for h in allow.split(",") if h.strip()),
        )

    def snapshot(self) -> dict[str, object]:
        """Non-secret view of settings for run records (storage state path is not recorded)."""
        return {
            "navigation_timeout_ms": self.navigation_timeout_ms,
            "wait_after_navigation_ms": self.wait_after_navigation_ms,
            "max_concurrency": self.max_concurrency,
            "delay_ms_between_jobs": self.delay_ms_between_jobs,
            "max_attempts": self.max_attempts,
            "allow_private_hosts": self.allow_private_hosts,
            "authenticated_session": self.storage_state is not None,
        }
