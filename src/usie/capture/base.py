from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Protocol

from usie.models import ArtifactDescriptor, ArtifactKind, Observation
from usie.urlsafety import DestinationPolicy


@dataclass(frozen=True)
class CaptureRequest:
    capture_id: str
    url: str
    timeout_ms: int
    wait_ms: int
    policy: DestinationPolicy
    storage_state: Path | None = None
    max_artifact_bytes: int = 50 * 1024 * 1024


class ArtifactSink(Protocol):
    """Persists an artifact immediately, so evidence survives later failures."""

    def put(self, kind: ArtifactKind, data: bytes, media_type: str) -> ArtifactDescriptor: ...


class Capturer(Protocol):
    async def __aenter__(self) -> Capturer: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...
    async def capture(self, request: CaptureRequest, sink: ArtifactSink) -> Observation: ...
