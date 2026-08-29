"""Benchmark the tiered duplicate pipeline against naive full hashing.

Generates a synthetic corpus, runs both approaches, and checks they find exactly
the same duplicate groups before reporting the time difference. The correctness
check is the point: an optimisation that changes the answer is not an
optimisation.

    uv run python scripts/benchmark.py
    uv run python scripts/benchmark.py --files 5000 --mb 400

Numbers quoted in the README come from this script. The corpus is synthetic:
random bytes with a planted duplicate rate, which is kinder to the partial-hash
tier than real data would be if real files shared long identical headers.
"""

from __future__ import annotations

import argparse
import random
import shutil
import tempfile
import time
from collections import defaultdict
from pathlib import Path

from file_organiser.dedupe import find_duplicates
from file_organiser.hashing import full_digest
from file_organiser.scanning import scan

EXTENSIONS = (".jpg", ".pdf", ".txt", ".mp4", ".bin", ".png", ".docx")


def build_corpus(root: Path, file_count: int, total_mb: int, duplicate_rate: float) -> dict:
    """Create a synthetic tree with a known number of planted duplicates."""
    rng = random.Random(20260829)
    average = (total_mb * 1024 * 1024) // file_count

    root.mkdir(parents=True, exist_ok=True)
    folders = [root / f"folder_{i:02d}" for i in range(10)]
    for folder in folders:
        folder.mkdir(exist_ok=True)

    originals: list[bytes] = []
    planted = 0

    for index in range(file_count):
        folder = rng.choice(folders)
        extension = rng.choice(EXTENSIONS)

        if originals and rng.random() < duplicate_rate:
            payload = rng.choice(originals)
            planted += 1
        else:
            size = max(1, int(rng.gauss(average, average / 3)))
            payload = rng.randbytes(size)
            # Keep the pool bounded so memory stays flat on large runs.
            if len(originals) < 400:
                originals.append(payload)

        (folder / f"file_{index:06d}{extension}").write_bytes(payload)

    return {"planted_duplicates": planted}


def naive_duplicates(root: Path) -> set[frozenset[Path]]:
    """The obvious implementation: fully hash every file, group by digest."""
    buckets: dict[str, list[Path]] = defaultdict(list)
    for path in root.rglob("*"):
        if path.is_file() and path.stat().st_size > 0:
            buckets[full_digest(path)].append(path)
    return {frozenset(paths) for paths in buckets.values() if len(paths) > 1}


def tiered_duplicates(root: Path) -> tuple[set[frozenset[Path]], object]:
    report = find_duplicates(scan(root))
    groups = {
        frozenset([group.keeper.path, *(d.path for d in group.duplicates)])
        for group in report.groups
    }
    return groups, report.stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files", type=int, default=3000)
    parser.add_argument("--mb", type=int, default=150)
    parser.add_argument("--duplicate-rate", type=float, default=0.15)
    args = parser.parse_args()

    workspace = Path(tempfile.mkdtemp(prefix="file-organiser-bench-"))
    corpus = workspace / "corpus"

    try:
        print(f"Building corpus: {args.files:,} files, ~{args.mb} MB ...")
        built = build_corpus(corpus, args.files, args.mb, args.duplicate_rate)

        measured = scan(corpus)
        total_bytes = measured.total_size
        print(f"  {measured.file_count:,} files, {total_bytes / 1024 / 1024:.1f} MB on disk")
        print(f"  {built['planted_duplicates']:,} duplicate copies planted\n")

        start = time.perf_counter()
        naive = naive_duplicates(corpus)
        naive_seconds = time.perf_counter() - start

        start = time.perf_counter()
        tiered, stats = tiered_duplicates(corpus)
        tiered_seconds = time.perf_counter() - start

        agree = naive == tiered
        print(f"{'Naive (hash everything)':<32} {naive_seconds:7.2f} s")
        print(f"{'Tiered (size -> partial -> full)':<32} {tiered_seconds:7.2f} s")
        if tiered_seconds > 0:
            print(f"{'Speedup':<32} {naive_seconds / tiered_seconds:7.2f}x")
        print()
        print(f"{'Duplicate groups found':<32} {len(tiered):,}")
        print(f"{'Same result as naive':<32} {'yes' if agree else 'NO - BUG'}")
        print()
        print(f"{'Files on disk':<32} {measured.file_count:,}")
        print(f"{'Size-matched candidates':<32} {stats.size_candidates:,}")
        print(f"{'Fully hashed':<32} {stats.fully_hashed:,}")
        print(f"{'Full reads avoided':<32} {stats.full_hashes_avoided:,}")

        return 0 if agree else 1
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
