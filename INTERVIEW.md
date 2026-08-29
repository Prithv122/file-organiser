# Interview Prep — file-organiser

**Five questions, five answers.** An unanswered question means this project is not shipped.

---

### Q1. Walk me through the architecture in 90 seconds.

_A:_ It's a pipeline with a hard split between deciding and doing.

A command first passes the target through `guard_target`, which refuses system
directories, drive roots, Git repository roots and dependency folders. Then
`scan` does a read-only walk producing `FileEntry` records — size, mtime,
category, and whether the file is a symlink or hard-linked.

From there the two real commands branch. `organise` calls `plan_organise`, which
is a pure function: it decides every move and touches nothing. `dedupe` calls
`find_duplicates`, which resolves same-size candidates into content-verified
groups through a tiered hash.

Both produce a plan that gets previewed. Only if the user passes `--apply` does
that plan reach `execute`, which is the only module in the package that mutates
the filesystem. Every change it makes is recorded as a JSON transaction, and
`undo` replays that transaction backwards.

The reason planning is pure and separate is that it makes the dry run
trustworthy. The preview isn't a simulation of what will happen — it is the
identical plan object the real run will execute.

### Q2. Why did you choose quarantine over deleting duplicates outright?

_A:_ Because "every operation is undoable" and "deleting frees space" are in
direct conflict, and I picked the one that protects data.

An `unlink()`ed file cannot be restored. If `dedupe` genuinely deleted, undo
would be a lie for the most destructive command in the tool. So duplicates get
*moved* into `<root>/.file-organiser/quarantine/<operation-id>/`, which is a
same-volume rename, and a separate `purge` command empties it.

The cost is that space isn't reclaimed until purge, which is awkward because
reclaiming space is the whole point of dedupe. I decided the right response was
to state it plainly rather than paper over it — `dedupe --apply` prints "this
space is not free yet. Run purge to reclaim it permanently."

I also considered `send2trash` to use the OS recycle bin. Rejected it: it adds a
dependency, behaves differently per platform, and has no notion of grouping files
by operation, so `undo <operation-id>` couldn't target a single run.

At scale I'd improve this with reflinks — on ReFS, Btrfs or XFS you can clone
before quarantining, which frees the space immediately while keeping the file
restorable.

### Q3. What's the weakest part of this, and what would break first under load?

_A:_ Memory, at around ten million files.

`ScanResult` holds every `FileEntry` in a Python list, and `size_groups()` builds
a dict of every size bucket on top of that. It's fine for a Downloads folder;
it will exhaust RAM on a corporate file share before the walk even finishes. The
fix is to stream entries into an on-disk SQLite index and do the size grouping as
a SQL `GROUP BY` instead of in memory.

The second thing to break is hashing throughput — full digests run serially on
one core and the workload is I/O-bound. A thread pool fixes it, but the pool size
is genuinely device-dependent: SSDs reward concurrency, spinning disks get slower
with it.

The one I'd call an actual correctness weakness rather than a scaling one is the
transaction log. It's a single JSON document written in a `finally` block. If the
process is hard-killed mid-run, the log for that run is lost, and the moves that
already happened become unrecoverable. A write-ahead journal — append one line
per action *before* performing it — would close that. I documented it rather than
built it because for the target use case the window is a few hundred
milliseconds, but I wouldn't ship it to a server that way.

### Q4. How do you know it works? What did you measure, and against what baseline?

_A:_ Two things, measured separately: is it correct, and is the optimisation
worth it.

For speed, `scripts/benchmark.py` builds a synthetic corpus — 3,000 files,
152 MB, seeded RNG, 15% planted duplicates — and runs both the tiered pipeline
and a naive "fully hash every file" baseline. Tiered came in at 1.2 seconds
against 30–33 seconds naive, so 25–28× across two runs.

The important part is what the benchmark asserts, not what it prints: it collects
the duplicate groups from both approaches and requires them to be *identical*,
exiting non-zero otherwise. An optimisation that changes the answer isn't an
optimisation, and for a tool that deletes files, "faster" is worthless without
"same result".

I'd also be honest about where the win comes from. The size filter does almost
all of it — 3,000 files down to 803 candidates. The partial-hash tier only
removes another 87. And that understates it, because the corpus is random bytes;
real files of the same type share long headers, which is precisely the case the
head+tail sampling is designed for.

For correctness, there are 163 tests at 92% coverage, deliberately aimed at the
cases that break naive implementations: same name/different content, same
size/different content, same content across different names and folders and
timestamps, empty files, symlinks, hard links, files bigger than one read chunk,
permission errors, and filename collisions both on disk and within a single
batch. Plus a round trip that organises a tree by type and date and asserts undo
returns it byte-for-byte.

### Q5. Your dry-run is the default and you have undo. Isn't that redundant — why build both?

_A:_ They solve different problems, and I'd argue a tool with only one of them is
incomplete.

Dry run protects you from mistakes you can predict. You look at the preview, you
see 1,900 files heading somewhere wrong, you don't run it. That only works for
errors that are visible in a preview you actually read — and nobody reads 1,900
lines.

Undo protects you from mistakes you couldn't predict. You approved the preview,
it did exactly what it said, and the result was still wrong — you pointed it at
the wrong folder, or the category rules weren't what you assumed. No amount of
previewing catches that, because the preview was accurate.

The one that's actually load-bearing is undo. Dry run is a good habit; undo is
the safety net. That's also why the delete path is quarantine-based — a dry run
on `dedupe` would tell you what's about to be deleted, but if you approve it and
you were wrong, only quarantine gets your files back.

There's a third layer people miss, which is verification at the moment of the
action. Detection and deletion are separate steps, and a file can be edited in
between. Before quarantining any duplicate, it's re-hashed against its keeper; if
it no longer matches, it's left alone and reported. That's a TOCTOU guard, and
neither dry run nor undo would have covered it.

---

## 30-second pitch

Cleanup tools have a trust problem: they delete on weak evidence like matching
filenames, they silently overwrite when two files collide, and when they get it
wrong there's no way back. I built a file organiser where every destructive path
is gated, evidence-based and reversible — duplicates are confirmed by SHA-256 and
never by name or size, nothing is written without an explicit `--apply`, and
every run writes a transaction log that `undo` can replay. Deletion moves files
to a quarantine rather than unlinking them, so undo actually works, and the tool
tells you plainly that the space isn't free until you purge. The duplicate finder
uses tiered hashing — size, then a head-and-tail sample, then full SHA-256 —
which benchmarks 25–28× faster than hashing everything, with the benchmark
asserting it finds exactly the same duplicates as the naive approach.
