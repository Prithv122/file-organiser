"""The transaction log, and the undo built on top of it.

Dry-run prevents mistakes; undo recovers from them. They solve different
problems, and this tool has both.

Every modifying run records what it did as a JSON transaction under
``<root>/.file-organiser/transactions/``. Undo replays that record backwards.

The consequence for deletion is the important design decision here: a file that
has been unlinked cannot be restored, so ``dedupe`` never unlinks. It *moves*
redundant copies into ``<root>/.file-organiser/quarantine/<operation-id>/``,
which is undoable. Disk space is therefore not reclaimed until ``purge`` empties
the quarantine, at which point the operation stops being undoable. That tradeoff
is deliberate and is reported honestly in the command output.

The store lives inside the target root so quarantining is a same-volume rename
rather than a copy across filesystems.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path

STORE_DIRNAME = ".file-organiser"
_ID_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2})-(\d{3})$")


class TransactionError(RuntimeError):
    """Raised when a transaction cannot be read, written or replayed."""


class ActionType(StrEnum):
    MOVE = "move"
    QUARANTINE = "quarantine"


@dataclass(frozen=True)
class Action:
    """One reversible filesystem change. Paths are relative to the root."""

    type: ActionType
    source: str
    destination: str
    size: int = 0
    digest: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "type": str(self.type),
            "source": self.source,
            "destination": self.destination,
            "size": self.size,
        }
        if self.digest:
            payload["digest"] = self.digest
        return payload

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> Action:
        try:
            return cls(
                type=ActionType(str(raw["type"])),
                source=str(raw["source"]),
                destination=str(raw["destination"]),
                size=int(raw.get("size", 0)),  # type: ignore[arg-type]
                digest=str(raw["digest"]) if raw.get("digest") else None,
            )
        except (KeyError, ValueError) as exc:
            raise TransactionError(f"malformed action in transaction: {raw}") from exc


@dataclass
class Transaction:
    """A complete record of one modifying run."""

    operation_id: str
    command: str
    root: Path
    created_at: str
    actions: list[Action] = field(default_factory=list)
    created_dirs: list[str] = field(default_factory=list)
    undone_at: str | None = None
    purged_at: str | None = None

    @property
    def is_undone(self) -> bool:
        return self.undone_at is not None

    @property
    def is_purged(self) -> bool:
        return self.purged_at is not None

    @property
    def can_undo(self) -> bool:
        return not self.is_undone and not self.is_purged

    @property
    def move_count(self) -> int:
        return sum(1 for a in self.actions if a.type is ActionType.MOVE)

    @property
    def quarantine_count(self) -> int:
        return sum(1 for a in self.actions if a.type is ActionType.QUARANTINE)

    @property
    def quarantined_bytes(self) -> int:
        return sum(a.size for a in self.actions if a.type is ActionType.QUARANTINE)

    def to_dict(self) -> dict[str, object]:
        return {
            "operation_id": self.operation_id,
            "command": self.command,
            "root": str(self.root),
            "created_at": self.created_at,
            "undone_at": self.undone_at,
            "purged_at": self.purged_at,
            "created_dirs": self.created_dirs,
            "actions": [a.to_dict() for a in self.actions],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> Transaction:
        try:
            actions = [Action.from_dict(a) for a in raw["actions"]]  # type: ignore[union-attr]
            return cls(
                operation_id=str(raw["operation_id"]),
                command=str(raw["command"]),
                root=Path(str(raw["root"])),
                created_at=str(raw["created_at"]),
                actions=actions,
                created_dirs=[str(d) for d in raw.get("created_dirs", [])],  # type: ignore[union-attr]
                undone_at=str(raw["undone_at"]) if raw.get("undone_at") else None,
                purged_at=str(raw["purged_at"]) if raw.get("purged_at") else None,
            )
        except (KeyError, TypeError) as exc:
            raise TransactionError(f"malformed transaction record: {exc}") from exc


class TransactionStore:
    """Reads and writes transaction records under ``<root>/.file-organiser``."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    @property
    def store_dir(self) -> Path:
        return self.root / STORE_DIRNAME

    @property
    def transactions_dir(self) -> Path:
        return self.store_dir / "transactions"

    @property
    def quarantine_root(self) -> Path:
        return self.store_dir / "quarantine"

    def quarantine_dir_for(self, operation_id: str) -> Path:
        return self.quarantine_root / operation_id

    def next_operation_id(self, *, today: str | None = None) -> str:
        """``YYYY-MM-DD-NNN``, counting up within each day."""
        day = today or datetime.now().strftime("%Y-%m-%d")
        highest = 0
        for existing in self._record_paths():
            match = _ID_PATTERN.match(existing.stem)
            if match and match.group(1) == day:
                highest = max(highest, int(match.group(2)))
        return f"{day}-{highest + 1:03d}"

    def save(self, transaction: Transaction) -> Path:
        self.transactions_dir.mkdir(parents=True, exist_ok=True)
        target = self.transactions_dir / f"{transaction.operation_id}.json"
        payload = json.dumps(transaction.to_dict(), indent=2)
        # Write to a sibling then replace, so an interrupted write cannot leave a
        # half-parsed record where undo expects a valid one.
        staging = target.with_suffix(".json.tmp")
        staging.write_text(payload, encoding="utf-8")
        staging.replace(target)
        return target

    def load(self, operation_id: str) -> Transaction:
        target = self.transactions_dir / f"{operation_id}.json"
        if not target.is_file():
            raise TransactionError(f"no transaction {operation_id} recorded under {self.root}")
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise TransactionError(
                f"transaction {operation_id} is not readable JSON: {exc}"
            ) from exc
        return Transaction.from_dict(raw)

    def list_transactions(self) -> list[Transaction]:
        """Every recorded transaction, newest first."""
        records = []
        for path in self._record_paths():
            try:
                records.append(self.load(path.stem))
            except TransactionError:
                continue
        return sorted(records, key=lambda t: t.operation_id, reverse=True)

    def latest_undoable(self) -> Transaction | None:
        return next((t for t in self.list_transactions() if t.can_undo), None)

    def _record_paths(self) -> list[Path]:
        if not self.transactions_dir.is_dir():
            return []
        return sorted(self.transactions_dir.glob("*.json"))


@dataclass
class UndoReport:
    operation_id: str
    restored: int = 0
    failed: list[tuple[Path, str]] = field(default_factory=list)
    removed_dirs: int = 0

    @property
    def ok(self) -> bool:
        return not self.failed


def undo(transaction: Transaction, store: TransactionStore) -> UndoReport:
    """Replay a transaction backwards, returning every file to where it was.

    Actions are reversed in the opposite order to their application, so a file
    that was moved and then had its old location reused still lands correctly.
    Nothing is overwritten during a restore: if something now occupies an
    original location, that single action is reported as failed and the rest of
    the undo continues.
    """
    if transaction.is_purged:
        raise TransactionError(
            f"{transaction.operation_id} was purged on {transaction.purged_at}; "
            f"its quarantined files are gone and it can no longer be undone"
        )
    if transaction.is_undone:
        raise TransactionError(
            f"{transaction.operation_id} was already undone on {transaction.undone_at}"
        )

    root = transaction.root
    report = UndoReport(operation_id=transaction.operation_id)

    for action in reversed(transaction.actions):
        original = root / action.source
        current = root / action.destination

        if not current.exists():
            report.failed.append((current, "no longer at its recorded location"))
            continue
        if original.exists():
            report.failed.append((original, "something else now occupies this path"))
            continue

        try:
            original.parent.mkdir(parents=True, exist_ok=True)
            current.replace(original)
            report.restored += 1
        except OSError as exc:
            report.failed.append((current, str(exc)))

    report.removed_dirs = _remove_empty_dirs(root, transaction.created_dirs)

    transaction.undone_at = datetime.now().isoformat(timespec="seconds")
    store.save(transaction)
    return report


def _remove_empty_dirs(root: Path, relative_dirs: list[str]) -> int:
    """Remove directories this tool created, deepest first, if now empty."""
    removed = 0
    for relative in sorted(relative_dirs, key=lambda d: d.count("/"), reverse=True):
        candidate = root / relative
        try:
            if candidate.is_dir() and not any(candidate.iterdir()):
                candidate.rmdir()
                removed += 1
        except OSError:
            continue
    return removed
