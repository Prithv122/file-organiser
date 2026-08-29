"""Planning where files should go, and what to do when something is in the way.

This module decides moves; it never performs them. ``plan_organise`` turns a scan
into an :class:`OrganisePlan`, which the executor in :mod:`file_organiser.execute`
either previews or applies. Keeping planning pure is what makes a trustworthy
dry-run possible: the preview and the real run are the same plan.

The conflict rules are the important part. A destination is never silently
overwritten, and two different files are never collapsed into one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from file_organiser.scanning import FileEntry, ScanResult

# Hard-coded rather than taken from ``calendar``, whose month names follow the
# machine locale. Folder names must not depend on who is running the tool.
MONTH_NAMES: tuple[str, ...] = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


class ConflictPolicy(StrEnum):
    """What to do when a destination path is already occupied."""

    RENAME = "rename"
    SKIP = "skip"
    REPLACE = "replace"


class SkipReason(StrEnum):
    ALREADY_IN_PLACE = "already in place"
    SYMLINK = "symlink"
    CONFLICT = "destination occupied"


@dataclass(frozen=True)
class PlannedMove:
    source: Path
    destination: Path
    category: str
    size: int
    #: True when the destination name differs from the source name because
    #: something was already sitting at the intended path.
    renamed: bool = False
    #: True when an existing file at the destination will be overwritten.
    replaces_existing: bool = False


@dataclass(frozen=True)
class SkippedFile:
    path: Path
    reason: SkipReason


@dataclass
class OrganisePlan:
    """A complete, reviewable description of what an organise run would do."""

    root: Path
    by_type: bool
    by_date: bool
    policy: ConflictPolicy
    moves: list[PlannedMove] = field(default_factory=list)
    skipped: list[SkippedFile] = field(default_factory=list)

    @property
    def move_count(self) -> int:
        return len(self.moves)

    @property
    def skip_count(self) -> int:
        return len(self.skipped)

    @property
    def conflict_count(self) -> int:
        """Destinations that were already occupied, however that was resolved."""
        renamed = sum(1 for m in self.moves if m.renamed)
        replaced = sum(1 for m in self.moves if m.replaces_existing)
        skipped = sum(1 for s in self.skipped if s.reason is SkipReason.CONFLICT)
        return renamed + replaced + skipped

    @property
    def bytes_moved(self) -> int:
        return sum(m.size for m in self.moves)

    @property
    def destination_folders(self) -> list[Path]:
        return sorted({m.destination.parent for m in self.moves})

    def is_empty(self) -> bool:
        return not self.moves


def destination_for(
    entry: FileEntry,
    root: Path,
    *,
    by_type: bool,
    by_date: bool,
) -> Path:
    """The folder ``entry`` belongs in, before any conflict resolution."""
    parts: list[str] = []
    if by_type:
        parts.append(entry.category)
    if by_date:
        stamp = datetime.fromtimestamp(entry.mtime)
        parts.append(f"{stamp.year:04d}")
        parts.append(f"{stamp.month:02d}-{MONTH_NAMES[stamp.month - 1]}")
    return root.joinpath(*parts)


def plan_organise(
    result: ScanResult,
    *,
    by_type: bool = True,
    by_date: bool = False,
    policy: ConflictPolicy = ConflictPolicy.RENAME,
) -> OrganisePlan:
    """Work out every move an organise run would make.

    Two files are never allowed to land on the same path. A collision with a file
    already on disk follows ``policy``; a collision between two files *inside this
    same batch* is always resolved by renaming, whatever the policy says, because
    skipping or replacing there would silently discard one of two distinct files.
    """
    if not by_type and not by_date:
        raise ValueError("organise needs at least one of by_type or by_date")

    root = result.root
    plan = OrganisePlan(root=root, by_type=by_type, by_date=by_date, policy=policy)

    # Destinations already spoken for by earlier moves in this same plan.
    claimed: set[Path] = set()
    # Sorted so the plan is identical for the same tree regardless of walk order.
    for entry in sorted(result.entries, key=lambda e: str(e.path).lower()):
        if entry.is_symlink:
            plan.skipped.append(SkippedFile(entry.path, SkipReason.SYMLINK))
            continue

        target_dir = destination_for(entry, root, by_type=by_type, by_date=by_date)
        destination = target_dir / entry.path.name

        if destination == entry.path:
            plan.skipped.append(SkippedFile(entry.path, SkipReason.ALREADY_IN_PLACE))
            continue

        on_disk = destination.exists()
        in_batch = destination in claimed

        if not on_disk and not in_batch:
            claimed.add(destination)
            plan.moves.append(
                PlannedMove(
                    source=entry.path,
                    destination=destination,
                    category=entry.category,
                    size=entry.size,
                )
            )
            continue

        # Something is in the way.
        if in_batch or policy is ConflictPolicy.RENAME:
            resolved = _next_free_name(destination, claimed)
            claimed.add(resolved)
            plan.moves.append(
                PlannedMove(
                    source=entry.path,
                    destination=resolved,
                    category=entry.category,
                    size=entry.size,
                    renamed=True,
                )
            )
        elif policy is ConflictPolicy.SKIP:
            plan.skipped.append(SkippedFile(entry.path, SkipReason.CONFLICT))
        else:  # ConflictPolicy.REPLACE, and only against a file already on disk.
            claimed.add(destination)
            plan.moves.append(
                PlannedMove(
                    source=entry.path,
                    destination=destination,
                    category=entry.category,
                    size=entry.size,
                    replaces_existing=True,
                )
            )

    return plan


def _next_free_name(destination: Path, claimed: set[Path]) -> Path:
    """Find ``name (n).ext`` for the lowest n that is free on disk and in-batch."""
    stem, suffix, parent = destination.stem, destination.suffix, destination.parent
    counter = 1
    while True:
        candidate = parent / f"{stem} ({counter}){suffix}"
        if not candidate.exists() and candidate not in claimed:
            return candidate
        counter += 1
