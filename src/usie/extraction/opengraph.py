"""Generic Open Graph, Twitter Card and JSON-LD extraction from a captured DOM."""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any

from usie.extraction.base import AdapterError, AdapterErrorCode, ExtractionRequest
from usie.models import ExtractionProvenance, ExtractionResult, Status
from usie.urlsafety import InvalidUrlError, validate_submitted_url

MAX_HTML_BYTES = 5 * 1024 * 1024
_SPACE = re.compile(r"\s+")


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = _SPACE.sub(" ", value).strip()
    return cleaned or None


class _MetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.canonical: str | None = None
        self.title_parts: list[str] = []
        self.jsonld_parts: list[str] = []
        self._in_title = False
        self._in_jsonld = False
        self._current_jsonld: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value for key, value in attrs if value is not None}
        if tag.lower() == "meta":
            key = (values.get("property") or values.get("name") or "").lower()
            content = _clean(values.get("content"))
            if key and content:
                self.meta.setdefault(key, content)
        elif tag.lower() == "link" and "canonical" in (values.get("rel") or "").lower().split():
            self.canonical = values.get("href")
        elif tag.lower() == "title":
            self._in_title = True
        elif tag.lower() == "script" and "ld+json" in (values.get("type") or "").lower():
            self._in_jsonld = True
            self._current_jsonld = []

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False
        elif tag.lower() == "script" and self._in_jsonld:
            self.jsonld_parts.append("".join(self._current_jsonld))
            self._current_jsonld = []
            self._in_jsonld = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
        if self._in_jsonld:
            self._current_jsonld.append(data)


def _jsonld_values(raw_values: list[str]) -> tuple[dict[str, str], list[str]]:
    values: dict[str, str] = {}
    issues: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            for key, field in (
                ("articleBody", "post_text"),
                ("text", "post_text"),
                ("description", "post_text"),
                ("headline", "title"),
                ("name", "title"),
            ):
                candidate = _clean(value.get(key)) if isinstance(value.get(key), str) else None
                if candidate and field not in values:
                    values[field] = candidate
            author = value.get("author")
            if isinstance(author, dict):
                author = author.get("name")
            if isinstance(author, str) and _clean(author) and "author" not in values:
                values["author"] = _clean(author) or ""
            graph = value.get("@graph")
            if graph is not None:
                visit(graph)

    for raw in raw_values:
        try:
            visit(json.loads(raw))
        except json.JSONDecodeError:
            issues.append("malformed_json_ld")
    return values, issues


class OpenGraphAdapter:
    """Extract common metadata without making network requests or claiming platform completeness."""

    name = "generic_open_graph"
    version = "1"

    def extract(self, request: ExtractionRequest) -> ExtractionResult:
        if len(request.content) > MAX_HTML_BYTES:
            raise AdapterError(
                AdapterErrorCode.EVIDENCE_TOO_LARGE,
                f"rendered DOM exceeds the {MAX_HTML_BYTES}-byte extraction limit",
            )
        request.validate()
        parser = _MetadataParser()
        try:
            parser.feed(request.content.decode("utf-8", errors="replace"))
            parser.close()
        except (AssertionError, ValueError) as exc:
            raise AdapterError(
                AdapterErrorCode.MALFORMED_CONTENT, "rendered DOM is malformed"
            ) from exc

        jsonld, issues = _jsonld_values(parser.jsonld_parts)
        post_text = (
            parser.meta.get("og:description")
            or parser.meta.get("twitter:description")
            or parser.meta.get("description")
            or jsonld.get("post_text")
        )
        title = (
            parser.meta.get("og:title")
            or parser.meta.get("twitter:title")
            or _clean(" ".join(parser.title_parts))
            or jsonld.get("title")
        )
        author = (
            parser.meta.get("article:author")
            or parser.meta.get("author")
            or jsonld.get("author")
        )
        raw_canonical = _clean(parser.canonical)
        canonical_url: str | None = None
        if raw_canonical:
            try:
                canonical_url = validate_submitted_url(raw_canonical).submitted
            except InvalidUrlError:
                issues.append("invalid_canonical_url")

        locators = {
            "post_text": (
                "meta[property=og:description]",
                "meta[name=twitter:description]",
                "meta[name=description]",
                "jsonld:post_text",
            ),
            "title": (
                "meta[property=og:title]",
                "meta[name=twitter:title]",
                "title",
                "jsonld:title",
            ),
            "author": ("meta[property=article:author]", "meta[name=author]", "jsonld:author"),
            "canonical_url": ("link[rel=canonical]",),
        }
        selected: dict[str, str | None] = {
            "post_text": post_text,
            "title": title,
            "author": author,
            "canonical_url": canonical_url,
        }
        provenance = {
            field: ExtractionProvenance(
                artifact_id=request.artifact.artifact_id,
                artifact_sha256=request.artifact.sha256,
                locator=next(
                    (
                        locator
                        for locator in locators[field]
                        if self._has_locator(field, locator, parser, jsonld)
                    ),
                    locators[field][0],
                ),
            )
            for field, value in selected.items()
            if value is not None
        }
        status = Status.SUCCESS if post_text else Status.PARTIAL
        return ExtractionResult(
            extraction_id=str(uuid.uuid4()),
            capture_id=request.capture_id,
            source_id=request.source_id,
            normalized_url=request.normalized_url,
            extractor=self.name,
            extractor_version=self.version,
            status=status,
            post_text=post_text,
            author=author,
            title=title,
            canonical_url=canonical_url,
            provenance=provenance,
            issues=issues,
            schema_version="1",
            created_at=datetime.now(UTC).isoformat(timespec="milliseconds"),
        )

    @staticmethod
    def _has_locator(
        field: str, locator: str, parser: _MetadataParser, jsonld: dict[str, str]
    ) -> bool:
        if locator.startswith("meta["):
            key = locator.split("=", maxsplit=1)[1].split("]", maxsplit=1)[0].strip('"\'')
            tag_value = parser.meta.get(key)
            return tag_value is not None
        if locator == "title":
            return bool(_clean(" ".join(parser.title_parts)))
        if locator == "link[rel=canonical]":
            return bool(_clean(parser.canonical))
        if locator.startswith("jsonld:"):
            return field in jsonld
        return False
