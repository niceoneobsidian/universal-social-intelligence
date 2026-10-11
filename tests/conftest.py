from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from usie.config import Settings
from usie.db import SqliteRepository


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        delay_ms_between_jobs=0,
        max_attempts=3,
        retry_base_delay_s=0.0,
        wait_after_navigation_ms=0,
    )


@pytest.fixture
def repo(settings: Settings) -> Iterator[SqliteRepository]:
    r = SqliteRepository(settings.db_path)
    yield r
    r.close()
