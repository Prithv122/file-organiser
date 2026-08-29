from __future__ import annotations

import os
from pathlib import Path

import pytest

from file_organiser.paths import (
    ExclusionMatcher,
    ProtectedPathError,
    guard_target,
    protected_roots,
)


def test_ordinary_directory_is_allowed(tmp_path: Path) -> None:
    assert guard_target(tmp_path) == tmp_path.resolve()


def test_missing_directory_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ProtectedPathError, match="does not exist"):
        guard_target(tmp_path / "absent")


def test_file_target_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("x", encoding="utf-8")
    with pytest.raises(ProtectedPathError, match="not a directory"):
        guard_target(target)


def test_git_repository_root_is_rejected(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    with pytest.raises(ProtectedPathError, match="Git repository"):
        guard_target(tmp_path)


def test_dependency_directory_is_rejected_by_name(tmp_path: Path) -> None:
    target = tmp_path / "node_modules"
    target.mkdir()
    with pytest.raises(ProtectedPathError, match="node_modules"):
        guard_target(target)


def test_home_directory_itself_is_rejected() -> None:
    with pytest.raises(ProtectedPathError, match="home directory"):
        guard_target(Path.home())


def test_subfolder_of_home_is_allowed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The motivating case: ~/Downloads must not be caught by the home guard."""
    fake_home = tmp_path / "home"
    downloads = fake_home / "Downloads"
    downloads.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    assert guard_target(downloads) == downloads.resolve()


def test_drive_root_is_rejected() -> None:
    root = Path(Path.cwd().anchor)
    with pytest.raises(ProtectedPathError, match=r"drive root|protected|contains"):
        guard_target(root)


def test_system_location_is_rejected() -> None:
    roots = [r for r in protected_roots() if r.is_dir()]
    if not roots:  # pragma: no cover - a machine with no resolvable system dirs
        pytest.skip("no protected system roots resolvable here")
    with pytest.raises(ProtectedPathError, match="protected"):
        guard_target(roots[0])


def test_force_overrides_every_guard(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    assert guard_target(tmp_path, force=True) == tmp_path.resolve()


@pytest.mark.skipif(os.name != "nt", reason="Windows-specific system path")
def test_windows_system_subdirectory_is_rejected() -> None:
    system32 = Path(os.environ["SYSTEMROOT"]) / "System32"
    if not system32.is_dir():  # pragma: no cover - unusual Windows install
        pytest.skip("System32 not present")
    with pytest.raises(ProtectedPathError, match="inside the protected location"):
        guard_target(system32)


class TestExclusionMatcher:
    def test_defaults_exclude_common_dependency_directories(self) -> None:
        matcher = ExclusionMatcher.build()
        assert matcher.matches(".git")
        assert matcher.matches("node_modules")
        assert matcher.matches(".venv")
        assert not matcher.matches("Documents")

    def test_extra_patterns_are_honoured(self) -> None:
        matcher = ExclusionMatcher.build(["Work", "*.tmp"])
        assert matcher.matches("Work")
        assert matcher.matches("scratch.tmp")
        assert not matcher.matches("Play")

    def test_pattern_matches_any_path_component(self) -> None:
        matcher = ExclusionMatcher.build(["Work"])
        assert matcher.matches("report.pdf", "Work/2026/report.pdf")

    def test_glob_matches_against_relative_path(self) -> None:
        matcher = ExclusionMatcher.build(["*.git*"])
        assert matcher.matches("config", ".gitconfig/config")

    def test_defaults_can_be_disabled(self) -> None:
        matcher = ExclusionMatcher.build(["Work"], use_defaults=False)
        assert not matcher.matches(".git")
        assert matcher.matches("Work")
