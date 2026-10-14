# Unified Workspace Standard

This repository participates in the Niceone engineering workspace standard. The current developer target is CPython 3.15.0, while package compatibility and production promotion remain evidence-gated.

- Keep this repository's dependency graph separate from OIS core.
- Use the same pinned `uv` bootstrap and Python 3.15 compatibility gate as OIS.
- Preserve the package's declared Python compatibility floor until tests prove a deliberate change is safe.
- Keep Ruff, pytest, pytest-asyncio, and mypy versions aligned with the canonical standard through explicit constraints and a lockfile.
- Generate and commit `uv.lock` after dependency resolution succeeds on Python 3.15; until then, do not claim deterministic lockfile parity.
- Do not promote Python 3.15 to production until the compatibility workflow, browser tests, and deployment environment pass.

See the OIS canonical policy at `https://github.com/niceoneobsidian/niceone-obsidian/blob/main/docs/engineering/UNIFIED_WORKSPACE_STANDARD.md`.
