"""Content-addressed-by-hash, never-overwrite evidence storage.

Layout: <root>/captures/<id[:2]>/<capture_id>/<fixed filename>. Filenames come from a fixed
allow-list, never from URLs. Writes are atomic, no-overwrite, fsynced, and re-hashed from disk.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from usie.models import ArtifactKind

FILENAMES: dict[ArtifactKind, str] = {
    ArtifactKind.RENDERED_DOM_HTML: "rendered_dom.html",
    ArtifactKind.RESPONSE_BODY: "response_body.bin",
    ArtifactKind.SCREENSHOT_VIEWPORT: "screenshot_viewport.png",
    ArtifactKind.SCREENSHOT_FULL_PAGE: "screenshot_full_page.png",
    ArtifactKind.NAVIGATION_RECORD: "navigation.json",
    ArtifactKind.CAPTURE_MANIFEST: "manifest.json",
}


class EvidenceError(RuntimeError):
    pass


@dataclass(frozen=True)
class StoredArtifact:
    relative_path: str
    sha256: str
    byte_size: int


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def new_capture_id() -> str:
    return str(uuid.uuid4())


def capture_dir(root: Path, capture_id: str) -> Path:
    try:
        canonical = str(uuid.UUID(capture_id))
    except ValueError as exc:
        raise EvidenceError("capture_id must be a UUID") from exc
    if canonical != capture_id:
        raise EvidenceError("capture_id must be a canonical lowercase UUID")
    target = (root / "captures" / capture_id[:2] / capture_id).resolve()
    if not target.is_relative_to(root.resolve()):
        raise EvidenceError("path escapes the evidence root")
    return target


def resolve_relative(root: Path, relative_path: str) -> Path:
    """Resolve a stored relative path, refusing traversal."""
    candidate = (root / relative_path).resolve()
    if not candidate.is_relative_to(root.resolve()):
        raise EvidenceError("path escapes the evidence root")
    return candidate


def store_artifact(
    root: Path, capture_id: str, kind: ArtifactKind, data: bytes, max_bytes: int
) -> StoredArtifact:
    if len(data) > max_bytes:
        raise EvidenceError(f"artifact {kind.value} is {len(data)} bytes, limit is {max_bytes}")
    directory = capture_dir(root, capture_id)
    destination = directory / FILENAMES[kind]
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    expected = sha256_bytes(data)
    fd, temporary = tempfile.mkstemp(prefix=".tmp-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        try:
            os.link(temporary, destination)  # atomic and fails if destination exists
        except FileExistsError as exc:
            raise EvidenceError(
                f"refusing to overwrite existing artifact {destination.name}"
            ) from exc
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    actual = sha256_file(destination)
    if actual != expected:
        raise EvidenceError(f"hash mismatch after write for {destination.name}")
    return StoredArtifact(destination.relative_to(root.resolve()).as_posix(), expected, len(data))


def json_bytes(data: object) -> bytes:
    return (json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode()


def verify_artifact(root: Path, relative_path: str, sha256: str, byte_size: int) -> str | None:
    """Return None if intact, else a short problem description."""
    try:
        path = resolve_relative(root, relative_path)
    except EvidenceError as exc:
        return str(exc)
    if not path.is_file():
        return "missing"
    if path.stat().st_size != byte_size:
        return "size_mismatch"
    if sha256_file(path) != sha256:
        return "hash_mismatch"
    return None
