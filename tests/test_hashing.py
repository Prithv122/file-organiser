from __future__ import annotations

import hashlib
from pathlib import Path

from conftest import write
from file_organiser.hashing import (
    PARTIAL_BYTES,
    full_digest,
    partial_digest,
    verify_still_identical,
)


def test_full_digest_matches_hashlib(tmp_path: Path) -> None:
    payload = b"the quick brown fox" * 100
    target = write(tmp_path / "a.bin", payload)
    assert full_digest(target) == hashlib.sha256(payload).hexdigest()


def test_full_digest_agrees_for_identical_content(tmp_path: Path) -> None:
    a = write(tmp_path / "a.bin", b"same")
    b = write(tmp_path / "b.bin", b"same")
    assert full_digest(a) == full_digest(b)


def test_full_digest_differs_for_different_content(tmp_path: Path) -> None:
    a = write(tmp_path / "a.bin", b"one")
    b = write(tmp_path / "b.bin", b"two")
    assert full_digest(a) != full_digest(b)


def test_full_digest_handles_a_file_larger_than_one_chunk(tmp_path: Path) -> None:
    payload = bytes(2 * 1024 * 1024 + 17)
    target = write(tmp_path / "big.bin", payload)
    assert full_digest(target) == hashlib.sha256(payload).hexdigest()


def test_full_digest_of_empty_file(tmp_path: Path) -> None:
    target = write(tmp_path / "empty.bin", b"")
    assert full_digest(target) == hashlib.sha256(b"").hexdigest()


def test_partial_digest_agrees_for_identical_content(tmp_path: Path) -> None:
    payload = bytes(range(256)) * 100
    a = write(tmp_path / "a.bin", payload)
    b = write(tmp_path / "b.bin", payload)
    assert partial_digest(a) == partial_digest(b)


def test_partial_digest_separates_files_differing_only_in_the_tail(tmp_path: Path) -> None:
    """A head-only sample would collapse these into one bucket."""
    head = b"IDENTICAL-HEADER" * 1000
    a = write(tmp_path / "a.bin", head + b"ENDING-A")
    b = write(tmp_path / "b.bin", head + b"ENDING-B")
    assert partial_digest(a) != partial_digest(b)


def test_partial_digest_separates_files_of_different_length(tmp_path: Path) -> None:
    a = write(tmp_path / "a.bin", b"x" * 10)
    b = write(tmp_path / "b.bin", b"x" * 20)
    assert partial_digest(a) != partial_digest(b)


def test_partial_digest_reads_small_files_whole(tmp_path: Path) -> None:
    """Below the sampling threshold there is no tail to seek to."""
    a = write(tmp_path / "a.bin", b"abc")
    b = write(tmp_path / "b.bin", b"abd")
    assert partial_digest(a) != partial_digest(b)


def test_partial_digest_on_file_straddling_the_sample_boundary(tmp_path: Path) -> None:
    size = PARTIAL_BYTES * 2 + 1
    a = write(tmp_path / "a.bin", b"a" * size)
    b = write(tmp_path / "b.bin", b"a" * (size - 1) + b"b")
    assert partial_digest(a) != partial_digest(b)


class TestVerifyStillIdentical:
    def test_true_for_identical_files(self, tmp_path: Path) -> None:
        a = write(tmp_path / "a.bin", b"payload")
        b = write(tmp_path / "b.bin", b"payload")
        assert verify_still_identical(a, b)

    def test_false_when_content_diverges(self, tmp_path: Path) -> None:
        a = write(tmp_path / "a.bin", b"payload")
        b = write(tmp_path / "b.bin", b"payloaD")
        assert not verify_still_identical(a, b)

    def test_false_when_one_file_changed_after_detection(self, tmp_path: Path) -> None:
        """The TOCTOU guard: a file edited between scan and delete must not be deleted."""
        a = write(tmp_path / "a.bin", b"payload")
        b = write(tmp_path / "b.bin", b"payload")
        assert verify_still_identical(a, b)

        b.write_bytes(b"edited since the scan")
        assert not verify_still_identical(a, b)

    def test_false_when_a_file_has_vanished(self, tmp_path: Path) -> None:
        a = write(tmp_path / "a.bin", b"payload")
        b = write(tmp_path / "b.bin", b"payload")
        b.unlink()
        assert not verify_still_identical(a, b)
