from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src" / "usie"


def imported_modules(path: Path) -> set[str]:
    mods: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    return mods


def test_layer1_does_not_import_layer2_machinery() -> None:
    forbidden = ("yt_dlp", "subprocess", "usie.extract", "usie.adapters", "bs4", "lxml")
    for f in (SRC / "capture").glob("*.py"):
        bad = [m for m in imported_modules(f) if m.startswith(forbidden)]
        assert not bad, f"{f.name} imports Layer 2 modules: {bad}"


def test_layer1_does_not_depend_on_storage() -> None:
    for f in (SRC / "capture").glob("*.py"):
        assert not [m for m in imported_modules(f) if m.startswith(("usie.db", "sqlite3"))]


def run_cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "usie.cli", *args],
        capture_output=True,  # noqa: S603
        text=True,
        env=env,
        check=False,
    )


def test_cli_route_ok_and_rejects(tmp_path: Path) -> None:
    ok = run_cli("route", "https://x.com/u/status/1?utm_source=a")
    assert ok.returncode == 0
    data = json.loads(ok.stdout)
    assert data["platform"] == "X" and data["normalized_url"] == "https://x.com/u/status/1"
    bad = run_cli("route", "https://u:p@x.com/")
    assert bad.returncode == 2 and "credentials" in bad.stderr
    assert "p@" not in bad.stdout


@pytest.fixture
def cli_env(tmp_path: Path) -> dict[str, str]:
    import os

    return {**os.environ, "USIE_DATA_DIR": str(tmp_path / "d")}


def test_cli_db_status_report_verify(cli_env: dict[str, str]) -> None:
    st = run_cli("db-status", env=cli_env)
    assert st.returncode == 0 and json.loads(st.stdout) == {"schema_version": 3, "latest": 3}
    assert json.loads(run_cli("report", env=cli_env).stdout) == []
    v = run_cli("verify", env=cli_env)
    assert v.returncode == 0 and json.loads(v.stdout)["artifacts_checked"] == 0
    assert json.loads(run_cli("recover", env=cli_env).stdout)["runs"] == 0
