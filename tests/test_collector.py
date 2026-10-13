from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from types import TracebackType
from typing import Any

import pytest

from usie.capture.base import ArtifactSink, CaptureRequest
from usie.collector import Collector
from usie.config import Settings
from usie.db import SqliteRepository
from usie.evidence import verify_artifact
from usie.models import ArtifactKind, ErrorCode, Observation


class FakeCapturer:
    """Scriptable Layer 1 stand-in. `script` maps URL -> list of per-attempt behaviours."""

    def __init__(
        self,
        script: dict[str, list[Any]] | None = None,
        delay: float = 0.0,
        html: bytes | None = None,
    ) -> None:
        self.script = script or {}
        self.calls: list[str] = []
        self.delay = delay
        self.html = html or b"<html>hello</html>"

    async def __aenter__(self) -> FakeCapturer:
        return self

    async def __aexit__(
        self, a: type[BaseException] | None, b: BaseException | None, c: TracebackType | None
    ) -> None:
        return None

    async def capture(self, request: CaptureRequest, sink: ArtifactSink) -> Observation:
        self.calls.append(request.url)
        if self.delay:
            await asyncio.sleep(self.delay)
        steps = self.script.get(request.url, [])
        step = steps[len(self.calls_for(request.url)) - 1] if steps else "ok"
        if step == "ok":
            sink.put(ArtifactKind.RENDERED_DOM_HTML, self.html, "text/html")
            sink.put(ArtifactKind.SCREENSHOT_VIEWPORT, b"\x89PNG", "image/png")
            return Observation(
                http_status=200,
                final_url=request.url,
                title="Hello",
                body_text_sample="hello " * 10,
            )
        if step == "503":
            return Observation(http_status=503, final_url=request.url)
        if step == "403":
            return Observation(http_status=403, final_url=request.url)
        if step == "crash_after_html":
            sink.put(ArtifactKind.RENDERED_DOM_HTML, b"<html>partial</html>", "text/html")
            raise RuntimeError("renderer died token=ABC123")
        raise AssertionError(step)

    def calls_for(self, url: str) -> list[str]:
        return [c for c in self.calls if c == url]


def rows(repo: SqliteRepository, sql: str) -> list[dict[str, Any]]:
    return repo.query(sql)


async def test_happy_path_stores_evidence_and_partial_status(
    settings: Settings, repo: SqliteRepository
) -> None:
    summary = await Collector(settings, repo, FakeCapturer()).run(["https://a.test/1"])
    assert summary["job_statuses"] == {"partial": 1}
    cap = rows(repo, "SELECT * FROM captures")[0]
    assert cap["status"] == "partial" and cap["status_reason"].startswith("evidence_only")
    arts = rows(repo, "SELECT * FROM artifacts")
    assert {a["kind"] for a in arts} == {
        "rendered_dom_html",
        "screenshot_viewport",
        "navigation_record",
        "capture_manifest",
    }
    for a in arts:
        assert (
            verify_artifact(settings.evidence_root, a["relative_path"], a["sha256"], a["byte_size"])
            is None
        )
    comp = json.loads(cap["completeness_json"])
    assert set(comp["missing"]) == {"response_body", "screenshot_full_page"}
    manifest = next(a for a in arts if a["kind"] == "capture_manifest")
    listed = json.loads((settings.evidence_root / manifest["relative_path"]).read_text())
    assert {x["kind"] for x in listed["artifacts"]} == {
        "rendered_dom_html",
        "screenshot_viewport",
        "navigation_record",
    }


async def test_validated_extraction_promotes_status_and_persists_provenance(
    settings: Settings, repo: SqliteRepository
) -> None:
    fixture = (
        Path(__file__).parent / "fixtures" / "extraction" / "open_graph_post.html"
    ).read_bytes()
    summary = await Collector(settings, repo, FakeCapturer(html=fixture)).run(
        ["https://x.com/user/status/1?utm_source=fixture"]
    )
    assert summary["job_statuses"] == {"success": 1}
    capture = rows(repo, "SELECT * FROM captures")[0]
    assert capture["status"] == "success"
    extracted = rows(repo, "SELECT * FROM extraction_results")
    assert len(extracted) == 1
    assert extracted[0]["post_text"] == "A fixture post with verified provenance."
    assert extracted[0]["source_id"] == capture["source_id"]
    assert extracted[0]["normalized_url"] == "https://x.com/user/status/1"
    provenance = json.loads(extracted[0]["provenance_json"])
    assert provenance["post_text"]["artifact_sha256"]
    assert provenance["post_text"]["locator"] == "meta[property=og:description]"
    compatibility_row = rows(repo, "SELECT * FROM social_evidence")[0]
    assert compatibility_row["status_label"] == "success"
    assert compatibility_row["post_text"] == extracted[0]["post_text"]


async def test_rejected_inputs_are_audited_without_credentials(
    settings: Settings, repo: SqliteRepository
) -> None:
    urls = ["https://user:hunter2@a.test/", "ftp://a.test/", "not a url", "https://a.test/ok"]
    summary = await Collector(settings, repo, FakeCapturer()).run(urls)
    assert len(summary["rejected"]) == 3 and summary["accepted_jobs"] == 1
    dump = json.dumps(rows(repo, "SELECT details_json FROM audit_events"))
    assert "hunter2" not in dump and "hunter2" not in json.dumps(summary)
    assert len(rows(repo, "SELECT * FROM audit_events WHERE action='input_rejected'")) == 3


async def test_duplicates_in_manifest_collapse_to_one_job(
    settings: Settings, repo: SqliteRepository
) -> None:
    cap = FakeCapturer()
    summary = await Collector(settings, repo, cap).run(
        ["https://a.test/p?utm_source=a", "https://a.test/p?utm_source=b", "https://a.test/p"]
    )
    assert summary["accepted_jobs"] == 1 and summary["duplicates_in_manifest"] == 2
    assert len(cap.calls) == 1


async def test_repeated_runs_preserve_history(settings: Settings, repo: SqliteRepository) -> None:
    cap = FakeCapturer()
    c = Collector(settings, repo, cap)
    await c.run(["https://a.test/1"])
    await c.run(["https://a.test/1"])
    assert rows(repo, "SELECT COUNT(*) AS n FROM sources")[0]["n"] == 1
    assert rows(repo, "SELECT COUNT(*) AS n FROM captures")[0]["n"] == 2
    paths = [r["relative_path"] for r in rows(repo, "SELECT relative_path FROM artifacts")]
    assert len(paths) == len(set(paths))  # nothing overwritten


async def test_transient_failure_retries_then_succeeds(
    settings: Settings, repo: SqliteRepository
) -> None:
    sleeps: list[float] = []

    async def fake_sleep(s: float) -> None:
        sleeps.append(s)

    cap = FakeCapturer({"https://a.test/1": ["503", "503", "ok"]})
    summary = await Collector(settings, repo, cap, sleep=fake_sleep).run(["https://a.test/1"])
    assert summary["job_statuses"] == {"partial": 1}
    statuses = [r["status"] for r in rows(repo, "SELECT status FROM captures ORDER BY attempt")]
    assert statuses == ["failed", "failed", "partial"]
    assert rows(repo, "SELECT attempts FROM jobs")[0]["attempts"] == 3


async def test_retries_are_bounded(settings: Settings, repo: SqliteRepository) -> None:
    cap = FakeCapturer({"https://a.test/1": ["503"] * 10})
    summary = await Collector(settings, repo, cap).run(["https://a.test/1"])
    assert summary["job_statuses"] == {"failed": 1}
    assert len(cap.calls) == settings.max_attempts


async def test_blocked_is_not_retried(settings: Settings, repo: SqliteRepository) -> None:
    cap = FakeCapturer({"https://a.test/1": ["403"]})
    summary = await Collector(settings, repo, cap).run(["https://a.test/1"])
    assert summary["job_statuses"] == {"blocked": 1} and len(cap.calls) == 1


async def test_evidence_survives_capture_crash(settings: Settings, repo: SqliteRepository) -> None:
    cap = FakeCapturer({"https://a.test/1": ["crash_after_html"] * 3})
    settings = replace(settings, max_attempts=1)
    summary = await Collector(settings, repo, cap).run(["https://a.test/1"])
    assert summary["job_statuses"] == {"failed": 1}
    cap_row = rows(repo, "SELECT * FROM captures")[0]
    assert cap_row["error_code"] == ErrorCode.INTERNAL_ERROR.value
    kinds = {a["kind"] for a in rows(repo, "SELECT kind FROM artifacts")}
    assert kinds == {"rendered_dom_html", "capture_manifest"}  # html kept + sealed
    html = next(settings.evidence_root.rglob("rendered_dom.html"))
    assert html.read_bytes() == b"<html>partial</html>"


async def test_evidence_write_failure_is_reported(
    settings: Settings, repo: SqliteRepository
) -> None:
    tiny = replace(settings, max_artifact_bytes=5, max_attempts=1)
    summary = await Collector(tiny, repo, FakeCapturer()).run(["https://a.test/1"])
    assert summary["job_statuses"] == {"failed": 1}
    assert rows(repo, "SELECT error_code FROM captures")[0]["error_code"] == "evidence_write_failed"


async def test_concurrency_is_bounded(settings: Settings, repo: SqliteRepository) -> None:
    active = 0
    peak = 0

    class Counting(FakeCapturer):
        async def capture(self, request: CaptureRequest, sink: ArtifactSink) -> Observation:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.02)
            active -= 1
            return Observation(http_status=200, final_url=request.url, title="t")

    s = replace(settings, max_concurrency=2)
    await Collector(s, repo, Counting()).run([f"https://a.test/{i}" for i in range(8)])
    assert peak == 2


async def test_cancellation_marks_unfinished_and_keeps_completed(
    settings: Settings, repo: SqliteRepository
) -> None:
    s = replace(settings, max_concurrency=1)
    cap = FakeCapturer(delay=0.2)
    task = asyncio.ensure_future(
        Collector(s, repo, cap).run([f"https://a.test/{i}" for i in range(4)])
    )
    await asyncio.sleep(0.3)  # first job done, second in flight
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    jobs = sorted(r["status"] for r in rows(repo, "SELECT status FROM jobs"))
    assert jobs.count("partial") >= 1 and jobs.count("cancelled") >= 2
    assert not rows(repo, "SELECT * FROM captures WHERE status='running'")
    assert rows(repo, "SELECT status FROM runs")[0]["status"] == "cancelled"
    assert rows(repo, "SELECT COUNT(*) AS n FROM artifacts")[0]["n"] > 0  # evidence kept


def test_data_dir_layout_is_outside_source_tree(settings: Settings, tmp_path: Path) -> None:
    assert settings.evidence_root.is_relative_to(tmp_path)
