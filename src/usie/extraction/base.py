"""Adapter contract for turning captured evidence into validated, attributable content."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

from usie.models import ArtifactDescriptor, ArtifactKind, ExtractionResult, Observation, Platform
from usie.routing import route_url
from usie.urlsafety import normalize_url


class AdapterErrorCode(StrEnum):
    INVALID_EVIDENCE = "invalid_evidence"
    EVIDENCE_TOO_LARGE = "evidence_too_large"
    MALFORMED_CONTENT = "malformed_content"


class AdapterError(ValueError):
    def __init__(self, code: AdapterErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ExtractionRequest:
    capture_id: str
    source_id: str
    submitted_url: str
    normalized_url: str
    platform: Platform
    observation: Observation
    artifact: ArtifactDescriptor
    content: bytes

    def validate(self) -> None:
        if self.artifact.kind is not ArtifactKind.RENDERED_DOM_HTML:
            raise AdapterError(AdapterErrorCode.INVALID_EVIDENCE, "expected rendered DOM evidence")
        if self.artifact.byte_size != len(self.content):
            raise AdapterError(AdapterErrorCode.INVALID_EVIDENCE, "evidence size does not match")
        if hashlib.sha256(self.content).hexdigest() != self.artifact.sha256:
            raise AdapterError(AdapterErrorCode.INVALID_EVIDENCE, "evidence hash does not match")
        if normalize_url(self.submitted_url) != self.normalized_url:
            raise AdapterError(AdapterErrorCode.INVALID_EVIDENCE, "normalized URL does not match")
        if route_url(self.submitted_url).platform is not self.platform:
            raise AdapterError(AdapterErrorCode.INVALID_EVIDENCE, "platform does not match URL")
        if str(uuid5(NAMESPACE_URL, self.normalized_url)) != self.source_id:
            raise AdapterError(
                AdapterErrorCode.INVALID_EVIDENCE, "source identity does not match URL"
            )
        if not urlsplit(self.submitted_url).hostname:
            raise AdapterError(AdapterErrorCode.INVALID_EVIDENCE, "URL has no hostname")


class Extractor(Protocol):
    name: str
    version: str

    def extract(self, request: ExtractionRequest) -> ExtractionResult: ...
