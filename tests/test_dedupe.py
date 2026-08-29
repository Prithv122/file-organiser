from __future__ import annotations

import os
from pathlib import Path

import pytest

from conftest import write
from file_organiser.dedupe import (
    find_duplicates,
    has_copy_marker,
    in_transient_dir,
)
from file_organiser.scanning import scan


def _groups_by_name(report) -> set[frozenset[str]]:
    return {
        frozenset([g.keeper.path.name, *(d.path.name for d in g.duplicates)]) for g in report.groups
    }


def test_finds_the_true_duplicate_pair(messy: Path) -> None:
    report = find_duplicates(scan(messy))
    assert _groups_by_name(report) == {frozenset({"report.pdf", "report (1).pdf"})}


def test_same_size_different_content_is_not_a_duplicate(messy: Path) -> None:
    """The decoys share a byte count. Only content may decide."""
    report = find_duplicates(scan(messy))
    flagged = {p for group in _groups_by_name(report) for p in group}
    assert "decoy_a.bin" not in flagged
    assert "decoy_b.bin" not in flagged


def test_same_name_different_content_is_not_a_duplicate(tmp_path: Path) -> None:
    write(tmp_path / "one" / "notes.txt", b"AAAA")
    write(tmp_path / "two" / "notes.txt", b"BBBB")
    assert find_duplicates(scan(tmp_path)).groups == []


def test_duplicate_detected_across_different_names_and_folders(tmp_path: Path) -> None:
    write(tmp_path / "alpha" / "original.dat", b"SHARED-PAYLOAD")
    write(tmp_path / "beta" / "totally-different-name.dat", b"SHARED-PAYLOAD")

    report = find_duplicates(scan(tmp_path))
    assert len(report.groups) == 1
    assert report.groups[0].file_count == 2


def test_duplicate_detected_despite_different_timestamps(tmp_path: Path) -> None:
    a = write(tmp_path / "a.dat", b"SHARED")
    b = write(tmp_path / "b.dat", b"SHARED")
    os.utime(a, (1_600_000_000, 1_600_000_000))
    os.utime(b, (1_700_000_000, 1_700_000_000))

    assert len(find_duplicates(scan(tmp_path)).groups) == 1


def test_empty_files_are_never_grouped(tmp_path: Path) -> None:
    write(tmp_path / "a.log", b"")
    write(tmp_path / "b.log", b"")
    assert find_duplicates(scan(tmp_path)).groups == []


def test_reclaimable_counts_only_the_redundant_copies(tmp_path: Path) -> None:
    payload = b"X" * 500
    for name in ("a.dat", "b.dat", "c.dat"):
        write(tmp_path / name, payload)

    report = find_duplicates(scan(tmp_path))
    assert len(report.groups) == 1
    assert report.groups[0].file_count == 3
    assert report.duplicate_count == 2
    assert report.reclaimable == 1000  # two redundant copies, not three


def test_detection_never_mutates_the_tree(messy: Path) -> None:
    before = {p: p.read_bytes() for p in messy.rglob("*") if p.is_file()}
    find_duplicates(scan(messy))
    after = {p: p.read_bytes() for p in messy.rglob("*") if p.is_file()}
    assert before == after


def test_hard_links_are_not_reported_as_duplicates(
    tmp_path: Path, supports_hardlinks: bool
) -> None:
    if not supports_hardlinks:
        pytest.skip("hard links unavailable on this machine")

    target = write(tmp_path / "original.bin", b"SHARED-PAYLOAD")
    os.link(target, tmp_path / "hardlink.bin")

    # Identical content, but one file on disk: deleting either reclaims nothing.
    assert find_duplicates(scan(tmp_path)).groups == []


def test_symlinks_are_not_reported_as_duplicates(tmp_path: Path, supports_symlinks: bool) -> None:
    if not supports_symlinks:
        pytest.skip("symlinks unavailable on this machine")

    target = write(tmp_path / "real.bin", b"PAYLOAD-CONTENT")
    (tmp_path / "link.bin").symlink_to(target)

    assert find_duplicates(scan(tmp_path)).groups == []


class TestKeeperRecommendation:
    def test_clean_name_beats_numbered_copy(self, tmp_path: Path) -> None:
        write(tmp_path / "report.pdf", b"PAYLOAD")
        write(tmp_path / "report (1).pdf", b"PAYLOAD")

        group = find_duplicates(scan(tmp_path)).groups[0]
        assert group.keeper.path.name == "report.pdf"

    def test_clean_name_beats_copy_suffix(self, tmp_path: Path) -> None:
        write(tmp_path / "report.pdf", b"PAYLOAD")
        write(tmp_path / "report - Copy.pdf", b"PAYLOAD")

        group = find_duplicates(scan(tmp_path)).groups[0]
        assert group.keeper.path.name == "report.pdf"

    def test_filed_location_beats_downloads(self, tmp_path: Path) -> None:
        write(tmp_path / "Documents" / "Projects" / "report.pdf", b"PAYLOAD")
        write(tmp_path / "Downloads" / "report.pdf", b"PAYLOAD")

        group = find_duplicates(scan(tmp_path)).groups[0]
        assert group.keeper.path.parent.name == "Projects"

    def test_recommendation_is_stable_across_runs(self, tmp_path: Path) -> None:
        write(tmp_path / "a" / "shared.dat", b"PAYLOAD")
        write(tmp_path / "b" / "shared.dat", b"PAYLOAD")
        write(tmp_path / "c" / "shared.dat", b"PAYLOAD")

        keepers = {find_duplicates(scan(tmp_path)).groups[0].keeper.path for _ in range(5)}
        assert len(keepers) == 1

    def test_keeper_can_be_overridden(self, tmp_path: Path) -> None:
        write(tmp_path / "report.pdf", b"PAYLOAD")
        write(tmp_path / "report (1).pdf", b"PAYLOAD")

        group = find_duplicates(scan(tmp_path)).groups[0]
        chosen = group.duplicates[0]
        flipped = group.with_keeper(chosen)

        assert flipped.keeper == chosen
        assert group.keeper in flipped.duplicates
        assert flipped.file_count == group.file_count

    def test_overriding_with_a_foreign_file_is_rejected(self, tmp_path: Path) -> None:
        write(tmp_path / "report.pdf", b"PAYLOAD")
        write(tmp_path / "report (1).pdf", b"PAYLOAD")
        write(tmp_path / "unrelated.txt", b"OTHER")

        result = scan(tmp_path)
        group = find_duplicates(result).groups[0]
        outsider = next(e for e in result.entries if e.path.name == "unrelated.txt")

        with pytest.raises(ValueError, match="not a member"):
            group.with_keeper(outsider)


class TestCopyMarkers:
    @pytest.mark.parametrize(
        "stem",
        ["report (1)", "report (12)", "report - Copy", "report - copy (2)", "Copy of report"],
    )
    def test_detected(self, stem: str) -> None:
        assert has_copy_marker(stem)

    @pytest.mark.parametrize("stem", ["report", "IMG_1234", "2026-01-01-notes", "v2"])
    def test_not_detected(self, stem: str) -> None:
        assert not has_copy_marker(stem)


class TestTransientDirs:
    def test_downloads_is_transient(self) -> None:
        assert in_transient_dir(Path("/home/u/Downloads/a.pdf"))

    def test_documents_is_not(self) -> None:
        assert not in_transient_dir(Path("/home/u/Documents/a.pdf"))


class TestTieredStats:
    def test_full_hashing_is_skipped_for_size_singletons(self, tmp_path: Path) -> None:
        """Files with a unique size never reach any hashing tier."""
        for i in range(5):
            write(tmp_path / f"f{i}.dat", b"x" * (i + 1))

        report = find_duplicates(scan(tmp_path))
        assert report.stats.size_candidates == 0
        assert report.stats.fully_hashed == 0

    def test_partial_tier_avoids_full_reads_for_size_collisions(self, tmp_path: Path) -> None:
        """Same size, different head/tail: ruled out without a full read."""
        big = 20_000
        write(tmp_path / "a.bin", b"A" * big)
        write(tmp_path / "b.bin", b"B" * big)

        report = find_duplicates(scan(tmp_path))
        assert report.groups == []
        assert report.stats.size_candidates == 2
        assert report.stats.partial_hashed == 2
        assert report.stats.fully_hashed == 0
        assert report.stats.full_hashes_avoided == 2

    def test_real_duplicates_do_reach_the_full_tier(self, tmp_path: Path) -> None:
        write(tmp_path / "a.bin", b"A" * 20_000)
        write(tmp_path / "b.bin", b"A" * 20_000)

        report = find_duplicates(scan(tmp_path))
        assert report.stats.fully_hashed == 2
        assert len(report.groups) == 1
