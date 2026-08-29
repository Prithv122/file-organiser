"""Content hashing, in tiers.

Hashing every file in a large tree is the slow part of duplicate detection, so
the work is staged: cheapest discriminator first, full cryptographic digest only
for files that survive every earlier tier.

    size  ->  partial digest (head + tail)  ->  full SHA-256

The tiers are an optimisation only. A file is never called a duplicate on the
strength of a partial digest -- a full SHA-256 match is always required.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

# Read in 1 MiB blocks: large enough to keep syscall overhead down, small enough
# that a multi-gigabyte file never has to fit in memory.
CHUNK_BYTES = 1024 * 1024

# Head and tail sampled by the partial tier. Two files sharing a format usually
# share a header, so sampling the tail as well avoids collapsing every file of a
# given type into one bucket.
PARTIAL_BYTES = 4096

ALGORITHM = "sha256"


def partial_digest(path: Path, *, sample: int = PARTIAL_BYTES) -> str:
    """Digest a sample of the file: its head, its tail, and its length.

    Cheap enough to run on every size-matched candidate. Files whose partial
    digests differ cannot be identical, so they need no full read.
    """
    digest = hashlib.new(ALGORITHM)
    size = path.stat().st_size
    digest.update(str(size).encode("ascii"))

    with path.open("rb") as handle:
        if size <= sample * 2:
            digest.update(handle.read())
        else:
            digest.update(handle.read(sample))
            handle.seek(-sample, 2)
            digest.update(handle.read(sample))
    return digest.hexdigest()


def full_digest(path: Path, *, chunk: int = CHUNK_BYTES) -> str:
    """Stream the whole file through SHA-256.

    Streamed rather than read whole so that a file larger than available memory
    still hashes successfully.
    """
    digest = hashlib.new(ALGORITHM)
    with path.open("rb") as handle:
        while block := handle.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def verify_still_identical(keeper: Path, victim: Path) -> bool:
    """Re-hash both files immediately before deleting ``victim``.

    Detection and deletion are separate steps, and a file can change in between.
    Re-checking at the moment of deletion closes that window: if anything has
    been modified since the scan, the digests no longer agree and the delete is
    abandoned rather than silently destroying data.
    """
    try:
        if keeper.stat().st_size != victim.stat().st_size:
            return False
        return full_digest(keeper) == full_digest(victim)
    except OSError:
        return False
