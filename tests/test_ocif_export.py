from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
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

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        if "FROM captures c JOIN sources s" in sql:
            return [{
                "capture_id": "capture-1", "source_id": "source-1", "status": "success",
                "status_reason": None, "captured_at": "2026-10-22T10:00:00+00:00",
                "finished_at": "2026-10-22T10:00:01+00:00",
                "submitted_url": "https://example.org/post/1",
                "normalized_url": "https://example.org/post/1", "platform": "UNIVERSAL",
                "issues_json": "[]",
            }]
        if "FROM artifacts" in sql:
            return [{
                "artifact_id": "artifact-1", "kind": "rendered_dom_html",
                "relative_path": "page.html", "sha256": self.digest, "byte_size": len(self.data),
                "media_type": "text/html",
            }]
        if "FROM extraction_results" in sql:
            return [{
                "extraction_id": "extract-1", "extractor": "generic_metadata",
                "extractor_version": "0.1.0", "status": "success",
                "post_text": "Example evidence text", "author": None, "title": "Example",
                "canonical_url": "https://example.org/post/1", "metadata_json": "{}",
                "issues_json": "[]", "schema_version": "1",
                "provenance_json": (
                    '{"post_text":{"artifact_id":"artifact-1","artifact_sha256":"'
                    + self.digest + '","locator":"meta[property=og:description]"}}'
                ),
            }]
        return []

    def test_export_includes_verified_hash_provenance(self) -> None:
        result = export_capture(self.repo, self.root, "capture-1", producer_revision="abc123")
        self.assertEqual(result["contract_version"], "0.1.0")
        self.assertEqual(result["extraction"]["provenance"]["post_text"]["artifact_sha256"], self.digest)
        self.assertEqual(result["artifacts"][0]["sha256"], self.digest)

    def test_missing_or_tampered_artifact_is_rejected(self) -> None:
        (self.root / "page.html").write_bytes(b"tampered")
        with self.assertRaises(OCIFExportError):
            export_capture(self.repo, self.root, "capture-1", producer_revision="abc123")

    def test_non_terminal_capture_is_rejected(self) -> None:
        original = self.repo.query.side_effect

        def queued_query(sql: str, params: tuple = ()) -> list[dict]:
            rows = original(sql, params)
            if "FROM captures c JOIN sources s" in sql:
                rows[0]["status"] = "running"
            return rows

        self.repo.query.side_effect = queued_query
        with self.assertRaises(OCIFExportError):
            export_capture(self.repo, self.root, "capture-1", producer_revision="abc123")


if __name__ == "__main__":
    unittest.main()
