from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from usie.collector import Collector, read_urls
from usie.config import Settings
from usie.db import SqliteRepository
from usie.db.migrations import MIGRATIONS, current_version
from usie.evidence import verify_artifact
from usie.logging_utils import configure_logging
from usie.ocif_export import ExportError, export_envelopes, write_export
from usie.routing import route_url
from usie.urlsafety import InvalidUrlError, validate_submitted_url


def _print(data: Any) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="usie", description="Evidence-first social URL collector")
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("route", help="Validate and classify a URL without network access")
    r.add_argument("url")
    c = sub.add_parser("collect", help="Collect URLs from a text manifest (one per line)")
    c.add_argument("manifest")
    c.add_argument("--storage-state", help="Playwright storage-state file (secret)")
    sub.add_parser("report", help="Capture status counts by platform")
    sub.add_parser("verify", help="Re-hash every stored artifact and report integrity problems")
    sub.add_parser("db-status", help="Schema version and pending migrations")
    sub.add_parser("recover", help="Mark crash-interrupted runs/jobs/captures as failed")
    e = sub.add_parser(
        "export-ocif", help="Export finalized, verified evidence to an OCIF JSON bundle"
    )
    e.add_argument("--capture-id", help="Export one finalized capture only")
    e.add_argument("--output", default="-", help="Output JSON path (new file only) or - for stdout")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging()
    settings = Settings.from_env()
    if args.command == "route":
        try:
            valid = validate_submitted_url(args.url, settings.max_url_length)
            route = route_url(valid.submitted)
        except (InvalidUrlError, ValueError) as exc:
            print(f"rejected: {exc}", file=sys.stderr)
            return 2
        _print(
            {
                "platform": route.platform.value,
                "hostname": route.hostname,
                "supported_host": route.supported_host,
                "normalized_url": valid.normalized,
            }
        )
        return 0
    if args.command == "export-ocif":
        try:
            envelopes = export_envelopes(
                settings.db_path,
                settings.evidence_root,
                capture_id=args.capture_id,
            )
            if args.output == "-":
                _print(envelopes)
            else:
                write_export(Path(args.output), envelopes)
                print(f"exported {len(envelopes)} capture(s) to {args.output}", file=sys.stderr)
            return 0
        except (ExportError, OSError) as exc:
            print(f"export rejected: {exc}", file=sys.stderr)
            return 2
    repo = SqliteRepository(settings.db_path)
    try:
        if args.command == "collect":
            if args.storage_state:
                from dataclasses import replace

                settings = replace(settings, storage_state=Path(args.storage_state).expanduser())
            from usie.capture.browser import PlaywrightCapturer

            async def go() -> dict[str, Any]:
                async with PlaywrightCapturer() as capturer:
                    return await Collector(settings, repo, capturer).run(read_urls(args.manifest))

            try:
                _print(asyncio.run(go()))
            except KeyboardInterrupt:
                print("interrupted; unfinished jobs marked cancelled", file=sys.stderr)
                return 130
        elif args.command == "report":
            _print(repo.status_report())
        elif args.command == "verify":
            problems = []
            rows = repo.artifacts_for_verification()
            for row in rows:
                issue = verify_artifact(
                    settings.evidence_root, row["relative_path"], row["sha256"], row["byte_size"]
                )
                if issue:
                    problems.append(
                        {
                            "artifact_id": row["artifact_id"],
                            "path": row["relative_path"],
                            "problem": issue,
                        }
                    )
            _print({"artifacts_checked": len(rows), "problems": problems})
            return 1 if problems else 0
        elif args.command == "db-status":
            version = current_version(repo._conn)  # noqa: SLF001
            _print({"schema_version": version, "latest": MIGRATIONS[-1].version})
        elif args.command == "recover":
            _print(repo.recover_interrupted())
    finally:
        repo.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
