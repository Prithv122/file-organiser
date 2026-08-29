"""Rendering reports for the terminal.

Output is deliberately plain: no emoji, which mangle on Windows consoles running
a legacy code page. Colour and structure come from Rich, which degrades cleanly
when output is piped to a file.

Wording matters here as much as formatting. An estimate is always labelled as an
estimate, and a figure that has been content-verified says so, because the
difference is the difference between a guess and a fact.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table

from file_organiser.dedupe import DedupeReport
from file_organiser.execute import OperationReport, PurgeReport
from file_organiser.organise import OrganisePlan
from file_organiser.scanning import ScanResult
from file_organiser.transactions import Transaction, UndoReport

_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")


def format_bytes(count: int) -> str:
    """Human-readable size using binary multiples, as Windows Explorer shows them."""
    size = float(count)
    for unit in _UNITS:
        if size < 1024 or unit == _UNITS[-1]:
            if unit == "B":
                return f"{int(size)} B"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"  # pragma: no cover - unreachable, loop always returns


def _timestamp(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).strftime("%b %d, %Y")


def render_scan(result: ScanResult, console: Console) -> None:
    console.print()
    console.print(f"[bold]Scan of[/bold] {result.root}")
    console.print()

    summary = Table.grid(padding=(0, 2))
    summary.add_column(justify="left")
    summary.add_column(justify="right")
    summary.add_row("Files scanned", f"{result.file_count:,}")
    summary.add_row("Total size", format_bytes(result.total_size))
    if result.skipped:
        summary.add_row("Skipped (excluded)", f"{result.skipped:,}")
    console.print(summary)

    if result.entries:
        console.print()
        categories = Table.grid(padding=(0, 2))
        categories.add_column(justify="left")
        categories.add_column(justify="right")
        categories.add_column(justify="right", style="dim")
        sizes = result.size_by_category
        for name, count in result.by_category.most_common():
            categories.add_row(name.capitalize(), f"{count:,}", format_bytes(sizes.get(name, 0)))
        console.print(categories)

    console.print()
    estimate = Table.grid(padding=(0, 2))
    estimate.add_column(justify="left")
    estimate.add_column(justify="right")
    estimate.add_row("Possible duplicates", f"{result.duplicate_candidate_count:,}")
    estimate.add_row("Possible space saved", format_bytes(result.potential_reclaimable))
    console.print(estimate)
    console.print(
        "[dim]Estimated from file sizes only. Run 'dedupe' to verify by content;[/dim]\n"
        "[dim]the verified figure is usually lower.[/dim]"
    )

    _render_errors(result.errors, console)


def render_plan_preview(plan: OrganisePlan, console: Console, *, limit: int = 15) -> None:
    console.print()
    console.print("[bold]Preview of changes[/bold]")
    console.print()

    if plan.is_empty():
        console.print("[green]Nothing to move. Everything is already where it belongs.[/green]")
        return

    console.print(f"{plan.move_count:,} files would be moved\n")
    for move in plan.moves[:limit]:
        source = move.source.relative_to(plan.root).as_posix()
        destination = move.destination.relative_to(plan.root).as_posix()
        marker = " [yellow](renamed: name was taken)[/yellow]" if move.renamed else ""
        if move.replaces_existing:
            marker = " [red](replaces existing; displaced file is quarantined)[/red]"
        console.print(f"  {source}")
        console.print(f"    -> {destination}{marker}")

    remaining = plan.move_count - min(plan.move_count, limit)
    if remaining:
        console.print(f"\n[dim]... and {remaining:,} more[/dim]")


def render_duplicate_groups(
    report: DedupeReport,
    console: Console,
    *,
    root: Path | None = None,
    limit: int = 10,
) -> None:
    console.print()
    if not report.groups:
        console.print("[green]No duplicates found. Every file's contents are unique.[/green]")
        _render_errors(report.errors, console)
        return

    groups = report.sorted_by_impact()
    plural = "group" if len(groups) == 1 else "groups"
    console.print(
        f"[bold]{len(groups):,} duplicate {plural}[/bold] "
        f"({report.duplicate_count:,} redundant copies, "
        f"{format_bytes(report.reclaimable)} recoverable)"
    )

    for index, group in enumerate(groups[:limit], start=1):
        console.print()
        console.print(f"[bold]Duplicate group #{index}[/bold]  ({format_bytes(group.size)} each)")

        console.print("  [green]KEEP[/green]")
        _render_member(group.keeper, console, root)

        console.print(f"  [yellow]DUPLICATES ({len(group.duplicates)})[/yellow]")
        for duplicate in group.duplicates:
            _render_member(duplicate, console, root)

        console.print(f"  [dim]Recovers {format_bytes(group.reclaimable)}[/dim]")

    remaining = len(groups) - min(len(groups), limit)
    if remaining:
        console.print(f"\n[dim]... and {remaining:,} more groups[/dim]")

    console.print()
    stats = report.stats
    console.print(
        f"[dim]Verified by SHA-256. {stats.size_candidates:,} size-matched candidates, "
        f"{stats.fully_hashed:,} fully hashed "
        f"({stats.full_hashes_avoided:,} full reads avoided by the partial tier).[/dim]"
    )
    _render_errors(report.errors, console)


def _render_member(entry, console: Console, root: Path | None = None) -> None:
    """Show a group member, with its location relative to the scan root.

    Absolute paths wrap across several lines in a normal terminal and bury the
    part that actually distinguishes one copy from another.
    """
    location = entry.path.parent
    if root is not None:
        try:
            relative = location.relative_to(root)
            location = f"./{relative.as_posix()}" if relative.parts else "."
        except ValueError:
            pass
    console.print(f"    {entry.path.name}")
    console.print(f"      [dim]Location: {location}[/dim]")
    console.print(f"      [dim]Modified: {_timestamp(entry.mtime)}[/dim]")


def render_operation_report(report: OperationReport, console: Console) -> None:
    console.print()
    heading = "DRY RUN - nothing was changed" if report.dry_run else "OPERATION COMPLETE"
    style = "yellow" if report.dry_run else "green"
    console.print(f"[bold {style}]{heading}[/bold {style}]")
    console.print()

    table = Table.grid(padding=(0, 2))
    table.add_column(justify="left")
    table.add_column(justify="right")

    verb = "would be" if report.dry_run else ""
    if report.command == "organise":
        table.add_row(f"Files moved {verb}".strip(), f"{report.moved:,}")
        table.add_row("Bytes moved", format_bytes(report.bytes_moved))
        table.add_row("Skipped", f"{report.skipped:,}")
        table.add_row("Conflicts handled", f"{report.conflicts:,}")
    if report.quarantined or report.command == "dedupe":
        table.add_row(f"Copies quarantined {verb}".strip(), f"{report.quarantined:,}")
        table.add_row("Space recoverable", format_bytes(report.bytes_quarantined))
    table.add_row("Errors", f"{len(report.errors):,}")
    console.print(table)

    if report.errors:
        _render_errors(report.errors, console)

    if report.dry_run:
        console.print("\n[dim]Re-run with --apply to make these changes.[/dim]")
        return

    if report.bytes_quarantined:
        console.print(
            "\n[dim]Quarantined copies are still on disk and still recoverable, so this "
            "space\nis not free yet. Run 'purge' to reclaim it permanently.[/dim]"
        )

    if report.operation_id:
        console.print(f"\nOperation ID: [bold]{report.operation_id}[/bold]")
        console.print(f"Undo with:    file-organiser undo {report.operation_id}")


def render_undo_report(report: UndoReport, console: Console) -> None:
    console.print()
    if report.ok:
        console.print(f"[bold green]UNDO COMPLETE[/bold green] ({report.operation_id})")
    else:
        console.print(f"[bold yellow]UNDO PARTIALLY COMPLETE[/bold yellow] ({report.operation_id})")
    console.print()
    console.print(f"  Files restored: {report.restored:,}")
    if report.removed_dirs:
        console.print(f"  Empty folders removed: {report.removed_dirs:,}")
    if report.failed:
        console.print(f"  [red]Could not restore: {len(report.failed):,}[/red]")
        for path, reason in report.failed[:10]:
            console.print(f"    [red]{path}[/red] - {reason}")


def render_purge_report(report: PurgeReport, console: Console, *, dry_run: bool) -> None:
    console.print()
    heading = "DRY RUN - nothing was deleted" if dry_run else "PURGE COMPLETE"
    style = "yellow" if dry_run else "green"
    console.print(f"[bold {style}]{heading}[/bold {style}]")
    console.print()
    console.print(f"  Files {'to delete' if dry_run else 'deleted'}: {report.files_removed:,}")
    console.print(
        f"  Space {'to free' if dry_run else 'freed'}: {format_bytes(report.bytes_freed)}"
    )
    if report.operation_ids:
        console.print(f"  Operations: {', '.join(report.operation_ids)}")
    if report.errors:
        _render_errors(report.errors, console)
    if dry_run and report.files_removed:
        console.print(
            "\n[dim]Re-run with --apply to delete permanently. This cannot be undone.[/dim]"
        )


def render_history(transactions: list[Transaction], console: Console) -> None:
    console.print()
    if not transactions:
        console.print("[dim]No operations recorded for this folder.[/dim]")
        return

    table = Table(title=None, show_header=True, header_style="bold")
    table.add_column("Operation ID")
    table.add_column("Command")
    table.add_column("When")
    table.add_column("Moved", justify="right")
    table.add_column("Quarantined", justify="right")
    table.add_column("Status")

    for transaction in transactions:
        if transaction.is_purged:
            status = "[red]purged[/red]"
        elif transaction.is_undone:
            status = "[dim]undone[/dim]"
        else:
            status = "[green]undoable[/green]"
        table.add_row(
            transaction.operation_id,
            transaction.command,
            transaction.created_at.replace("T", " "),
            f"{transaction.move_count:,}",
            f"{transaction.quarantine_count:,}",
            status,
        )
    console.print(table)


def _render_errors(errors: list, console: Console, *, limit: int = 10) -> None:
    if not errors:
        return
    console.print()
    console.print(f"[red]{len(errors):,} path(s) could not be processed:[/red]")
    for item in errors[:limit]:
        path, message = (item.path, item.message) if hasattr(item, "path") else item
        console.print(f"  [red]{path}[/red] - {message}")
    if len(errors) > limit:
        console.print(f"  [dim]... and {len(errors) - limit:,} more[/dim]")
