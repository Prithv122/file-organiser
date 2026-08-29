"""End-to-end tests driving the real Typer app.

These exercise the same entry point the documented commands use, rather than
calling the library directly -- a project can have a green unit suite and a
broken CLI.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from conftest import write
from file_organiser.cli import app

runner = CliRunner()


def invoke(*args: str):
    return runner.invoke(app, list(args))


def files_under(root: Path) -> set[str]:
    return {
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and ".file-organiser" not in p.parts
    }


class TestScan:
    def test_reports_counts_and_changes_nothing(self, messy: Path) -> None:
        before = files_under(messy)
        result = invoke("scan", str(messy))

        assert result.exit_code == 0
        assert "Files scanned" in result.stdout
        assert files_under(messy) == before

    def test_labels_the_duplicate_figure_as_an_estimate(self, messy: Path) -> None:
        result = invoke("scan", str(messy))
        assert "Estimated from file sizes only" in result.stdout

    def test_missing_directory_exits_nonzero(self, tmp_path: Path) -> None:
        result = invoke("scan", str(tmp_path / "absent"))
        assert result.exit_code == 1


class TestGuardrails:
    def test_refuses_a_git_repository(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        write(tmp_path / "a.txt", b"x")

        result = invoke("organise", str(tmp_path))
        assert result.exit_code == 1
        assert "Git repository" in result.stdout + str(result.output)

    def test_force_overrides_the_guardrail(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        write(tmp_path / "a.txt", b"x")

        result = invoke("organise", str(tmp_path), "--force")
        assert result.exit_code == 0


class TestOrganise:
    def test_dry_run_is_the_default(self, tmp_path: Path) -> None:
        write(tmp_path / "a.jpg", b"IMG")

        result = invoke("organise", str(tmp_path))

        assert result.exit_code == 0
        assert "DRY RUN" in result.stdout
        assert files_under(tmp_path) == {"a.jpg"}

    def test_apply_moves_the_files(self, tmp_path: Path) -> None:
        write(tmp_path / "a.jpg", b"IMG")

        result = invoke("organise", str(tmp_path), "--apply")

        assert result.exit_code == 0
        assert files_under(tmp_path) == {"images/a.jpg"}

    def test_by_date_nests_year_and_month(self, tmp_path: Path) -> None:
        write(tmp_path / "a.jpg", b"IMG")

        invoke("organise", str(tmp_path), "--by-date", "--apply")

        moved = files_under(tmp_path).pop()
        assert moved.startswith("2")  # a year folder, not a category folder
        assert moved.endswith("/a.jpg")

    def test_combined_modes_nest_date_under_category(self, tmp_path: Path) -> None:
        write(tmp_path / "a.jpg", b"IMG")

        invoke("organise", str(tmp_path), "--by-type", "--by-date", "--apply")

        assert files_under(tmp_path).pop().startswith("images/")

    def test_conflicting_file_is_renamed_not_overwritten(self, tmp_path: Path) -> None:
        write(tmp_path / "documents" / "report.pdf", b"ORIGINAL")
        write(tmp_path / "inbox" / "report.pdf", b"DIFFERENT")

        invoke("organise", str(tmp_path), "--apply")

        assert (tmp_path / "documents" / "report.pdf").read_bytes() == b"ORIGINAL"
        assert (tmp_path / "documents" / "report (1).pdf").read_bytes() == b"DIFFERENT"

    def test_exclude_is_honoured(self, tmp_path: Path) -> None:
        write(tmp_path / "a.jpg", b"IMG")
        write(tmp_path / "Work" / "b.jpg", b"WORK")

        invoke("organise", str(tmp_path), "--exclude", "Work", "--apply")

        assert "Work/b.jpg" in files_under(tmp_path)

    def test_config_file_drives_categories(self, tmp_path: Path) -> None:
        write(tmp_path / "a.jpg", b"IMG")
        config = tmp_path.parent / "rules.toml"
        config.write_text('[categories]\nphotos = ["jpg"]\n', encoding="utf-8")

        invoke("organise", str(tmp_path), "--config", str(config), "--apply")

        assert files_under(tmp_path) == {"photos/a.jpg"}

    def test_malformed_config_exits_nonzero(self, tmp_path: Path) -> None:
        write(tmp_path / "a.jpg", b"IMG")
        config = tmp_path.parent / "bad.toml"
        config.write_text("[categories\nbroken", encoding="utf-8")

        result = invoke("organise", str(tmp_path), "--config", str(config))
        assert result.exit_code == 1


class TestDedupe:
    def test_dry_run_is_the_default(self, tmp_path: Path) -> None:
        write(tmp_path / "report.pdf", b"PAYLOAD")
        write(tmp_path / "report (1).pdf", b"PAYLOAD")

        result = invoke("dedupe", str(tmp_path))

        assert "DRY RUN" in result.stdout
        assert len(files_under(tmp_path)) == 2

    def test_apply_quarantines_the_redundant_copy(self, tmp_path: Path) -> None:
        write(tmp_path / "report.pdf", b"PAYLOAD")
        write(tmp_path / "report (1).pdf", b"PAYLOAD")

        result = invoke("dedupe", str(tmp_path), "--apply")

        assert result.exit_code == 0
        assert files_under(tmp_path) == {"report.pdf"}

    def test_says_space_is_not_yet_freed(self, tmp_path: Path) -> None:
        write(tmp_path / "report.pdf", b"PAYLOAD")
        write(tmp_path / "report (1).pdf", b"PAYLOAD")

        result = invoke("dedupe", str(tmp_path), "--apply")
        assert "not free yet" in result.stdout

    def test_same_size_different_content_is_untouched(self, tmp_path: Path) -> None:
        write(tmp_path / "a.bin", b"AAAAAAAA")
        write(tmp_path / "b.bin", b"BBBBBBBB")

        result = invoke("dedupe", str(tmp_path), "--apply")

        assert "No duplicates found" in result.stdout
        assert len(files_under(tmp_path)) == 2


class TestUndo:
    def test_undo_restores_an_organise(self, tmp_path: Path) -> None:
        write(tmp_path / "a.jpg", b"IMG")
        invoke("organise", str(tmp_path), "--apply")
        assert files_under(tmp_path) == {"images/a.jpg"}

        result = invoke("undo", "--root", str(tmp_path))

        assert result.exit_code == 0
        assert files_under(tmp_path) == {"a.jpg"}

    def test_undo_restores_a_dedupe(self, tmp_path: Path) -> None:
        write(tmp_path / "report.pdf", b"PAYLOAD")
        write(tmp_path / "report (1).pdf", b"PAYLOAD")
        invoke("dedupe", str(tmp_path), "--apply")

        invoke("undo", "--root", str(tmp_path))

        assert files_under(tmp_path) == {"report.pdf", "report (1).pdf"}

    def test_undo_by_explicit_operation_id(self, tmp_path: Path) -> None:
        write(tmp_path / "a.jpg", b"IMG")
        applied = invoke("organise", str(tmp_path), "--apply")
        operation_id = _extract_operation_id(applied.stdout)

        result = invoke("undo", operation_id, "--root", str(tmp_path))

        assert result.exit_code == 0
        assert files_under(tmp_path) == {"a.jpg"}

    def test_undo_with_no_history_exits_nonzero(self, tmp_path: Path) -> None:
        result = invoke("undo", "--root", str(tmp_path))
        assert result.exit_code == 1

    def test_undo_twice_exits_nonzero(self, tmp_path: Path) -> None:
        write(tmp_path / "a.jpg", b"IMG")
        applied = invoke("organise", str(tmp_path), "--apply")
        operation_id = _extract_operation_id(applied.stdout)

        invoke("undo", operation_id, "--root", str(tmp_path))
        result = invoke("undo", operation_id, "--root", str(tmp_path))

        assert result.exit_code == 1


class TestHistoryAndPurge:
    def test_history_lists_the_operation(self, tmp_path: Path) -> None:
        write(tmp_path / "a.jpg", b"IMG")
        invoke("organise", str(tmp_path), "--apply")

        result = invoke("history", "--root", str(tmp_path))

        assert result.exit_code == 0
        assert "organise" in result.stdout
        assert "undoable" in result.stdout

    def test_history_is_empty_for_an_untouched_folder(self, tmp_path: Path) -> None:
        result = invoke("history", "--root", str(tmp_path))
        assert "No operations recorded" in result.stdout

    def test_purge_dry_run_keeps_the_files(self, tmp_path: Path) -> None:
        write(tmp_path / "report.pdf", b"PAYLOAD")
        write(tmp_path / "report (1).pdf", b"PAYLOAD")
        invoke("dedupe", str(tmp_path), "--apply")

        result = invoke("purge", "--root", str(tmp_path))

        assert "DRY RUN" in result.stdout
        assert list((tmp_path / ".file-organiser" / "quarantine").rglob("*.pdf"))

    def test_purge_apply_frees_the_space(self, tmp_path: Path) -> None:
        write(tmp_path / "report.pdf", b"PAYLOAD")
        write(tmp_path / "report (1).pdf", b"PAYLOAD")
        invoke("dedupe", str(tmp_path), "--apply")

        result = invoke("purge", "--root", str(tmp_path), "--apply", "--yes")

        assert result.exit_code == 0
        assert not list((tmp_path / ".file-organiser" / "quarantine").rglob("*.pdf"))

    def test_purge_prompt_can_be_declined(self, tmp_path: Path) -> None:
        write(tmp_path / "report.pdf", b"PAYLOAD")
        write(tmp_path / "report (1).pdf", b"PAYLOAD")
        invoke("dedupe", str(tmp_path), "--apply")

        result = runner.invoke(app, ["purge", "--root", str(tmp_path), "--apply"], input="n\n")

        assert "Cancelled" in result.stdout
        assert list((tmp_path / ".file-organiser" / "quarantine").rglob("*.pdf"))

    def test_undo_after_purge_is_refused(self, tmp_path: Path) -> None:
        write(tmp_path / "report.pdf", b"PAYLOAD")
        write(tmp_path / "report (1).pdf", b"PAYLOAD")
        applied = invoke("dedupe", str(tmp_path), "--apply")
        operation_id = _extract_operation_id(applied.stdout)

        invoke("purge", "--root", str(tmp_path), "--apply", "--yes")
        result = invoke("undo", operation_id, "--root", str(tmp_path))

        assert result.exit_code == 1


class TestRoundTrip:
    def test_organise_then_undo_returns_the_tree_exactly(self, messy: Path) -> None:
        before = {
            p.relative_to(messy).as_posix(): p.read_bytes() for p in messy.rglob("*") if p.is_file()
        }

        invoke("organise", str(messy), "--by-type", "--by-date", "--apply")
        assert files_under(messy) != set(before)

        invoke("undo", "--root", str(messy))

        after = {
            p.relative_to(messy).as_posix(): p.read_bytes()
            for p in messy.rglob("*")
            if p.is_file() and ".file-organiser" not in p.parts
        }
        assert after == before


def test_version_reports_the_package_version() -> None:
    result = invoke("version")
    assert result.exit_code == 0
    assert "file-organiser" in result.stdout


def _extract_operation_id(output: str) -> str:
    for line in output.splitlines():
        if "Operation ID" in line:
            return line.split(":", 1)[1].strip()
    raise AssertionError(f"no operation id in output:\n{output}")
