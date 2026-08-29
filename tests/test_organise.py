from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pytest

from conftest import write
from file_organiser.organise import (
    ConflictPolicy,
    SkipReason,
    plan_organise,
)
from file_organiser.scanning import scan


def _at(path: Path, when: str) -> Path:
    """Force a file's mtime so date-based tests are deterministic."""
    stamp = datetime.fromisoformat(when).timestamp()
    os.utime(path, (stamp, stamp))
    return path


def _destinations(plan) -> dict[str, str]:
    return {m.source.name: m.destination.relative_to(plan.root).as_posix() for m in plan.moves}


class TestByType:
    def test_files_are_routed_to_category_folders(self, tmp_path: Path) -> None:
        write(tmp_path / "a.jpg", b"x")
        write(tmp_path / "b.pdf", b"y")
        write(tmp_path / "c.py", b"z")

        plan = plan_organise(scan(tmp_path), by_type=True)
        assert _destinations(plan) == {
            "a.jpg": "images/a.jpg",
            "b.pdf": "documents/b.pdf",
            "c.py": "code/c.py",
        }

    def test_unrecognised_extensions_go_to_other(self, tmp_path: Path) -> None:
        write(tmp_path / "mystery.qqq", b"x")
        plan = plan_organise(scan(tmp_path), by_type=True)
        assert _destinations(plan) == {"mystery.qqq": "other/mystery.qqq"}

    def test_planning_never_touches_the_filesystem(self, tmp_path: Path) -> None:
        write(tmp_path / "a.jpg", b"x")
        before = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))

        plan_organise(scan(tmp_path), by_type=True)

        after = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))
        assert before == after


class TestByDate:
    def test_files_are_nested_by_year_and_month(self, tmp_path: Path) -> None:
        _at(write(tmp_path / "a.jpg", b"x"), "2026-08-14T12:00:00")

        plan = plan_organise(scan(tmp_path), by_type=False, by_date=True)
        assert _destinations(plan) == {"a.jpg": "2026/08-August/a.jpg"}

    def test_month_names_do_not_depend_on_locale(self, tmp_path: Path) -> None:
        _at(write(tmp_path / "a.jpg", b"x"), "2026-01-05T09:00:00")
        plan = plan_organise(scan(tmp_path), by_type=False, by_date=True)
        assert _destinations(plan) == {"a.jpg": "2026/01-January/a.jpg"}

    def test_combined_nests_date_under_category(self, tmp_path: Path) -> None:
        _at(write(tmp_path / "a.jpg", b"x"), "2026-08-14T12:00:00")

        plan = plan_organise(scan(tmp_path), by_type=True, by_date=True)
        assert _destinations(plan) == {"a.jpg": "images/2026/08-August/a.jpg"}

    def test_at_least_one_mode_is_required(self, tmp_path: Path) -> None:
        write(tmp_path / "a.jpg", b"x")
        with pytest.raises(ValueError, match="at least one"):
            plan_organise(scan(tmp_path), by_type=False, by_date=False)


class TestIdempotence:
    def test_already_filed_files_are_skipped_not_moved(self, tmp_path: Path) -> None:
        write(tmp_path / "images" / "a.jpg", b"x")

        plan = plan_organise(scan(tmp_path), by_type=True)
        assert plan.moves == []
        assert [s.reason for s in plan.skipped] == [SkipReason.ALREADY_IN_PLACE]

    def test_a_second_run_over_organised_output_is_a_no_op(self, tmp_path: Path) -> None:
        _at(
            write(tmp_path / "images" / "2026" / "08-August" / "a.jpg", b"x"), "2026-08-14T12:00:00"
        )

        plan = plan_organise(scan(tmp_path), by_type=True, by_date=True)
        assert plan.is_empty()


class TestConflicts:
    def test_existing_destination_is_renamed_by_default(self, tmp_path: Path) -> None:
        write(tmp_path / "documents" / "report.pdf", b"ORIGINAL")
        write(tmp_path / "inbox" / "report.pdf", b"DIFFERENT")

        plan = plan_organise(scan(tmp_path), by_type=True)
        assert _destinations(plan) == {"report.pdf": "documents/report (1).pdf"}
        assert plan.moves[0].renamed
        assert plan.conflict_count == 1

    def test_existing_destination_is_never_silently_overwritten(self, tmp_path: Path) -> None:
        write(tmp_path / "documents" / "report.pdf", b"ORIGINAL")
        write(tmp_path / "inbox" / "report.pdf", b"DIFFERENT")

        plan = plan_organise(scan(tmp_path), by_type=True)
        occupied = tmp_path / "documents" / "report.pdf"
        assert all(m.destination != occupied for m in plan.moves)

    def test_skip_policy_leaves_the_file_alone(self, tmp_path: Path) -> None:
        write(tmp_path / "documents" / "report.pdf", b"ORIGINAL")
        write(tmp_path / "inbox" / "report.pdf", b"DIFFERENT")

        plan = plan_organise(scan(tmp_path), by_type=True, policy=ConflictPolicy.SKIP)
        assert plan.moves == []
        # documents/report.pdf is already filed; inbox/report.pdf is the conflict.
        skipped = {s.path.parent.name: s.reason for s in plan.skipped}
        assert skipped == {
            "documents": SkipReason.ALREADY_IN_PLACE,
            "inbox": SkipReason.CONFLICT,
        }

    def test_replace_policy_is_flagged_explicitly(self, tmp_path: Path) -> None:
        write(tmp_path / "documents" / "report.pdf", b"ORIGINAL")
        write(tmp_path / "inbox" / "report.pdf", b"DIFFERENT")

        plan = plan_organise(scan(tmp_path), by_type=True, policy=ConflictPolicy.REPLACE)
        assert plan.moves[0].replaces_existing
        assert _destinations(plan) == {"report.pdf": "documents/report.pdf"}

    def test_two_files_in_one_batch_never_collapse(self, tmp_path: Path) -> None:
        """Distinct files with the same name must both survive."""
        write(tmp_path / "one" / "notes.txt", b"FIRST")
        write(tmp_path / "two" / "notes.txt", b"SECOND")

        plan = plan_organise(scan(tmp_path), by_type=True)
        targets = [m.destination for m in plan.moves]
        assert len(targets) == 2
        assert len(set(targets)) == 2

    def test_in_batch_collision_renames_even_under_skip_policy(self, tmp_path: Path) -> None:
        """Skip would discard one of two distinct files, so rename wins here."""
        write(tmp_path / "one" / "notes.txt", b"FIRST")
        write(tmp_path / "two" / "notes.txt", b"SECOND")

        plan = plan_organise(scan(tmp_path), by_type=True, policy=ConflictPolicy.SKIP)
        assert len(plan.moves) == 2
        assert len({m.destination for m in plan.moves}) == 2

    def test_in_batch_collision_renames_even_under_replace_policy(self, tmp_path: Path) -> None:
        write(tmp_path / "one" / "notes.txt", b"FIRST")
        write(tmp_path / "two" / "notes.txt", b"SECOND")

        plan = plan_organise(scan(tmp_path), by_type=True, policy=ConflictPolicy.REPLACE)
        assert len({m.destination for m in plan.moves}) == 2

    def test_rename_counter_climbs_past_existing_copies(self, tmp_path: Path) -> None:
        write(tmp_path / "documents" / "report.pdf", b"A")
        write(tmp_path / "documents" / "report (1).pdf", b"B")
        write(tmp_path / "inbox" / "report.pdf", b"C")

        plan = plan_organise(scan(tmp_path), by_type=True)
        assert _destinations(plan)["report.pdf"] == "documents/report (2).pdf"

    def test_three_way_batch_collision_gets_distinct_names(self, tmp_path: Path) -> None:
        for folder in ("one", "two", "three"):
            write(tmp_path / folder / "notes.txt", folder.encode())

        plan = plan_organise(scan(tmp_path), by_type=True)
        names = sorted(m.destination.name for m in plan.moves)
        assert names == ["notes (1).txt", "notes (2).txt", "notes.txt"]


class TestSymlinks:
    def test_symlinks_are_skipped(self, tmp_path: Path, supports_symlinks: bool) -> None:
        if not supports_symlinks:
            pytest.skip("symlinks unavailable on this machine")

        target = write(tmp_path / "real.bin", b"PAYLOAD")
        (tmp_path / "link.bin").symlink_to(target)

        plan = plan_organise(scan(tmp_path), by_type=True)
        assert SkipReason.SYMLINK in {s.reason for s in plan.skipped}
        assert "link.bin" not in {m.source.name for m in plan.moves}


class TestDeterminism:
    def test_the_same_tree_always_produces_the_same_plan(self, messy: Path) -> None:
        plans = [
            [(m.source, m.destination) for m in plan_organise(scan(messy), by_type=True).moves]
            for _ in range(5)
        ]
        assert all(p == plans[0] for p in plans)
