# file-organiser — A2

**Tier:** 1 · **Category:** A — Simple Python · **Wave:** 1

Root rules in `../GUIDELINES.md` apply. This file is project-specific only — keep it under 40 lines.

## What this is

File organiser + duplicate finder CLI (hashing, Typer, `--dry-run`). Sorts files in a directory
by type/extension and/or modified date, and finds exact duplicates by content hash (SHA-256),
with a safe dry-run mode for every destructive operation.

## Stack

Python 3.13+, `uv`, `typer`, stdlib `hashlib`/`pathlib`/`shutil`, `pytest`, `ruff`. No runtime
network or DB dependencies.

## Acceptance criteria

- [ ] `file-organiser organise <dir>` sorts files into subfolders by type (and optionally by date)
- [ ] `file-organiser dedupe <dir>` finds duplicate files by content hash, not just name/size
- [ ] Every destructive operation (move/delete) supports `--dry-run` and defaults to safe behavior
- [ ] `uv run pytest` green, `ruff check .` / `ruff format --check .` clean
- [ ] Ship gate passes (`/ship`)

## Project-specific notes

- Real `hatchling` build backend + `[project.scripts]` console entry point (`file-organiser`),
  following the packaging fix from A1 — not a `uv` virtual project.
- Package name is `file_organiser` (underscore); CLI/repo name is `file-organiser` (hyphen).
