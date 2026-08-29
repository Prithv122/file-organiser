"""Shared fixtures.

Every test in this suite operates inside pytest's ``tmp_path``. Nothing here
ever touches a real user directory.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def write(path: Path, content: bytes | str = b"") -> Path:
    """Create ``path`` (and parents) with the given contents."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        content = content.encode("utf-8")
    path.write_bytes(content)
    return path


@pytest.fixture
def messy(tmp_path: Path) -> Path:
    """A small, deliberately untidy folder used across the suite.

    Contents by design:
      - three categories (images, documents, code) plus one uncategorised file
      - one true duplicate pair with *different names* in *different folders*
      - one same-size-but-different-content pair, to catch naive size matching
      - one empty file
    """
    root = tmp_path / "messy"

    # Length chosen to be distinct from every other file here, so that the only
    # size collisions in this fixture are the ones the tests are about.
    write(root / "holiday.jpg", b"JPEG-CONTENT-HOLIDAY-PHOTO")
    write(root / "notes.txt", b"some notes")
    write(root / "script.py", b"print('hi')\n")
    write(root / "unknown.qqq", b"mystery")

    # A genuine duplicate: same bytes, different name, different folder.
    write(root / "report.pdf", b"PDF-PAYLOAD-1234567890")
    write(root / "archive" / "report (1).pdf", b"PDF-PAYLOAD-1234567890")

    # Same size, different content: must never be called a duplicate.
    write(root / "decoy_a.bin", b"AAAAAAAAAAAAAAAA")
    write(root / "decoy_b.bin", b"BBBBBBBBBBBBBBBB")

    write(root / "empty.log", b"")

    return root


@pytest.fixture
def supports_symlinks(tmp_path: Path) -> bool:
    """Windows needs developer mode or admin rights to create symlinks."""
    probe = tmp_path / "_symlink_probe"
    target = tmp_path / "_symlink_target"
    target.write_bytes(b"x")
    try:
        probe.symlink_to(target)
    except (OSError, NotImplementedError):
        return False
    probe.unlink()
    target.unlink()
    return True


@pytest.fixture
def supports_hardlinks(tmp_path: Path) -> bool:
    probe = tmp_path / "_hardlink_probe"
    target = tmp_path / "_hardlink_target"
    target.write_bytes(b"x")
    try:
        os.link(target, probe)
    except (OSError, NotImplementedError, AttributeError):
        return False
    probe.unlink()
    target.unlink()
    return True
