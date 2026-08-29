"""Guardrails: which directories this tool refuses to touch, and what it skips.

Two distinct concepts live here.

``guard_target`` answers "may I operate on this directory at all?" and refuses
system locations, drive roots, and source repositories outright.

``ExclusionMatcher`` answers "should I skip this entry while walking?" and is
what keeps ``.git``, virtualenvs and ``node_modules`` out of results even when
the target itself is legitimate.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

DEFAULT_EXCLUDES: tuple[str, ...] = (
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "site-packages",
    ".idea",
    ".vscode",
    "$RECYCLE.BIN",
    "System Volume Information",
    ".Trash",
    ".DS_Store",
)

# Directory names that must never be reorganised, even when named directly.
NEVER_ORGANISE_NAMES: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        ".venv",
        "venv",
        "site-packages",
        "__pycache__",
        ".tox",
    }
)

_POSIX_PROTECTED = (
    "/",
    "/bin",
    "/sbin",
    "/boot",
    "/dev",
    "/etc",
    "/lib",
    "/lib64",
    "/proc",
    "/sys",
    "/usr",
    "/var",
    "/opt",
    "/System",
    "/Library",
    "/Applications",
    # Deliberately not "/private": on macOS /tmp resolves into it, which would
    # make every temp-directory target look like a system location.
)

_WINDOWS_ENV_VARS = (
    "SystemRoot",
    "windir",
    "ProgramFiles",
    "ProgramFiles(x86)",
    "ProgramW6432",
    "ProgramData",
)


class ProtectedPathError(RuntimeError):
    """Raised when the requested target is too dangerous to operate on."""


def protected_roots() -> list[Path]:
    """System directories that must not be organised, nor anything inside them."""
    roots: list[Path] = []
    if os.name == "nt":
        for var in _WINDOWS_ENV_VARS:
            value = os.environ.get(var)
            if value:
                roots.append(Path(value))
    else:
        roots.extend(Path(candidate) for candidate in _POSIX_PROTECTED)

    resolved: list[Path] = []
    for root in roots:
        try:
            resolved.append(root.resolve())
        except OSError:  # pragma: no cover - unresolvable env var on odd systems
            continue
    return resolved


def exact_only_protected() -> list[Path]:
    """Paths protected as a target themselves, but whose children are fair game.

    The home directory is the motivating case: organising ``~`` directly would
    scatter dotfiles and config directories, but ``~/Downloads`` is the single
    most common legitimate target this tool has.
    """
    try:
        return [Path.home().resolve()]
    except OSError:  # pragma: no cover - no resolvable home on odd systems
        return []


def _is_drive_root(path: Path) -> bool:
    return path == Path(path.anchor) if path.anchor else False


def guard_target(target: Path | str, *, force: bool = False) -> Path:
    """Validate ``target`` as somewhere we may safely operate.

    Returns the resolved path. Raises :class:`ProtectedPathError` when the
    location is a system directory, a drive root, a source repository, or a
    dependency directory -- unless ``force`` is set.
    """
    path = Path(target)
    try:
        resolved = path.resolve()
    except OSError as exc:
        raise ProtectedPathError(f"cannot resolve {path}: {exc}") from exc

    if not resolved.exists():
        raise ProtectedPathError(f"{resolved} does not exist")
    if not resolved.is_dir():
        raise ProtectedPathError(f"{resolved} is not a directory")

    if force:
        return resolved

    if _is_drive_root(resolved):
        raise ProtectedPathError(
            f"{resolved} is a drive root. Refusing to operate on an entire volume. "
            f"Point at a specific folder, or pass --force if you truly mean it."
        )

    if resolved.name in NEVER_ORGANISE_NAMES:
        raise ProtectedPathError(
            f"{resolved} is a '{resolved.name}' directory. Reorganising it would "
            f"corrupt the project that owns it."
        )

    if (resolved / ".git").is_dir():
        raise ProtectedPathError(
            f"{resolved} is the root of a Git repository. Moving its files would "
            f"break the working tree. Pass --force to override."
        )

    for root in exact_only_protected():
        if resolved == root:
            raise ProtectedPathError(
                f"{resolved} is your home directory. Organising it directly would "
                f"scatter dotfiles and config directories. Point at a subfolder "
                f"such as {root / 'Downloads'}, or pass --force."
            )

    for root in protected_roots():
        if resolved == root:
            raise ProtectedPathError(
                f"{resolved} is a protected system location. Operation cancelled."
            )
        if resolved.is_relative_to(root) and root != Path(root.anchor):
            raise ProtectedPathError(
                f"{resolved} lives inside the protected location {root}. Operation cancelled."
            )
        if root.is_relative_to(resolved):
            raise ProtectedPathError(
                f"{resolved} contains the protected location {root}. "
                f"Refusing to operate on it. Point at a narrower folder."
            )

    return resolved


@dataclass(frozen=True)
class ExclusionMatcher:
    """Decides whether a walked entry should be skipped."""

    patterns: tuple[str, ...]

    @classmethod
    def build(
        cls,
        extra: tuple[str, ...] | list[str] | None = None,
        *,
        use_defaults: bool = True,
    ) -> ExclusionMatcher:
        patterns: list[str] = list(DEFAULT_EXCLUDES) if use_defaults else []
        if extra:
            patterns.extend(extra)
        return cls(patterns=tuple(patterns))

    def matches(self, name: str, relative_posix: str = "") -> bool:
        """True when ``name`` (or its path relative to the scan root) is excluded."""
        for pattern in self.patterns:
            if name == pattern or fnmatch(name, pattern):
                return True
            if relative_posix and (
                fnmatch(relative_posix, pattern)
                or any(part == pattern for part in relative_posix.split("/"))
            ):
                return True
        return False
