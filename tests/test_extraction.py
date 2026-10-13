from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import pytest

from usie.extraction import AdapterError, AdapterErrorCode, ExtractionRequest, OpenGraphAdapter
from usie.models import ArtifactDescriptor, ArtifactKind, Observation, Platform, Status
from usie.urlsafety import normalize_url

URL = "https://x.com/user/status/1?utm_source=fixture"


def request_for(content: bytes) -> ExtractionRequest:
    normalized = normalize_url(URL)
    artifact = ArtifactDescriptor(
        artifact_id=str(uuid.uuid4()),
        kind=ArtifactKind.RENDERED_DOM_HTML,
        relative_path="captures/00/example/rendered_dom.html",
        sha256=hashlib.sha256(content).hexdigest(),
        byte_size=len(content),
        media_type="text/html",
        description="fixture",
    )
    return ExtractionRequest(
        capture_id=str(uuid.uuid4()),
        source_id=str(uuid5(NAMESPACE_URL, normalized)),
        submitted_url=URL,
        normalized_url=normalized,
        platform=Platform.X,
        observation=Observation(http_status=200, final_url=URL),
        artifact=artifact,
        content=content,
    )


def test_open_graph_extracts_content_and_provenance() -> None:
    fixture = (
        Path(__file__).parent / "fixtures" / "extraction" / "open_graph_post.html"
    ).read_bytes()
    result = OpenGraphAdapter().extract(request_for(fixture))

    assert result.status is Status.SUCCESS
    assert result.post_text == "A fixture post with verified provenance."
    assert result.title == "A fixture post"
    assert result.author == "Example Author"
    assert result.canonical_url == "https://x.com/user/status/1"
    assert result.provenance["post_text"].artifact_sha256 == hashlib.sha256(fixture).hexdigest()
    assert result.provenance["post_text"].locator == "meta[property=og:description]"


def test_jsonld_post_body_is_a_validated_fallback() -> None:
    request = request_for(
        b'<script type="application/ld+json">'
        b'{"@type":"SocialMediaPosting","articleBody":"JSON-LD post body"}'
        b"</script>"
    )

    result = OpenGraphAdapter().extract(request)

    assert result.status is Status.SUCCESS
    assert result.post_text == "JSON-LD post body"
    assert result.provenance["post_text"].locator == "jsonld:post_text"


def test_title_without_extracted_post_text_stays_partial() -> None:
    result = OpenGraphAdapter().extract(request_for(b"<title>Observed title only</title>"))

    assert result.status is Status.PARTIAL
    assert result.title == "Observed title only"
    assert result.post_text is None


def test_adapter_rejects_evidence_hash_mismatch() -> None:
    request = request_for(b"<p>captured</p>")
    altered = ExtractionRequest(
        capture_id=request.capture_id,
        source_id=request.source_id,
        submitted_url=request.submitted_url,
        normalized_url=request.normalized_url,
        platform=request.platform,
        observation=request.observation,
        artifact=request.artifact,
        content=b"<p>tampered</p>",
    )

    with pytest.raises(AdapterError) as error:
        OpenGraphAdapter().extract(altered)
    assert error.value.code is AdapterErrorCode.INVALID_EVIDENCE


def test_adapter_rejects_mismatched_source_identity() -> None:
    request = request_for(b"<p>captured</p>")
    altered = ExtractionRequest(
        capture_id=request.capture_id,
        source_id=str(uuid.uuid4()),
        submitted_url=request.submitted_url,
        normalized_url=request.normalized_url,
        platform=request.platform,
        observation=request.observation,
        artifact=request.artifact,
        content=request.content,
    )

    with pytest.raises(AdapterError, match="source identity"):
        OpenGraphAdapter().extract(altered)
