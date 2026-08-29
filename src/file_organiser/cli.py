"""Command-line interface.

Every command that can change the filesystem is a dry run until ``--apply`` is
passed. The dry run and the real run execute the same planning code, so the
preview is not an approximation of what will happen -- it is what will happen.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from file_organiser import __version__
from file_organiser.categories import ConfigError, Ruleset, load_exclusions_from_toml
from file_organiser.dedupe import find_duplicates
from file_organiser.execute import apply_dedupe, apply_organise, purge
from file_organiser.organise import ConflictPolicy, plan_organise
from file_organiser.paths import ProtectedPathError, guard_target
from file_organiser.reporting import (
    render_duplicate_groups,
    render_history,
    render_operation_report,
    render_plan_preview,
    render_purge_report,
    render_scan,
    render_undo_report,
)
from file_organiser.scanning import scan as scan_tree
from file_organiser.transactions import TransactionError, TransactionStore
from file_organiser.transactions import undo as undo_transaction

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help=(
        "Safe file organiser and duplicate finder. "
        "Nothing is changed without --apply, and everything can be undone."
    ),
)
console = Console()
error_console = Console(stderr=True)

PathArg = Annotated[Path, typer.Argument(help="Folder to work on.", show_default=False)]
ExcludeOpt = Annotated[
    list[str] | None,
    typer.Option("--exclude", "-e", help="Name or glob to skip. Repeatable."),
]
ConfigOpt = Annotated[
    Path | None,
    typer.Option("--config", "-c", help="TOML rules file with [categories] and [settings]."),
]
ForceOpt = Annotated[
    bool,
    typer.Option("--force", help="Override the protected-location guardrail. Think first."),
]
ApplyOpt = Annotated[
    bool,
    typer.Option("--apply", help="Actually make the changes. Without this, nothing is written."),
]


def _fail(message: str) -> None:
    error_console.print(f"[bold red]Error:[/bold red] {message}")
    raise typer.Exit(code=1)


def _prepare(
    path: Path,
    config: Path | None,
    exclude: list[str] | None,
    force: bool,
) -> tuple[Path, Ruleset, list[str]]:
    """Validate the target and assemble the ruleset and exclusions."""
    try:
        root = guard_target(path, force=force)
    except ProtectedPathError as exc:
        _fail(str(exc))

    ruleset = Ruleset.default()
    patterns = list(exclude or [])
    if config:
        try:
            ruleset = Ruleset.from_toml(config)
            patterns.extend(load_exclusions_from_toml(config))
        except ConfigError as exc:
            _fail(str(exc))
    return root, ruleset, patterns


@app.command()
def scan(
    path: PathArg,
    exclude: ExcludeOpt = None,
    config: ConfigOpt = None,
    force: ForceOpt = False,
) -> None:
    """Report what is in a folder. Read-only; changes nothing."""
    root, ruleset, patterns = _prepare(path, config, exclude, force)
    render_scan(scan_tree(root, ruleset=ruleset, exclude=patterns), console)


@app.command()
def organise(
    path: PathArg,
    by_type: Annotated[
        bool,
        typer.Option("--by-type", help="Sort into category folders. Default if neither given."),
    ] = False,
    by_date: Annotated[
        bool, typer.Option("--by-date", help="Sort into YYYY/MM-Month folders.")
    ] = False,
    conflict: Annotated[
        ConflictPolicy,
        typer.Option("--conflict", help="What to do when a destination is occupied."),
    ] = ConflictPolicy.RENAME,
    apply: ApplyOpt = False,
    exclude: ExcludeOpt = None,
    config: ConfigOpt = None,
    force: ForceOpt = False,
) -> None:
    """Move files into folders by type, by date, or both."""
    root, ruleset, patterns = _prepare(path, config, exclude, force)

    # Neither flag given means the common case: sort by type.
    if not by_type and not by_date:
        by_type = True

    result = scan_tree(root, ruleset=ruleset, exclude=patterns)
    plan = plan_organise(result, by_type=by_type, by_date=by_date, policy=conflict)

    render_plan_preview(plan, console)
    report = apply_organise(plan, store=TransactionStore(root), dry_run=not apply)
    render_operation_report(report, console)

    if report.errors:
        raise typer.Exit(code=1)


@app.command()
def dedupe(
    path: PathArg,
    apply: ApplyOpt = False,
    exclude: ExcludeOpt = None,
    config: ConfigOpt = None,
    force: ForceOpt = False,
) -> None:
    """Find files with identical contents and quarantine the redundant copies.

    Duplicates are decided by SHA-256, never by name or size. Quarantined copies
    stay on disk and can be restored with 'undo'; 'purge' frees the space.
    """
    root, ruleset, patterns = _prepare(path, config, exclude, force)

    result = scan_tree(root, ruleset=ruleset, exclude=patterns)
    found = find_duplicates(result)

    render_duplicate_groups(found, console, root=root)
    report = apply_dedupe(
        found,
        store=TransactionStore(root),
        root=root,
        dry_run=not apply,
    )
    render_operation_report(report, console)

    if report.errors:
        raise typer.Exit(code=1)


@app.command()
def undo(
    operation_id: Annotated[
        str | None,
        typer.Argument(help="Operation to reverse. Defaults to the most recent undoable one."),
    ] = None,
    root: Annotated[
        Path, typer.Option("--root", "-r", help="Folder whose history to read.")
    ] = Path("."),
) -> None:
    """Reverse a previous operation, restoring files to where they were."""
    store = TransactionStore(root.resolve())

    try:
        if operation_id:
            transaction = store.load(operation_id)
        else:
            latest = store.latest_undoable()
            if latest is None:
                _fail(f"nothing undoable recorded under {store.root}")
            transaction = latest
        report = undo_transaction(transaction, store)
    except TransactionError as exc:
        _fail(str(exc))

    render_undo_report(report, console)
    if not report.ok:
        raise typer.Exit(code=1)


@app.command()
def history(
    root: Annotated[
        Path, typer.Option("--root", "-r", help="Folder whose history to read.")
    ] = Path("."),
) -> None:
    """List recorded operations and whether each can still be undone."""
    render_history(TransactionStore(root.resolve()).list_transactions(), console)


@app.command("purge")
def purge_command(
    operation_id: Annotated[
        str | None,
        typer.Argument(help="Operation to purge. Defaults to every unpurged one."),
    ] = None,
    root: Annotated[
        Path, typer.Option("--root", "-r", help="Folder whose quarantine to empty.")
    ] = Path("."),
    apply: ApplyOpt = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation prompt.")] = False,
) -> None:
    """Permanently delete quarantined files. This is the one thing undo cannot fix."""
    store = TransactionStore(root.resolve())

    try:
        preview = purge(store, operation_id=operation_id, dry_run=True)
    except TransactionError as exc:
        _fail(str(exc))

    if apply and preview.files_removed and not yes:
        render_purge_report(preview, console, dry_run=True)
        confirmed = typer.confirm(
            f"\nPermanently delete {preview.files_removed} file(s)? This cannot be undone.",
        )
        if not confirmed:
            console.print("[yellow]Cancelled. Nothing was deleted.[/yellow]")
            raise typer.Exit(code=0)

    try:
        report = purge(store, operation_id=operation_id, dry_run=not apply)
    except TransactionError as exc:
        _fail(str(exc))

    render_purge_report(report, console, dry_run=not apply)
    if report.errors:
        raise typer.Exit(code=1)


@app.command()
def version() -> None:
    """Print the version and exit."""
    console.print(f"file-organiser {__version__}")


if __name__ == "__main__":  # pragma: no cover
    app()
