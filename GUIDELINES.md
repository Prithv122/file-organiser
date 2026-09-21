# file-organiser — A2

**Tier:** 2 (upgraded from 1 — see `../PROGRESS.md` 2026-08-29 entry) · **Category:** A — Simple
Python · **Wave:** 1

Root rules in `../GUIDELINES.md` apply. This file is project-specific only — keep it under 40 lines.

## What this is

A safe file-management engine: `scan` (read-only report), `organise` (rules-based, by type
and/or date), `dedupe` (tiered SHA-256-verified duplicate detection), all flowing through a
transaction log that `undo` can replay. CLI-first; a GUI is an explicit non-goal for this build.

## Stack

Python 3.13+, `uv`, `typer`, stdlib `hashlib`/`pathlib`/`shutil`/`json`, `pytest`, `ruff`. No
runtime network or DB dependencies.

## Acceptance criteria

- [x] `scan` reports counts by category, total size, duplicate-candidate stats — read-only
- [x] `organise --by-type` / `--by-date` / both, driven by a configurable rules file
- [x] `dedupe` uses size → partial hash → SHA-256 tiering, never filename/size alone
- [x] Every modifying command defaults to dry-run; `--apply` required to act; never silent-overwrite
- [x] Every modifying command writes a transaction log; `undo <id>` restores it
- [x] Protected-path guardrail blocks OS/`.git`/venv/`node_modules` dirs unless overridden
- [x] Tests cover symlinks, hard links, empty files, large files, conflicts — never touch real files
- [x] `uv run pytest` green, `ruff check .` / `ruff format --check .` clean
- [ ] Ship gate passes

## Project-specific notes

- Full revised architecture and staged build plan live in `../PROGRESS.md` (2026-08-29 entry) —
  read that before continuing, not just this file.
- Package `file_organiser`; CLI/repo name stays `file-organiser` (not `filetool`) for consistency
  with the already-created GitHub repo.
- **Local pytest needs `--basetemp`** pointing somewhere writable; this machine's sandbox blocks
  the shared `%TEMP%`. Deliberately not in `pyproject.toml` — it's machine-specific and would
  break CI and clean clones.
- `execute.py` is the only module that writes to disk. Keep it that way: `scanning` and
  `organise` staying pure is what makes the dry-run preview trustworthy.
- Deferred, with reasoning in `NOTES.md` → Open questions: GUI, EXIF dates, write-ahead
  journaling, interactive keeper selection.
