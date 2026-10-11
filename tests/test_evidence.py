from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from usie.evidence import (
    EvidenceError,
    capture_dir,
    new_capture_id,
    resolve_relative,
    sha256_bytes,
    store_artifact,
    verify_artifact,
)
from usie.models import ArtifactKind

LIMIT = 1024 * 1024


def test_store_hash_and_permissions(tmp_path: Path) -> None:
    cid = new_capture_id()
    a = store_artifact(tmp_path, cid, ArtifactKind.RENDERED_DOM_HTML, b"<html>evidence", LIMIT)
    assert a.sha256 == sha256_bytes(b"<html>evidence")
    assert a.byte_size == 14
    path = tmp_path / a.relative_path
    assert path.read_bytes() == b"<html>evidence"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert not [p for p in path.parent.iterdir() if p.name.startswith(".tmp-")]


def test_never_overwrites(tmp_path: Path) -> None:
    cid = new_capture_id()
    store_artifact(tmp_path, cid, ArtifactKind.RENDERED_DOM_HTML, b"first", LIMIT)
    with pytest.raises(EvidenceError):
        store_artifact(tmp_path, cid, ArtifactKind.RENDERED_DOM_HTML, b"second", LIMIT)
    path = capture_dir(tmp_path, cid) / "rendered_dom.html"
    assert path.read_bytes() == b"first"
    assert not [p for p in path.parent.iterdir() if p.name.startswith(".tmp-")]


def test_size_limit(tmp_path: Path) -> None:
    with pytest.raises(EvidenceError):
        store_artifact(tmp_path, new_capture_id(), ArtifactKind.RESPONSE_BODY, b"x" * 11, 10)


@pytest.mark.parametrize(
    "bad", ["../../etc", "not-a-uuid", "", "A" * 36, "00000000-0000-0000-0000-00000000000G"]
)
def test_rejects_untrusted_capture_ids(tmp_path: Path, bad: str) -> None:
    with pytest.raises(EvidenceError):
        capture_dir(tmp_path, bad)


def test_rejects_uppercase_uuid(tmp_path: Path) -> None:
    with pytest.raises(EvidenceError):
        capture_dir(tmp_path, new_capture_id().upper())


def test_resolve_relative_blocks_traversal(tmp_path: Path) -> None:
    with pytest.raises(EvidenceError):
        resolve_relative(tmp_path / "root", "../outside.txt")


def test_verify_detects_tampering_and_loss(tmp_path: Path) -> None:
    cid = new_capture_id()
    a = store_artifact(tmp_path, cid, ArtifactKind.NAVIGATION_RECORD, b"{}", LIMIT)
    assert verify_artifact(tmp_path, a.relative_path, a.sha256, a.byte_size) is None
    path = tmp_path / a.relative_path
    os.chmod(path, 0o600)
    path.write_bytes(b"[]")  # same size, different content
    assert verify_artifact(tmp_path, a.relative_path, a.sha256, a.byte_size) == "hash_mismatch"
    path.write_bytes(b"[] ")
    assert verify_artifact(tmp_path, a.relative_path, a.sha256, a.byte_size) == "size_mismatch"
    path.unlink()
    assert verify_artifact(tmp_path, a.relative_path, a.sha256, a.byte_size) == "missing"
    assert verify_artifact(tmp_path, "../x", a.sha256, 1) is not None


def test_post_write_hash_verification_fires(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import usie.evidence as ev

    monkeypatch.setattr(ev, "sha256_file", lambda _p: "0" * 64)  # simulate silent disk corruption
    with pytest.raises(EvidenceError, match="hash mismatch"):
        store_artifact(tmp_path, new_capture_id(), ArtifactKind.NAVIGATION_RECORD, b"{}", LIMIT)
