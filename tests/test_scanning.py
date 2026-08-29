from __future__ import annotations

import os
from pathlib import Path

import pytest

from conftest import write
from file_organiser.categories import Ruleset
from file_organiser.scanning import scan


def test_scan_finds_every_file(messy: Path) -> None:
    result = scan(messy)
    assert result.file_count == 9
    assert result.errors == []


def test_scan_never_mutates_the_tree(messy: Path) -> None:
    before = {p: p.stat().st_mtime_ns for p in messy.rglob("*") if p.is_file()}
    scan(messy)
    after = {p: p.stat().st_mtime_ns for p in messy.rglob("*") if p.is_file()}
    assert before == after


def test_categories_are_assigned(messy: Path) -> None:
    counts = scan(messy).by_category
    assert counts["images"] == 1
    assert counts["code"] == 1
    # notes.txt + report.pdf + report (1).pdf
    assert counts["documents"] == 3
    # unknown.qqq, two .bin decoys, empty.log
    assert counts["other"] == 4


def test_total_size_is_the_sum_of_entries(messy: Path) -> None:
    result = scan(messy)
    expected = sum(p.stat().st_size for p in messy.rglob("*") if p.is_file())
    assert result.total_size == expected


def test_size_groups_pair_the_real_duplicate_and_the_decoys(messy: Path) -> None:
    groups = scan(messy).size_groups()
    grouped_names = {frozenset(e.path.name for e in group) for group in groups}

    assert frozenset({"report.pdf", "report (1).pdf"}) in grouped_names
    # Same size, different content: still a *candidate* at this stage. Only the
    # hashing pass in dedupe may rule on it.
    assert frozenset({"decoy_a.bin", "decoy_b.bin"}) in grouped_names


def test_empty_files_are_never_dedupe_candidates(messy: Path) -> None:
    result = scan(messy)
    assert all(e.size > 0 for e in result.dedupe_candidates())
    assert "empty.log" not in {e.path.name for e in result.dedupe_candidates()}


def test_potential_reclaimable_is_an_upper_bound(messy: Path) -> None:
    result = scan(messy)
    # Two candidate groups of two, so one redundant copy each.
    assert result.duplicate_candidate_count == 2
    assert result.potential_reclaimable > 0


def test_default_excludes_skip_dependency_directories(tmp_path: Path) -> None:
    write(tmp_path / "keep.txt", "yes")
    write(tmp_path / "node_modules" / "pkg" / "index.js", "nope")
    write(tmp_path / ".venv" / "pyvenv.cfg", "nope")

    result = scan(tmp_path)
    assert {e.path.name for e in result.entries} == {"keep.txt"}
    assert result.skipped >= 2


def test_custom_excludes_are_applied(tmp_path: Path) -> None:
    write(tmp_path / "keep.txt", "yes")
    write(tmp_path / "Work" / "secret.txt", "nope")
    write(tmp_path / "scratch.tmp", "nope")

    result = scan(tmp_path, exclude=["Work", "*.tmp"])
    assert {e.path.name for e in result.entries} == {"keep.txt"}


def test_custom_ruleset_is_used(tmp_path: Path) -> None:
    write(tmp_path / "a.jpg", "x")
    rules = Ruleset.from_mapping({"photos": ("jpg",)})
    assert scan(tmp_path, ruleset=rules).by_category["photos"] == 1


def test_empty_directory_scans_cleanly(tmp_path: Path) -> None:
    result = scan(tmp_path)
    assert result.file_count == 0
    assert result.total_size == 0
    assert result.size_groups() == []


def test_symlinks_are_recorded_but_excluded_from_dedupe(
    tmp_path: Path, supports_symlinks: bool
) -> None:
    if not supports_symlinks:
        pytest.skip("symlinks unavailable on this machine")

    target = write(tmp_path / "real.bin", b"PAYLOAD")
    (tmp_path / "link.bin").symlink_to(target)

    result = scan(tmp_path)
    by_name = {e.path.name: e for e in result.entries}
    assert by_name["link.bin"].is_symlink
    assert not by_name["real.bin"].is_symlink
    assert "link.bin" not in {e.path.name for e in result.dedupe_candidates()}


def test_symlinked_directories_are_not_followed(tmp_path: Path, supports_symlinks: bool) -> None:
    if not supports_symlinks:
        pytest.skip("symlinks unavailable on this machine")

    real_dir = tmp_path / "real"
    write(real_dir / "inside.txt", "x")
    loop = tmp_path / "loop"
    try:
        loop.symlink_to(real_dir, target_is_directory=True)
    except OSError:  # pragma: no cover - directory symlinks may need privileges
        pytest.skip("directory symlinks unavailable")

    result = scan(tmp_path)
    # inside.txt is seen once via the real directory, never again through the link.
    assert [e.path.name for e in result.entries].count("inside.txt") == 1


def test_hard_links_collapse_to_one_representative(
    tmp_path: Path, supports_hardlinks: bool
) -> None:
    if not supports_hardlinks:
        pytest.skip("hard links unavailable on this machine")

    target = write(tmp_path / "original.bin", b"SHARED-PAYLOAD")
    os.link(target, tmp_path / "hardlink.bin")

    result = scan(tmp_path)
    assert result.file_count == 2
    # Both are the same file on disk, so they must not form a duplicate group:
    # deleting one reclaims nothing.
    assert result.size_groups() == []


def test_unreadable_directory_is_reported_not_raised(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("POSIX permission semantics")

    blocked = tmp_path / "blocked"
    blocked.mkdir()
    write(blocked / "hidden.txt", "x")
    blocked.chmod(0o000)
    try:
        result = scan(tmp_path)
        assert result.errors
    finally:
        blocked.chmod(0o755)
