from __future__ import annotations

from pathlib import Path

import pytest

from conftest import write
from file_organiser.dedupe import find_duplicates
from file_organiser.execute import apply_dedupe, apply_organise, purge
from file_organiser.organise import ConflictPolicy, plan_organise
from file_organiser.scanning import scan
from file_organiser.transactions import TransactionError, TransactionStore, undo


def snapshot(root: Path) -> dict[str, bytes]:
    """User files under root, keyed by relative path, with contents.

    The tool's own store is excluded: transaction records and quarantine are
    bookkeeping, not user data, and counting them would mask what actually
    happened to the files a person cares about.
    """
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and ".file-organiser" not in p.parts
    }


class TestDryRunIsTheDefault:
    def test_organise_dry_run_changes_nothing(self, messy: Path) -> None:
        before = snapshot(messy)
        report = apply_organise(plan_organise(scan(messy), by_type=True))

        assert report.dry_run
        assert report.moved > 0  # it reports what it *would* do
        assert snapshot(messy) == before

    def test_organise_defaults_to_dry_run(self, messy: Path) -> None:
        """Calling without dry_run must not be destructive."""
        before = snapshot(messy)
        apply_organise(plan_organise(scan(messy), by_type=True))
        assert snapshot(messy) == before

    def test_dedupe_dry_run_changes_nothing(self, messy: Path) -> None:
        before = snapshot(messy)
        report = apply_dedupe(find_duplicates(scan(messy)), root=messy)

        assert report.dry_run
        assert report.quarantined == 1
        assert snapshot(messy) == before

    def test_dry_run_writes_no_transaction(self, messy: Path) -> None:
        apply_organise(plan_organise(scan(messy), by_type=True))
        assert TransactionStore(messy).list_transactions() == []


class TestOrganiseApply:
    def test_files_land_in_category_folders(self, tmp_path: Path) -> None:
        write(tmp_path / "a.jpg", b"IMG")
        write(tmp_path / "b.pdf", b"DOC")

        apply_organise(plan_organise(scan(tmp_path), by_type=True), dry_run=False)

        assert (tmp_path / "images" / "a.jpg").read_bytes() == b"IMG"
        assert (tmp_path / "documents" / "b.pdf").read_bytes() == b"DOC"
        assert not (tmp_path / "a.jpg").exists()

    def test_no_file_contents_are_lost(self, messy: Path) -> None:
        before = sorted(snapshot(messy).values())
        apply_organise(plan_organise(scan(messy), by_type=True), dry_run=False)
        after = sorted(snapshot(messy).values())
        assert before == after

    def test_a_conflicting_file_is_renamed_not_overwritten(self, tmp_path: Path) -> None:
        write(tmp_path / "documents" / "report.pdf", b"ORIGINAL")
        write(tmp_path / "inbox" / "report.pdf", b"DIFFERENT")

        apply_organise(plan_organise(scan(tmp_path), by_type=True), dry_run=False)

        assert (tmp_path / "documents" / "report.pdf").read_bytes() == b"ORIGINAL"
        assert (tmp_path / "documents" / "report (1).pdf").read_bytes() == b"DIFFERENT"

    def test_replaced_file_is_quarantined_not_destroyed(self, tmp_path: Path) -> None:
        """Even --conflict replace must leave the displaced file recoverable."""
        write(tmp_path / "documents" / "report.pdf", b"ORIGINAL")
        write(tmp_path / "inbox" / "report.pdf", b"DIFFERENT")

        report = apply_organise(
            plan_organise(scan(tmp_path), by_type=True, policy=ConflictPolicy.REPLACE),
            dry_run=False,
        )

        assert (tmp_path / "documents" / "report.pdf").read_bytes() == b"DIFFERENT"
        assert report.quarantined == 1
        quarantined = list((tmp_path / ".file-organiser" / "quarantine").rglob("*.pdf"))
        assert [p.read_bytes() for p in quarantined] == [b"ORIGINAL"]

    def test_a_transaction_is_recorded(self, messy: Path) -> None:
        report = apply_organise(plan_organise(scan(messy), by_type=True), dry_run=False)

        assert report.operation_id
        transaction = TransactionStore(messy).load(report.operation_id)
        assert transaction.command == "organise"
        assert transaction.move_count == report.moved


class TestUndoOrganise:
    def test_undo_restores_every_file(self, messy: Path) -> None:
        before = snapshot(messy)
        report = apply_organise(plan_organise(scan(messy), by_type=True), dry_run=False)
        assert snapshot(messy) != before

        store = TransactionStore(messy)
        undo_report = undo(store.load(report.operation_id), store)

        assert undo_report.ok
        assert undo_report.restored == report.moved
        assert snapshot(messy) == before

    def test_undo_removes_the_folders_it_created(self, tmp_path: Path) -> None:
        write(tmp_path / "a.jpg", b"IMG")
        report = apply_organise(plan_organise(scan(tmp_path), by_type=True), dry_run=False)
        assert (tmp_path / "images").is_dir()

        store = TransactionStore(tmp_path)
        undo(store.load(report.operation_id), store)

        assert not (tmp_path / "images").exists()

    def test_undo_is_recorded_and_cannot_run_twice(self, tmp_path: Path) -> None:
        write(tmp_path / "a.jpg", b"IMG")
        report = apply_organise(plan_organise(scan(tmp_path), by_type=True), dry_run=False)

        store = TransactionStore(tmp_path)
        undo(store.load(report.operation_id), store)

        with pytest.raises(TransactionError, match="already undone"):
            undo(store.load(report.operation_id), store)

    def test_undo_refuses_to_overwrite_a_reoccupied_original(self, tmp_path: Path) -> None:
        write(tmp_path / "a.jpg", b"IMG")
        report = apply_organise(plan_organise(scan(tmp_path), by_type=True), dry_run=False)

        # Someone puts a new, different file where the original used to be.
        write(tmp_path / "a.jpg", b"SOMETHING NEW")

        store = TransactionStore(tmp_path)
        undo_report = undo(store.load(report.operation_id), store)

        assert not undo_report.ok
        assert (tmp_path / "a.jpg").read_bytes() == b"SOMETHING NEW"
        assert (tmp_path / "images" / "a.jpg").read_bytes() == b"IMG"

    def test_undo_reports_a_file_that_vanished(self, tmp_path: Path) -> None:
        write(tmp_path / "a.jpg", b"IMG")
        report = apply_organise(plan_organise(scan(tmp_path), by_type=True), dry_run=False)

        (tmp_path / "images" / "a.jpg").unlink()

        store = TransactionStore(tmp_path)
        undo_report = undo(store.load(report.operation_id), store)
        assert not undo_report.ok
        assert "no longer" in undo_report.failed[0][1]


class TestDedupeApply:
    def test_duplicate_is_quarantined_and_keeper_survives(self, tmp_path: Path) -> None:
        write(tmp_path / "report.pdf", b"PAYLOAD")
        write(tmp_path / "report (1).pdf", b"PAYLOAD")

        report = apply_dedupe(find_duplicates(scan(tmp_path)), root=tmp_path, dry_run=False)

        assert report.quarantined == 1
        assert (tmp_path / "report.pdf").read_bytes() == b"PAYLOAD"
        assert not (tmp_path / "report (1).pdf").exists()

    def test_quarantined_file_still_exists_on_disk(self, tmp_path: Path) -> None:
        """Quarantine is not deletion: the bytes are still recoverable."""
        write(tmp_path / "report.pdf", b"PAYLOAD")
        write(tmp_path / "report (1).pdf", b"PAYLOAD")

        apply_dedupe(find_duplicates(scan(tmp_path)), root=tmp_path, dry_run=False)

        held = list((tmp_path / ".file-organiser" / "quarantine").rglob("*.pdf"))
        assert [p.read_bytes() for p in held] == [b"PAYLOAD"]

    def test_undo_restores_the_duplicate(self, tmp_path: Path) -> None:
        write(tmp_path / "report.pdf", b"PAYLOAD")
        write(tmp_path / "report (1).pdf", b"PAYLOAD")

        report = apply_dedupe(find_duplicates(scan(tmp_path)), root=tmp_path, dry_run=False)
        store = TransactionStore(tmp_path)
        undo(store.load(report.operation_id), store)

        assert (tmp_path / "report (1).pdf").read_bytes() == b"PAYLOAD"
        assert (tmp_path / "report.pdf").read_bytes() == b"PAYLOAD"

    def test_file_changed_since_detection_is_left_alone(self, tmp_path: Path) -> None:
        """The TOCTOU guard, end to end."""
        write(tmp_path / "report.pdf", b"PAYLOAD")
        write(tmp_path / "report (1).pdf", b"PAYLOAD")

        dedupe_report = find_duplicates(scan(tmp_path))
        # Someone edits the copy between detection and apply.
        (tmp_path / "report (1).pdf").write_bytes(b"EDITED SINCE THE SCAN")

        report = apply_dedupe(dedupe_report, root=tmp_path, dry_run=False)

        assert report.quarantined == 0
        assert report.errors
        assert (tmp_path / "report (1).pdf").read_bytes() == b"EDITED SINCE THE SCAN"

    def test_several_duplicates_sharing_a_name_do_not_collide(self, tmp_path: Path) -> None:
        for folder in ("a", "b", "c"):
            write(tmp_path / folder / "shared.dat", b"IDENTICAL")

        report = apply_dedupe(find_duplicates(scan(tmp_path)), root=tmp_path, dry_run=False)

        assert report.quarantined == 2
        held = list((tmp_path / ".file-organiser" / "quarantine").rglob("*.dat"))
        assert len(held) == 2

    def test_only_selected_groups_are_acted_on(self, tmp_path: Path) -> None:
        write(tmp_path / "x1.dat", b"GROUP-X-PAYLOAD")
        write(tmp_path / "x2.dat", b"GROUP-X-PAYLOAD")
        write(tmp_path / "y1.dat", b"GROUP-Y")
        write(tmp_path / "y2.dat", b"GROUP-Y")

        found = find_duplicates(scan(tmp_path))
        assert len(found.groups) == 2
        chosen = [g for g in found.groups if g.keeper.path.name.startswith("x")]

        report = apply_dedupe(found, root=tmp_path, dry_run=False, groups=chosen)

        assert report.quarantined == 1
        assert (tmp_path / "y1.dat").exists()
        assert (tmp_path / "y2.dat").exists()

    def test_keeper_override_is_respected(self, tmp_path: Path) -> None:
        write(tmp_path / "report.pdf", b"PAYLOAD")
        write(tmp_path / "report (1).pdf", b"PAYLOAD")

        found = find_duplicates(scan(tmp_path))
        group = found.groups[0]
        flipped = group.with_keeper(group.duplicates[0])

        apply_dedupe(found, root=tmp_path, dry_run=False, groups=[flipped])

        # The copy the user chose survives; the default recommendation does not.
        assert (tmp_path / "report (1).pdf").exists()
        assert not (tmp_path / "report.pdf").exists()


class TestPurge:
    def test_purge_dry_run_keeps_the_files(self, tmp_path: Path) -> None:
        write(tmp_path / "report.pdf", b"PAYLOAD")
        write(tmp_path / "report (1).pdf", b"PAYLOAD")
        apply_dedupe(find_duplicates(scan(tmp_path)), root=tmp_path, dry_run=False)

        store = TransactionStore(tmp_path)
        report = purge(store)

        assert report.files_removed == 1
        assert list(store.quarantine_root.rglob("*.pdf"))

    def test_purge_frees_the_space(self, tmp_path: Path) -> None:
        write(tmp_path / "report.pdf", b"PAYLOAD")
        write(tmp_path / "report (1).pdf", b"PAYLOAD")
        apply_dedupe(find_duplicates(scan(tmp_path)), root=tmp_path, dry_run=False)

        store = TransactionStore(tmp_path)
        report = purge(store, dry_run=False)

        assert report.files_removed == 1
        assert report.bytes_freed == len(b"PAYLOAD")
        assert not list(store.quarantine_root.rglob("*.pdf"))

    def test_a_purged_transaction_can_no_longer_be_undone(self, tmp_path: Path) -> None:
        write(tmp_path / "report.pdf", b"PAYLOAD")
        write(tmp_path / "report (1).pdf", b"PAYLOAD")
        applied = apply_dedupe(find_duplicates(scan(tmp_path)), root=tmp_path, dry_run=False)

        store = TransactionStore(tmp_path)
        purge(store, dry_run=False)

        with pytest.raises(TransactionError, match="purged"):
            undo(store.load(applied.operation_id), store)

    def test_purge_leaves_organise_moves_undoable(self, tmp_path: Path) -> None:
        """Purge only empties quarantine; it must not touch moved files."""
        write(tmp_path / "a.jpg", b"IMG")
        applied = apply_organise(plan_organise(scan(tmp_path), by_type=True), dry_run=False)

        store = TransactionStore(tmp_path)
        purge(store, dry_run=False)

        undo_report = undo(store.load(applied.operation_id), store)
        assert undo_report.ok
        assert (tmp_path / "a.jpg").read_bytes() == b"IMG"


class TestOperationIds:
    def test_ids_increment_within_a_day(self, tmp_path: Path) -> None:
        store = TransactionStore(tmp_path)
        first = store.next_operation_id(today="2026-08-29")
        assert first == "2026-08-29-001"

        write(tmp_path / "a.jpg", b"IMG")
        apply_organise(plan_organise(scan(tmp_path), by_type=True), dry_run=False)

        assert store.next_operation_id().endswith("-002")

    def test_transactions_list_newest_first(self, tmp_path: Path) -> None:
        write(tmp_path / "a.jpg", b"IMG")
        apply_organise(plan_organise(scan(tmp_path), by_type=True), dry_run=False)
        write(tmp_path / "b.pdf", b"DOC")
        apply_organise(plan_organise(scan(tmp_path), by_type=True), dry_run=False)

        store = TransactionStore(tmp_path)
        ids = [t.operation_id for t in store.list_transactions()]
        assert ids == sorted(ids, reverse=True)
        assert len(ids) == 2

    def test_latest_undoable_skips_undone_transactions(self, tmp_path: Path) -> None:
        write(tmp_path / "a.jpg", b"IMG")
        first = apply_organise(plan_organise(scan(tmp_path), by_type=True), dry_run=False)

        store = TransactionStore(tmp_path)
        undo(store.load(first.operation_id), store)

        assert store.latest_undoable() is None

    def test_unknown_operation_id_is_reported(self, tmp_path: Path) -> None:
        with pytest.raises(TransactionError, match="no transaction"):
            TransactionStore(tmp_path).load("2026-01-01-999")


class TestStoreIsExcludedFromScans:
    def test_the_store_is_never_scanned(self, tmp_path: Path) -> None:
        write(tmp_path / "a.jpg", b"IMG")
        apply_organise(plan_organise(scan(tmp_path), by_type=True), dry_run=False)

        found = {e.path.name for e in scan(tmp_path).entries}
        assert not any(name.endswith(".json") for name in found)

    def test_a_second_organise_ignores_the_store(self, tmp_path: Path) -> None:
        write(tmp_path / "report.pdf", b"PAYLOAD")
        write(tmp_path / "report (1).pdf", b"PAYLOAD")
        apply_dedupe(find_duplicates(scan(tmp_path)), root=tmp_path, dry_run=False)

        plan = plan_organise(scan(tmp_path), by_type=True)
        assert all(".file-organiser" not in str(m.source) for m in plan.moves)
