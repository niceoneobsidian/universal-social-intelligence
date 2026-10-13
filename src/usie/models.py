"""Typed domain models and the status/error taxonomies."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Status(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATUSES: frozenset[Status] = frozenset(
    {
        Status.SUCCESS,
        Status.PARTIAL,
        Status.BLOCKED,
        Status.UNAVAILABLE,
        Status.FAILED,
        Status.CANCELLED,
    }
)


class Platform(StrEnum):
    X = "X"
    LINKEDIN = "LINKEDIN"
    TIKTOK = "TIKTOK"
    INSTAGRAM = "INSTAGRAM"
    THREADS = "THREADS"
    YOUTUBE = "YOUTUBE"
    FACEBOOK = "FACEBOOK"
    UNIVERSAL = "UNIVERSAL"


class ErrorCode(StrEnum):
    INVALID_URL = "invalid_url"
    UNSAFE_DESTINATION = "unsafe_destination"
    NAVIGATION_TIMEOUT = "navigation_timeout"
    NAVIGATION_ERROR = "navigation_error"
    HTTP_SERVER_ERROR = "http_server_error"
    BROWSER_UNAVAILABLE = "browser_unavailable"
    EVIDENCE_WRITE_FAILED = "evidence_write_failed"
    SESSION_STATE_INVALID = "session_state_invalid"
    INTERRUPTED = "interrupted"
    INTERNAL_ERROR = "internal_error"


class ArtifactKind(StrEnum):
    RENDERED_DOM_HTML = "rendered_dom_html"
    RESPONSE_BODY = "response_body"
    SCREENSHOT_VIEWPORT = "screenshot_viewport"
    SCREENSHOT_FULL_PAGE = "screenshot_full_page"
    NAVIGATION_RECORD = "navigation_record"
    CAPTURE_MANIFEST = "capture_manifest"


# Honest descriptions of what each artifact actually contains (and does not).
ARTIFACT_DESCRIPTIONS: dict[ArtifactKind, str] = {
    ArtifactKind.RENDERED_DOM_HTML: (
        "Serialized DOM (page.content()) after load plus a short settle delay. Not the original "
        "server bytes; excludes cross-origin frames, shadow-DOM internals, JS heap state and "
        "network subresources."
    ),
    ArtifactKind.RESPONSE_BODY: (
        "Decoded body of the final main-frame HTTP response as delivered by the server. May be "
        "an app shell that renders content via JavaScript."
    ),
    ArtifactKind.SCREENSHOT_VIEWPORT: "PNG of the initial 1440x1000 viewport at capture time.",
    ArtifactKind.SCREENSHOT_FULL_PAGE: (
        "PNG of the full scrollable page as rendered by Chromium. Lazy-loaded or virtualized "
        "content below the fold may be absent."
    ),
    ArtifactKind.NAVIGATION_RECORD: (
        "JSON: submitted/final URL, HTTP status, HTTP redirect chain (not JS/meta redirects), "
        "redacted response headers, blocked requests, tool versions."
    ),
    ArtifactKind.CAPTURE_MANIFEST: "JSON listing every artifact of this capture with SHA-256.",
}


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RedirectHop(_Frozen):
    url: str
    status: int | None = None
    location: str | None = None


class Observation(_Frozen):
    """What Layer 1 observed in the browser. Contains no extracted/derived content."""

    http_status: int | None = None
    final_url: str | None = None
    title: str | None = None
    redirect_chain: list[RedirectHop] = Field(default_factory=list)
    response_headers: dict[str, str] = Field(default_factory=dict)
    body_text_sample: str | None = None
    blocked_requests: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    error_code: ErrorCode | None = None
    tool_versions: dict[str, str] = Field(default_factory=dict)


class ArtifactDescriptor(_Frozen):
    artifact_id: str
    kind: ArtifactKind
    relative_path: str
    sha256: str
    byte_size: int
    media_type: str
    description: str


class Completeness(_Frozen):
    expected: list[str]
    present: list[str]
    missing: list[str]


class ExtractionProvenance(_Frozen):
    artifact_id: str
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    locator: str = Field(min_length=1, max_length=240)


class ExtractionResult(_Frozen):
    extraction_id: str
    capture_id: str
    source_id: str
    normalized_url: str
    extractor: str
    extractor_version: str
    status: Status
    post_text: str | None = None
    author: str | None = None
    title: str | None = None
    canonical_url: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    provenance: dict[str, ExtractionProvenance] = Field(default_factory=dict)
    error_code: str | None = None
    issues: list[str] = Field(default_factory=list)
    schema_version: str = "1"
    created_at: str

    @model_validator(mode="after")
    def validate_extracted_fields(self) -> ExtractionResult:
        if self.status not in {Status.SUCCESS, Status.PARTIAL}:
            raise ValueError("an extraction result must be success or partial")
        fields = {
            "post_text": self.post_text,
            "author": self.author,
            "title": self.title,
            "canonical_url": self.canonical_url,
            "metadata": self.metadata or None,
        }
        for name, value in fields.items():
            if value is not None and name not in self.provenance:
                raise ValueError(f"extracted field {name} is missing provenance")
        if self.post_text is not None and not self.post_text.strip():
            raise ValueError("post_text must not be blank")
        if self.status is Status.SUCCESS and self.post_text is None:
            raise ValueError("success requires validated post_text")
        if self.error_code is not None and self.status is Status.SUCCESS:
            raise ValueError("an extraction with an error cannot be successful")
        return self


class StatusDecision(_Frozen):
    status: Status
    reason: str
    error_code: ErrorCode | None = None
    retryable: bool = False
