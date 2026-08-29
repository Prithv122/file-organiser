# Resume Bullets — file-organiser

Form: **action → technical specifics → measured outcome.** Numbers or it doesn't go on the resume.

---

## Bullets

- Built a transactional file-management CLI (Python 3.13, Typer) with a tiered duplicate detector — size → head/tail sample → SHA-256 — benchmarked at **25–28× faster than naive full hashing** (1.2 s vs 30–33 s on a 3,000-file, 152 MB corpus), with the benchmark asserting both approaches return identical duplicate groups.
- Designed a quarantine-plus-transaction-log model so every destructive operation is reversible: each run writes a JSON transaction that `undo` replays backwards, and duplicates are moved rather than unlinked, trading deferred space reclamation for guaranteed recoverability.
- Hardened the destructive paths against the failure modes that make cleanup tools untrustworthy — content-only duplicate evidence, no silent overwrites, forced renaming on within-batch filename collisions, and a re-hash TOCTOU check immediately before deletion — covered by **168 tests at 92% line coverage** including symlinks, hard links, empty files and permission errors, run on both Windows and Linux CI.

## Which roles this supports

- [ ] Data Scientist / ML
- [ ] AI Engineer (LLM/NLP/CV)
- [x] Data Engineer
- [x] Data Analyst / Python Developer

## Keywords this project earns

Python 3.13, Typer, Rich, `hashlib`/SHA-256, `pathlib`, content-addressed deduplication, transactional rollback, idempotency, TOCTOU, dry-run/preview design, CLI design, TOML configuration, pytest, fixtures and parametrisation, coverage, ruff, GitHub Actions, cross-platform filesystem semantics (symlinks, hard links, inodes, case-insensitive paths), benchmarking.

---

### Why these three

The first bullet carries a measured number *and* the correctness guarantee behind
it, which is the part that invites a good follow-up question rather than a
dangerous one. The second names a real architectural tradeoff and states the cost
out loud — deferred space reclamation — which is far stronger than claiming a
free win. The third is about the failure modes I designed against, which is what
separates this from the hundreds of "file organiser" repos that match on filename
and have no undo.

Every claim here is answerable from `INTERVIEW.md` and reproducible from
`scripts/benchmark.py`.
