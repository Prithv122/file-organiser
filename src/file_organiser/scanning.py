"""Read-only filesystem walking and categorisation.

Nothing in this module mutates the filesystem. Every other command builds on the
:class:`ScanResult` it produces, so the walk is the one place that has to get
symlinks, hard links and permission errors right.
"""

from __future__ import annotations

import os
import stat
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from file_organiser.categories import Ruleset
from file_organiser.paths import ExclusionMatcher


@dataclass(frozen=True, slots=True)
class FileEntry:
    """One regular file found during a scan."""

    path: Path
    size: int
    mtime: float
    category: str
    is_symlink: bool = False
    # (st_dev, st_ino), populated only when the file has more than one hard link.
    link_key: tuple[int, int] | None = None

    @property
    def is_hard_linked(self) -> bool:
        return self.link_key is not None


@dataclass(frozen=True, slots=True)
class ScanError:
    """A path that could not be read, and why."""

    path: Path
    message: str


@dataclass
class ScanResult:
    """Everything a read-only pass over a directory tree learned."""

    root: Path
    entries: list[FileEntry] = field(default_factory=list)
    errors: list[ScanError] = field(default_factory=list)
    skipped: int = 0

    @property
    def file_count(self) -> int:
        return len(self.entries)

    @property
    def total_size(self) -> int:
        return sum(entry.size for entry in self.entries)

    @property
    def by_category(self) -> Counter[str]:
        return Counter(entry.category for entry in self.entries)

    @property
    def size_by_category(self) -> dict[str, int]:
        totals: dict[str, int] = defaultdict(int)
        for entry in self.entries:
            totals[entry.category] += entry.size
        return dict(totals)

    def dedupe_candidates(self) -> list[FileEntry]:
        """Entries eligible to be considered for duplicate detection.

        Excludes symlinks (deleting one reclaims nothing and may break a target)
        and empty files (every empty file is byte-identical to every other, which
        is true but useless, and deleting them is rarely what anyone wants).
        """
        return [e for e in self.entries if not e.is_symlink and e.size > 0]

    def size_groups(self) -> list[list[FileEntry]]:
        """Candidate groups sharing a byte size -- the cheap first-pass filter.

        Hard links to the same inode are collapsed to a single representative,
        because they are one file on disk and deleting one reclaims nothing.
        """
        buckets: dict[int, list[FileEntry]] = defaultdict(list)
        for entry in self.dedupe_candidates():
            buckets[entry.size].append(entry)

        groups: list[list[FileEntry]] = []
        for bucket in buckets.values():
            collapsed: list[FileEntry] = []
            seen_links: set[tuple[int, int]] = set()
            for entry in bucket:
                if entry.link_key is not None:
                    if entry.link_key in seen_links:
                        continue
                    seen_links.add(entry.link_key)
                collapsed.append(entry)
            if len(collapsed) > 1:
                groups.append(collapsed)
        return groups

    @property
    def duplicate_candidate_count(self) -> int:
        """Files that *might* be duplicates, on size alone. Not yet verified."""
        return sum(len(group) - 1 for group in self.size_groups())

    @property
    def potential_reclaimable(self) -> int:
        """Upper bound on bytes recoverable, assuming every size match is real.

        This is deliberately an over-estimate. Only ``dedupe`` reports a verified
        figure, because only ``dedupe`` hashes the contents.
        """
        return sum(group[0].size * (len(group) - 1) for group in self.size_groups())


def scan(
    root: Path | str,
    *,
    ruleset: Ruleset | None = None,
    exclude: tuple[str, ...] | list[str] | None = None,
    use_default_excludes: bool = True,
) -> ScanResult:
    """Walk ``root`` and describe what is there. Never touches the filesystem."""
    root = Path(root)
    ruleset = ruleset or Ruleset.default()
    excluder = ExclusionMatcher.build(exclude, use_defaults=use_default_excludes)
    result = ScanResult(root=root)

    def on_error(exc: OSError) -> None:
        result.errors.append(ScanError(path=Path(exc.filename or root), message=str(exc)))

    walker = os.walk(root, topdown=True, onerror=on_error, followlinks=False)
    for dirpath, dirnames, filenames in walker:
        current = Path(dirpath)

        kept: list[str] = []
        for name in dirnames:
            relative = _relative_posix(current / name, root)
            if excluder.matches(name, relative):
                result.skipped += 1
            else:
                kept.append(name)
        dirnames[:] = kept

        for name in filenames:
            file_path = current / name
            relative = _relative_posix(file_path, root)
            if excluder.matches(name, relative):
                result.skipped += 1
                continue

            try:
                info = file_path.lstat()
            except OSError as exc:
                result.errors.append(ScanError(path=file_path, message=str(exc)))
                continue

            is_symlink = stat.S_ISLNK(info.st_mode)
            if not is_symlink and not stat.S_ISREG(info.st_mode):
                # Sockets, FIFOs, device nodes: nothing sensible to do with them.
                result.skipped += 1
                continue

            result.entries.append(
                FileEntry(
                    path=file_path,
                    size=info.st_size,
                    mtime=info.st_mtime,
                    category=ruleset.category_for(file_path),
                    is_symlink=is_symlink,
                    link_key=_link_key(info),
                )
            )

    return result


def _relative_posix(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:  # pragma: no cover - only if os.walk escapes the root
        return path.name


def _link_key(info: os.stat_result) -> tuple[int, int] | None:
    """Identify a file by inode, but only when it actually has multiple links."""
    if getattr(info, "st_nlink", 1) <= 1:
        return None
    dev, ino = info.st_dev, info.st_ino
    if not dev or not ino:
        return None
    return (dev, ino)
