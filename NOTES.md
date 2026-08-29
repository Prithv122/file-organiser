# Build Notes — file-organiser

Working notes: what broke, what was tried, why X over Y.
Not for recruiters — for me, six months from now, in an interview.

---

## Log

### 2026-08-29 — scope change before writing any code

Started as the catalogue's Tier 1 "organise + dedupe CLI, one session". Re-scoped
to a Tier 2 file-management engine before implementation: rules config, tiered
hashing, transaction log, undo, guardrails, reporting. The catalogue entry and
`PROGRESS.md` were updated first so a later session doesn't work from a stale
brief.

Deliberately deferred the GUI. The engine has to be right before anything sits on
top of it, and a GUI over an unproven engine is just a faster way to lose files.

### 2026-08-29 — the delete/undo conflict

The central design problem. The requirement was "every modifying operation must
be undoable" *and* "dedupe reclaims space". Those are contradictory: an
`unlink()`ed file cannot be restored.

Options considered:

| Approach | Undoable? | Frees space? | Verdict |
|---|---|---|---|
| `unlink()` the duplicate | no | immediately | Fails the undo requirement outright |
| Copy to backup, then unlink | yes | no (worse — doubles I/O) | Pointless: the copy costs a full read+write |
| Move to quarantine, purge later | yes | on purge | **Chosen** |
| OS recycle bin (`send2trash`) | partly | on empty | Adds a dependency; behaviour differs per platform; no per-operation grouping, so undo can't target one run |

Went with quarantine + a separate `purge`. The tradeoff is real and I decided not
to hide it: `dedupe --apply` prints "this space is not free yet". A tool that
claimed to have freed 6.8 GB while the bytes were still on disk would be lying,
and that lie is exactly the kind of thing that destroys trust in this category of
tool.

Quarantine lives at `<root>/.file-organiser/quarantine/` rather than somewhere
under `~`, specifically so the move is a same-volume rename. A quarantine in the
home directory would turn every deletion on another drive into a full copy.

### 2026-08-29 — bugs caught while building

**Home directory guard rejected `~/Downloads`.** First version of
`guard_target` treated the home directory like a system root, refusing the target
*and everything inside it*. That blocked the single most common legitimate use
of the tool. Split the concept in two: `protected_roots()` (system dirs — inside
is also forbidden) and `exact_only_protected()` (home — the folder itself is
refused, children are fine). Caught by writing the test for the motivating case
rather than only for the thing I was guarding against.

**macOS `/tmp` would have looked like a system directory.** Had `/private` in the
POSIX protected list. On macOS `/tmp` resolves to `/private/tmp`, so every test
using a temp directory would have been refused as a protected location. Removed
it; `/System` and `/Library` already cover what matters there. CI is Ubuntu so
this would not have been caught by CI — only by someone running the suite on a
Mac.

**In-batch collisions could have silently eaten a file.** First version resolved
destination conflicts only against files already on disk. Two *different* files
named `notes.txt` in different source folders both mapped to
`documents/notes.txt`; under `--conflict skip` or `replace`, one would have been
discarded. Fixed by tracking a `claimed` set of destinations within the plan and
forcing rename for in-batch collisions regardless of policy. This is the bug I'd
most expect a naive implementation to ship with.

**Test fixture accidentally created a three-way size collision.** `holiday.jpg`
happened to be exactly 16 bytes, the same as both decoy files, so the "same size,
different content" test was asserting against a group of three rather than two.
The code was right; the fixture was sloppy. Changed the payload to a distinct
length and commented *why* the length matters, so nobody helpfully "tidies" it
back.

### 2026-08-29 — environment friction

Local `pytest` could not create `tmp_path`: the sandbox blocks writes to the
shared `%TEMP%`, and the existing `pytest-of-<user>` directory was not writable.
Worked around per-run with `--basetemp` pointing into the session scratchpad.
Deliberately did **not** put this in `pyproject.toml` — it is a property of this
machine, not of the project, and hard-coding it would break CI and any clean
clone.

---

## Rejected approaches

| Approach | Why rejected |
|---|---|
| YAML config | Needs a runtime dependency to parse a list of file extensions. `tomllib` is stdlib from 3.11. |
| `calendar.month_name` for date folders | Locale-dependent — the same folder would be `08-August` or `08-août` depending on who ran the tool. Hard-coded English instead. |
| Filename or size as duplicate evidence | Both produce false positives constantly, and a false positive here means destroyed data. Cheap tiers rule files *out* only. |
| Head-only partial hash | Files of one type routinely share a header, so a head-only sample collapses them into one bucket and forces the full read anyway. Sampling head + tail + length separates them. |
| Emoji in terminal output | Mangles on Windows consoles running a legacy code page. |
| Treating hard links as duplicates | Same content, but one inode: deleting a link reclaims nothing. Reporting that as recoverable space would be a false metric. |
| Interactive confirmation on every `--apply` | `--apply` is already the explicit opt-in; a second prompt makes scripting painful. Kept the prompt only for `purge`, which is the one irreversible command. |

## Open questions

- [ ] EXIF `DateTimeOriginal` for `--by-date` on photos. mtime is often wrong after copying between devices, but reading EXIF costs a dependency and a per-file parse.
- [ ] Is `--conflict replace` worth keeping? It quarantines the displaced file so it is safe, but rename covers nearly every real case and the extra policy is more surface area to reason about.
- [ ] Interactive per-group keeper selection for `dedupe`. The API supports it (`DuplicateGroup.with_keeper`) and it is tested, but there is no CLI path to it yet — it needs a UI, which is really the GUI question.
- [ ] Write-ahead journaling. A hard kill mid-run currently loses the transaction log for that run; only completed actions are persisted, in a `finally` block.
