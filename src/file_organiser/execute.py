"""Applying plans. The only module in this package that mutates the filesystem.

Everything here is gated: callers must pass ``dry_run=False`` explicitly, and the
CLI only does so when the user passes ``--apply``. A dry run walks exactly the
same code path and produces exactly the same report, minus the writes.

Two invariants hold throughout:

* nothing is overwritten that has not first been moved somewhere recoverable;
* every completed action is written to a transaction record, even when the run
  fails part way through, so a partial run is still undoable.
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from file_organiser.dedupe import DedupeReport, DuplicateGroup
from file_organiser.hashing import verify_still_identical
from file_organiser.organise import OrganisePlan
from file_organiser.transactions import (
    Action,
    ActionType,
    Transaction,
    TransactionStore,
)


@dataclass
class OperationReport:
    """What a run did, or would have done."""

    command: str
    root: Path
    dry_run: bool
    operation_id: str | None = None
    moved: int = 0
    quarantined: int = 0
    skipped: int = 0
    conflicts: int = 0
    bytes_moved: int = 0
    bytes_quarantined: int = 0
    errors: list[tuple[Path, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def changed_anything(self) -> bool:
        return bool(self.moved or self.quarantined)


def apply_organise(
    plan: OrganisePlan,
    *,
    store: TransactionStore | None = None,
    dry_run: bool = True,
) -> OperationReport:
    """Carry out an organise plan, or describe it without touching anything."""
    report = OperationReport(
        command="organise",
        root=plan.root,
        dry_run=dry_run,
        skipped=plan.skip_count,
        conflicts=plan.conflict_count,
    )

    if dry_run:
        report.moved = plan.move_count
        report.bytes_moved = plan.bytes_moved
        return report

    store = store or TransactionStore(plan.root)
    transaction = _new_transaction("organise", plan.root, store)
    created_dirs: set[str] = set()

    try:
        for move in plan.moves:
            try:
                _ensure_dir(move.destination.parent, plan.root, created_dirs)

                if move.replaces_existing and move.destination.exists():
                    # Never destroy the file being replaced: quarantine it first
                    # so the replacement remains undoable.
                    _quarantine(
                        move.destination,
                        plan.root,
                        store,
                        transaction,
                        created_dirs,
                    )
                    report.quarantined += 1

                move.source.replace(move.destination)
                transaction.actions.append(
                    Action(
                        type=ActionType.MOVE,
                        source=_rel(move.source, plan.root),
                        destination=_rel(move.destination, plan.root),
                        size=move.size,
                    )
                )
                report.moved += 1
                report.bytes_moved += move.size
            except OSError as exc:
                report.errors.append((move.source, str(exc)))
    finally:
        # Persist whatever completed, even if the loop died, so a partial run
        # can still be undone.
        transaction.created_dirs = sorted(created_dirs)
        if transaction.actions:
            store.save(transaction)
            report.operation_id = transaction.operation_id

    return report


def apply_dedupe(
    dedupe_report: DedupeReport,
    *,
    store: TransactionStore | None = None,
    root: Path | None = None,
    dry_run: bool = True,
    groups: list[DuplicateGroup] | None = None,
) -> OperationReport:
    """Quarantine the redundant copies in each duplicate group.

    Files are moved into the quarantine, never unlinked, so the operation stays
    undoable. Space is reclaimed by ``purge``, not here.

    Every file is re-hashed against its keeper immediately before being touched.
    A file that changed between detection and now is left alone and reported.
    """
    selected = groups if groups is not None else dedupe_report.groups
    target_root = root or (selected[0].keeper.path.parent if selected else Path.cwd())

    report = OperationReport(command="dedupe", root=target_root, dry_run=dry_run)

    if dry_run:
        report.quarantined = sum(len(g.duplicates) for g in selected)
        report.bytes_quarantined = sum(g.reclaimable for g in selected)
        return report

    store = store or TransactionStore(target_root)
    transaction = _new_transaction("dedupe", target_root, store)
    created_dirs: set[str] = set()

    try:
        for group in selected:
            for duplicate in group.duplicates:
                if not verify_still_identical(group.keeper.path, duplicate.path):
                    report.errors.append(
                        (
                            duplicate.path,
                            "content no longer matches its keeper; left untouched",
                        )
                    )
                    continue
                try:
                    _quarantine(
                        duplicate.path,
                        target_root,
                        store,
                        transaction,
                        created_dirs,
                        digest=group.digest,
                        size=duplicate.size,
                    )
                    report.quarantined += 1
                    report.bytes_quarantined += duplicate.size
                except OSError as exc:
                    report.errors.append((duplicate.path, str(exc)))
    finally:
        transaction.created_dirs = sorted(created_dirs)
        if transaction.actions:
            store.save(transaction)
            report.operation_id = transaction.operation_id

    return report


@dataclass
class PurgeReport:
    operation_ids: list[str] = field(default_factory=list)
    files_removed: int = 0
    bytes_freed: int = 0
    errors: list[tuple[Path, str]] = field(default_factory=list)


def purge(
    store: TransactionStore,
    *,
    operation_id: str | None = None,
    dry_run: bool = True,
) -> PurgeReport:
    """Permanently delete quarantined files, reclaiming their space.

    This is the one irreversible operation in the tool. After a purge the
    corresponding transaction can no longer be undone, and it is marked as such.
    """
    report = PurgeReport()

    candidates = (
        [store.load(operation_id)]
        if operation_id
        else [t for t in store.list_transactions() if t.quarantine_count and not t.is_purged]
    )

    for transaction in candidates:
        if transaction.is_purged:
            continue
        quarantine = store.quarantine_dir_for(transaction.operation_id)
        if not quarantine.is_dir():
            continue

        report.operation_ids.append(transaction.operation_id)
        for path in sorted(quarantine.rglob("*")):
            if not path.is_file():
                continue
            size = path.stat().st_size
            if dry_run:
                report.files_removed += 1
                report.bytes_freed += size
                continue
            try:
                path.unlink()
                report.files_removed += 1
                report.bytes_freed += size
            except OSError as exc:
                report.errors.append((path, str(exc)))

        if not dry_run:
            _remove_tree_if_empty(quarantine)
            transaction.purged_at = datetime.now().isoformat(timespec="seconds")
            store.save(transaction)

    return report


def _new_transaction(command: str, root: Path, store: TransactionStore) -> Transaction:
    return Transaction(
        operation_id=store.next_operation_id(),
        command=command,
        root=root,
        created_at=datetime.now().isoformat(timespec="seconds"),
    )


def _rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _ensure_dir(directory: Path, root: Path, created: set[str]) -> None:
    """Create ``directory``, remembering any level this tool brought into being."""
    if directory.is_dir():
        return
    missing = []
    probe = directory
    while not probe.exists() and probe != probe.parent:
        missing.append(probe)
        probe = probe.parent
    directory.mkdir(parents=True, exist_ok=True)
    for made in missing:
        created.add(_rel(made, root))


def _quarantine(
    path: Path,
    root: Path,
    store: TransactionStore,
    transaction: Transaction,
    created: set[str],
    *,
    digest: str | None = None,
    size: int | None = None,
) -> Path:
    """Move ``path`` into this operation's quarantine and record the action."""
    destination_dir = store.quarantine_dir_for(transaction.operation_id)
    _ensure_dir(destination_dir, root, created)

    # Index-prefixed so several files sharing a name never collide in here.
    index = sum(1 for a in transaction.actions if a.type is ActionType.QUARANTINE)
    destination = destination_dir / f"{index:04d}_{path.name}"

    resolved_size = size if size is not None else path.stat().st_size
    path.replace(destination)
    transaction.actions.append(
        Action(
            type=ActionType.QUARANTINE,
            source=_rel(path, root),
            destination=_rel(destination, root),
            size=resolved_size,
            digest=digest,
        )
    )
    return destination


def _remove_tree_if_empty(directory: Path) -> None:
    """Tidy up emptied quarantine folders; leaving one behind is harmless."""
    for child in sorted(directory.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if child.is_dir():
            with suppress(OSError):
                child.rmdir()
    with suppress(OSError):
        directory.rmdir()
