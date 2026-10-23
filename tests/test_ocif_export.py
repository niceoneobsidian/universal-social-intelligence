from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast
from unittest.mock import Mock

from usie.integrations.ocif import OCIFExportError, export_capture


class OCIFExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = b"verified capture evidence"
        self.digest = hashlib.sha256(self.data).hexdigest()
        (self.root / "page.html").write_bytes(self.data)
        self.repo = Mock()
        self.repo.query.side_effect = self.query

    def tearDown(self) -> None:
        self.temp.cleanup()

    def provenance(self, *fields: str) -> str:
        reference = {
            "artifact_id": "artifact-1",
            "artifact_sha256": self.digest,
            "locator": "meta[property=og:description]",
        }
        return json.dumps({field: reference for field in fields})

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        if "FROM captures c JOIN sources s" in sql:
            return [
                {
                    "capture_id": "capture-1",
                    "source_id": "source-1",
                    "status": "success",
                    "status_reason": None,
                    "error_code": None,
                    "captured_at": "2026-10-22T10:00:00+00:00",
                    "finished_at": "2026-10-22T10:00:01+00:00",
                    "submitted_url": "https://example.org/post/1",
                    "normalized_url": "https://example.org/post/1",
                    "platform": "UNIVERSAL",
                    "issues_json": "[]",
                }
            ]
        if "FROM artifacts" in sql:
            return [
                {
                    "artifact_id": "artifact-1",
                    "kind": "rendered_dom_html",
                    "relative_path": "page.html",
                    "sha256": self.digest,
                    "byte_size": len(self.data),
                    "media_type": "text/html",
                }
            ]
        if "FROM extraction_results" in sql:
            return [
                {
                    "extraction_id": "extract-1",
                    "extractor": "generic_metadata",
                    "extractor_version": "0.1.0",
                    "status": "success",
                    "post_text": "Example evidence text",
                    "author": None,
                    "title": "Example",
                    "canonical_url": "https://example.org/post/1",
                    "metadata_json": "{}",
                    "issues_json": "[]",
                    "schema_version": "1",
                    "error_code": None,
                    "provenance_json": self.provenance(
                        "post_text", "title", "canonical_url"
                    ),
                }
            ]
        return []

    def mutate_extraction(self, **changes: Any) -> None:
        original = self.repo.query.side_effect

        def changed_query(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
            rows = cast(list[dict[str, Any]], original(sql, params))
            if "FROM extraction_results" in sql:
                rows[0].update(changes)
            return rows

        self.repo.query.side_effect = changed_query

    def test_export_includes_verified_hash_provenance(self) -> None:
        result = export_capture(self.repo, self.root, "capture-1", producer_revision="abc123")
        self.assertEqual(result["contract_version"], "0.1.0")
        self.assertEqual(
            result["extraction"]["provenance"]["post_text"]["artifact_sha256"],
            self.digest,
        )
        self.assertEqual(result["artifacts"][0]["sha256"], self.digest)

    def test_missing_or_tampered_artifact_is_rejected(self) -> None:
        (self.root / "page.html").write_bytes(b"tampered")
        with self.assertRaises(OCIFExportError):
            export_capture(self.repo, self.root, "capture-1", producer_revision="abc123")

    def test_missing_artifact_is_rejected(self) -> None:
        (self.root / "page.html").unlink()
        with self.assertRaises(OCIFExportError):
            export_capture(self.repo, self.root, "capture-1", producer_revision="abc123")

    def test_path_traversal_is_rejected(self) -> None:
        original = self.repo.query.side_effect

        def traversal_query(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
            rows = cast(list[dict[str, Any]], original(sql, params))
            if "FROM artifacts" in sql:
                rows[0]["relative_path"] = "../outside.html"
            return rows

        self.repo.query.side_effect = traversal_query
        with self.assertRaisesRegex(OCIFExportError, "path escapes"):
            export_capture(self.repo, self.root, "capture-1", producer_revision="abc123")

    def test_terminal_capture_statuses_are_preserved(self) -> None:
        original = self.query
        for status in ("partial", "blocked", "unavailable", "failed", "cancelled"):
            def terminal_query(
                sql: str, params: tuple[Any, ...] = (), *, capture_status: str = status
            ) -> list[dict[str, Any]]:
                rows = cast(list[dict[str, Any]], original(sql, params))
                if "FROM captures c JOIN sources s" in sql:
                    rows[0]["status"] = capture_status
                if "FROM extraction_results" in sql:
                    return []
                return rows

            self.repo.query.side_effect = terminal_query
            result = export_capture(
                self.repo, self.root, "capture-1", producer_revision="abc123"
            )
            self.assertEqual(result["capture_status"], status)
            self.assertIsNone(result["extraction"])

    def test_non_terminal_capture_is_rejected(self) -> None:
        original = self.repo.query.side_effect

        def queued_query(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
            rows = cast(list[dict[str, Any]], original(sql, params))
            if "FROM captures c JOIN sources s" in sql:
                rows[0]["status"] = "running"
            return rows

        self.repo.query.side_effect = queued_query
        with self.assertRaises(OCIFExportError):
            export_capture(self.repo, self.root, "capture-1", producer_revision="abc123")

    def test_populated_field_without_provenance_is_rejected(self) -> None:
        self.mutate_extraction(provenance_json=self.provenance("post_text"))
        with self.assertRaisesRegex(OCIFExportError, "title is missing provenance"):
            export_capture(self.repo, self.root, "capture-1", producer_revision="abc123")

    def test_success_requires_post_text_provenance(self) -> None:
        self.mutate_extraction(provenance_json=self.provenance("title", "canonical_url"))
        with self.assertRaisesRegex(OCIFExportError, "post_text is missing provenance"):
            export_capture(self.repo, self.root, "capture-1", producer_revision="abc123")

    def test_unknown_extraction_status_is_rejected(self) -> None:
        self.mutate_extraction(status="complete")
        with self.assertRaisesRegex(OCIFExportError, "unsupported status"):
            export_capture(self.repo, self.root, "capture-1", producer_revision="abc123")

    def test_success_with_error_code_is_rejected(self) -> None:
        self.mutate_extraction(error_code="extract_failed")
        with self.assertRaisesRegex(OCIFExportError, "with an error"):
            export_capture(self.repo, self.root, "capture-1", producer_revision="abc123")

    def test_non_list_extraction_issues_are_rejected(self) -> None:
        self.mutate_extraction(issues_json='{"not":"a list"}')
        with self.assertRaisesRegex(OCIFExportError, "issues must be a list"):
            export_capture(self.repo, self.root, "capture-1", producer_revision="abc123")


if __name__ == "__main__":
    unittest.main()
