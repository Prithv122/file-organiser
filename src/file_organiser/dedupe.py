"""Duplicate detection and keeper recommendation.

Duplicates are decided on content and nothing else. Two files with the same name
are not duplicates; two files with the same size are not duplicates; only two
files with the same SHA-256 digest are duplicates.

Choosing *which* copy to keep is a separate judgement, and a heuristic one. It is
deliberately deterministic so the same tree always yields the same
recommendation, and it is always overridable by the caller.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from file_organiser.hashing import full_digest, partial_digest
from file_organiser.scanning import FileEntry, ScanError, ScanResult

# Filename shapes that mark a file as a copy of something else. Deliberately
# narrow: "IMG_1234.jpg" must not be mistaken for a numbered copy.
_COPY_MARKERS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\s*\(\d+\)$"),
    re.compile(r"\s*-\s*copy(\s*\(\d+\))?$", re.IGNORECASE),
    re.compile(r"\s+copy$", re.IGNORECASE),
    re.compile(r"^copy\s+of\s+", re.IGNORECASE),
)

# Folders things land in rather than folders things are filed in. A copy sitting
# in one of these is a weaker claim to being the canonical original.
_TRANSIENT_DIRS: frozenset[str] = frozenset(
    {"downloads", "download", "desktop", "temp", "tmp", "unsorted", "inbox", "new folder"}
)


def has_copy_marker(stem: str) -> bool:
    """True when a filename stem looks like an automatically-named copy."""
    return any(pattern.search(stem) for pattern in _COPY_MARKERS)


def in_transient_dir(path: Path) -> bool:
    return any(part.lower() in _TRANSIENT_DIRS for part in path.parent.parts)


def keeper_sort_key(entry: FileEntry) -> tuple[bool, bool, int, float, str]:
    """Rank candidates; the lowest sorts first and becomes the recommended keeper.

    In priority order: a name with no copy marker beats one with; a file outside
    a transient folder beats one inside; a shorter name beats a longer one; an
    older file beats a newer one; and finally the path itself breaks any
    remaining tie so the result never depends on walk order.
    """
    return (
        has_copy_marker(entry.path.stem),
        in_transient_dir(entry.path),
        len(entry.path.name),
        entry.mtime,
        str(entry.path).lower(),
    )


@dataclass(frozen=True)
class DuplicateGroup:
    """A set of files proven byte-identical by SHA-256."""

    digest: str
    keeper: FileEntry
    duplicates: tuple[FileEntry, ...]

    @property
    def size(self) -> int:
        return self.keeper.size

    @property
    def file_count(self) -> int:
        return 1 + len(self.duplicates)

    @property
    def reclaimable(self) -> int:
        """Bytes freed by deleting every copy but the keeper."""
        return self.size * len(self.duplicates)

    def with_keeper(self, chosen: FileEntry) -> DuplicateGroup:
        """Return this group with a caller-chosen keeper instead of the default."""
        members = [self.keeper, *self.duplicates]
        if chosen not in members:
            raise ValueError(f"{chosen.path} is not a member of this duplicate group")
        return DuplicateGroup(
            digest=self.digest,
            keeper=chosen,
            duplicates=tuple(m for m in members if m != chosen),
        )


@dataclass
class DedupeStats:
    """How much work the tiered pipeline actually did."""

    size_candidates: int = 0
    partial_hashed: int = 0
    fully_hashed: int = 0

    @property
    def full_hashes_avoided(self) -> int:
        return max(self.size_candidates - self.fully_hashed, 0)


@dataclass
class DedupeReport:
    groups: list[DuplicateGroup] = field(default_factory=list)
    errors: list[ScanError] = field(default_factory=list)
    stats: DedupeStats = field(default_factory=DedupeStats)

    @property
    def duplicate_count(self) -> int:
        """Redundant copies found, not counting the keeper of each group."""
        return sum(len(group.duplicates) for group in self.groups)

    @property
    def reclaimable(self) -> int:
        """Verified bytes recoverable. Unlike the scan estimate, this is exact."""
        return sum(group.reclaimable for group in self.groups)

    def sorted_by_impact(self) -> list[DuplicateGroup]:
        return sorted(self.groups, key=lambda g: (-g.reclaimable, str(g.keeper.path).lower()))


def find_duplicates(result: ScanResult) -> DedupeReport:
    """Resolve a scan's size candidates into content-verified duplicate groups."""
    report = DedupeReport()

    for size_group in result.size_groups():
        report.stats.size_candidates += len(size_group)

        partial_buckets = _bucket_by(size_group, partial_digest, report)
        report.stats.partial_hashed += sum(len(b) for b in partial_buckets.values())

        for partial_bucket in partial_buckets.values():
            if len(partial_bucket) < 2:
                continue

            full_buckets = _bucket_by(partial_bucket, full_digest, report)
            report.stats.fully_hashed += sum(len(b) for b in full_buckets.values())

            for digest, bucket in full_buckets.items():
                if len(bucket) < 2:
                    continue
                ordered = sorted(bucket, key=keeper_sort_key)
                report.groups.append(
                    DuplicateGroup(
                        digest=digest,
                        keeper=ordered[0],
                        duplicates=tuple(ordered[1:]),
                    )
                )

    return report


def _bucket_by(
    entries: list[FileEntry],
    digest_fn: Callable[[Path], str],
    report: DedupeReport,
) -> dict[str, list[FileEntry]]:
    """Group entries by a digest function, recording files that could not be read."""
    buckets: dict[str, list[FileEntry]] = defaultdict(list)
    for entry in entries:
        try:
            digest = digest_fn(entry.path)
        except OSError as exc:
            report.errors.append(ScanError(path=entry.path, message=str(exc)))
            continue
        buckets[digest].append(entry)
    return buckets
