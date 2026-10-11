"""Pipeline orchestration: validate -> preserve evidence -> classify -> store.

Layer 2 (extraction) plugs in after evidence is stored (P1). Evidence is persisted by the sink
as it is produced, so a later failure never loses what was already captured.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from usie import __version__
from usie.capture.base import Capturer, CaptureRequest
from usie.capture.browser import navigation_record
from usie.config import Settings
from usie.db.repository import SqliteRepository
from usie.evidence import (
    FILENAMES,
    EvidenceError,
    json_bytes,
    new_capture_id,
    store_artifact,
)
from usie.logging_utils import redact_url
from usie.models import (
    ARTIFACT_DESCRIPTIONS,
    ArtifactDescriptor,
    ArtifactKind,
    Completeness,
    ErrorCode,
    Observation,
    Status,
    StatusDecision,
)
from usie.retry import RetryPolicy
from usie.routing import route_url
from usie.status import classify_observation
from usie.urlsafety import DestinationPolicy, InvalidUrlError, validate_submitted_url

log = logging.getLogger("usie.collector")

EXPECTED_ARTIFACTS = [
    ArtifactKind.RENDERED_DOM_HTML,
    ArtifactKind.RESPONSE_BODY,
    ArtifactKind.SCREENSHOT_VIEWPORT,
    ArtifactKind.SCREENSHOT_FULL_PAGE,
    ArtifactKind.NAVIGATION_RECORD,
]


def read_urls(path: str | Path) -> list[str]:
    return [
        ln.strip()
        for ln in Path(path).read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]


class _Sink:
    def __init__(self, repo: SqliteRepository, root: Path, capture_id: str, max_bytes: int) -> None:
        self.repo, self.root, self.capture_id, self.max_bytes = repo, root, capture_id, max_bytes
        self.stored: list[ArtifactDescriptor] = []

    def put(self, kind: ArtifactKind, data: bytes, media_type: str) -> ArtifactDescriptor:
        import uuid

        stored = store_artifact(self.root, self.capture_id, kind, data, self.max_bytes)
        descriptor = ArtifactDescriptor(
            artifact_id=str(uuid.uuid4()),
            kind=kind,
            relative_path=stored.relative_path,
            sha256=stored.sha256,
            byte_size=stored.byte_size,
            media_type=media_type,
            description=ARTIFACT_DESCRIPTIONS[kind],
        )
        self.repo.add_artifact(self.capture_id, descriptor)
        self.stored.append(descriptor)
        return descriptor


class Collector:
    def __init__(
        self,
        settings: Settings,
        repo: SqliteRepository,
        capturer: Capturer,
        sleep: Any = asyncio.sleep,
    ) -> None:
        self.settings, self.repo, self.capturer, self._sleep = settings, repo, capturer, sleep
        self.policy = DestinationPolicy(settings.allow_private_hosts, settings.host_allowlist)
        self.retry = RetryPolicy(
            max_attempts=settings.max_attempts, base_delay_s=settings.retry_base_delay_s
        )

    async def run(self, urls: Sequence[str]) -> dict[str, Any]:
        run_id = self.repo.create_run(len(urls), self.settings.snapshot(), {"usie": __version__})
        rejected: list[dict[str, str]] = []
        jobs: list[tuple[str, str, str, str]] = []  # job_id, source_id, submitted, platform
        duplicates = 0
        for raw in urls:
            try:
                valid = validate_submitted_url(raw, self.settings.max_url_length)
                platform = route_url(valid.submitted).platform
            except (InvalidUrlError, ValueError) as exc:
                entry = {"url": redact_url(raw)[:200], "reason": str(exc)}
                rejected.append(entry)
                self.repo.audit(
                    "input_rejected", subject_type="run", subject_id=run_id, details=entry
                )
                continue
            source_id = self.repo.ensure_source(valid.submitted, valid.normalized, platform.value)
            job_id, created = self.repo.create_job(run_id, source_id, valid.submitted)
            if created:
                jobs.append((job_id, source_id, valid.submitted, platform.value))
            else:
                duplicates += 1

        semaphore = asyncio.Semaphore(self.settings.max_concurrency)
        results: dict[str, Status] = {}

        async def worker(job_id: str, source_id: str, submitted: str) -> None:
            async with semaphore:
                results[job_id] = await self._process_job(run_id, job_id, source_id, submitted)
                if self.settings.delay_ms_between_jobs:
                    await self._sleep(self.settings.delay_ms_between_jobs / 1000)

        tasks = [asyncio.ensure_future(worker(j, s, u)) for j, s, u, _ in jobs]
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.repo.cancel_unfinished(run_id)
            self.repo.finish_run(run_id, Status.CANCELLED, {"reason": "interrupted by operator"})
            raise
        counts: dict[str, int] = {}
        for status in results.values():
            counts[status.value] = counts.get(status.value, 0) + 1
        summary: dict[str, Any] = {
            "run_id": run_id,
            "input_count": len(urls),
            "accepted_jobs": len(jobs),
            "duplicates_in_manifest": duplicates,
            "rejected": rejected,
            "job_statuses": counts,
        }
        self.repo.finish_run(run_id, Status.SUCCESS if not rejected else Status.PARTIAL, summary)
        return summary

    async def _process_job(
        self, run_id: str, job_id: str, source_id: str, submitted: str
    ) -> Status:
        self.repo.start_job(job_id)
        decision = StatusDecision(
            status=Status.FAILED, reason="not attempted", error_code=ErrorCode.INTERNAL_ERROR
        )
        attempt = 0
        while True:
            attempt += 1
            capture_id = new_capture_id()
            self.repo.start_capture(capture_id, job_id, run_id, source_id, attempt, submitted)
            try:
                decision = await self._one_capture(capture_id, submitted)
            except asyncio.CancelledError:
                self.repo.finish_capture(
                    capture_id,
                    Status.CANCELLED,
                    "cancelled",
                    None,
                    None,
                    None,
                    ["cancelled_by_operator"],
                )
                self.repo.finish_job(job_id, Status.CANCELLED, "cancelled", None)
                raise
            if not self.retry.should_retry(decision, attempt):
                break
            await self._sleep(self.retry.delay_s(attempt))
        self.repo.finish_job(
            job_id,
            decision.status,
            decision.reason,
            decision.error_code.value if decision.error_code else None,
        )
        return decision.status

    async def _one_capture(self, capture_id: str, submitted: str) -> StatusDecision:
        sink = _Sink(
            self.repo, self.settings.evidence_root, capture_id, self.settings.max_artifact_bytes
        )
        issues: list[str] = []
        obs: Observation | None = None
        decision: StatusDecision | None = None
        try:
            request = CaptureRequest(
                capture_id=capture_id,
                url=submitted,
                timeout_ms=self.settings.navigation_timeout_ms,
                wait_ms=self.settings.wait_after_navigation_ms,
                policy=self.policy,
                storage_state=self.settings.storage_state,
                max_artifact_bytes=self.settings.max_artifact_bytes,
            )
            obs = await self.capturer.capture(request, sink)
            issues.extend(obs.errors)
            decision = classify_observation(obs)
        except asyncio.CancelledError:
            raise
        except EvidenceError as exc:
            issues.append(f"evidence:{exc}")
            decision = StatusDecision(
                status=Status.FAILED,
                reason="evidence write failed",
                error_code=ErrorCode.EVIDENCE_WRITE_FAILED,
            )
        except Exception as exc:
            log.exception("capture failed", extra={"fields": {"capture_id": capture_id}})
            issues.append(f"{type(exc).__name__}:{str(exc)[:300]}")
            decision = StatusDecision(
                status=Status.FAILED,
                reason="capture raised an exception",
                error_code=ErrorCode.INTERNAL_ERROR,
            )
        # Seal on every path: whatever evidence exists gets a manifest.
        try:
            self._seal(sink, submitted, capture_id, obs, issues)
        except EvidenceError as exc:
            issues.append(f"seal:{exc}")
            decision = StatusDecision(
                status=Status.FAILED,
                reason="evidence write failed",
                error_code=ErrorCode.EVIDENCE_WRITE_FAILED,
            )
        present = [a.kind.value for a in sink.stored]
        expected = [k.value for k in EXPECTED_ARTIFACTS]
        completeness = Completeness(
            expected=expected, present=present, missing=[k for k in expected if k not in present]
        )
        self.repo.finish_capture(
            capture_id,
            decision.status,
            decision.reason,
            decision.error_code.value if decision.error_code else None,
            obs,
            completeness,
            issues,
        )
        return decision

    def _seal(
        self,
        sink: _Sink,
        submitted: str,
        capture_id: str,
        obs: Observation | None,
        issues: list[str],
    ) -> None:
        if obs is not None:
            sink.put(
                ArtifactKind.NAVIGATION_RECORD,
                navigation_record(submitted, obs),
                "application/json",
            )
        manifest = {
            "capture_id": capture_id,
            "submitted_url": submitted,
            "artifacts": [
                {
                    "kind": a.kind.value,
                    "path": FILENAMES[a.kind],
                    "sha256": a.sha256,
                    "byte_size": a.byte_size,
                    "description": a.description,
                }
                for a in sink.stored
            ],
            "issues": issues,
        }
        sink.put(ArtifactKind.CAPTURE_MANIFEST, json_bytes(manifest), "application/json")
