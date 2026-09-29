"""Immutable source identity & incoming-classification (§6). Domain-only, no I/O."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum

from sat_descarga_masiva.domain.errors import SourceIntegrityError


class IncomingClassification(Enum):
    NEW = "new"
    DUPLICATE = "duplicate"
    CONFLICT = "conflict"


def sha256_hex(data: bytes) -> str:
    """Hex SHA-256 of raw bytes — the artifact identity, not a proxy for UUID."""
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class SourceIdentity:
    """Identity of one source artifact (uuid + content hash)."""

    uuid: str
    sha256: str


def classify_incoming(incoming: SourceIdentity, known: dict[str, str]) -> IncomingClassification:
    """Classify against a uuid -> sha256 lookup.

    - unknown uuid            -> NEW
    - same uuid, same hash    -> DUPLICATE  (skip)
    - same uuid, diff hash    -> CONFLICT   (never a silent overwrite -> NEEDS_REVIEW)
    """
    existing = known.get(incoming.uuid)
    if existing is None:
        return IncomingClassification.NEW
    if existing == incoming.sha256:
        return IncomingClassification.DUPLICATE
    return IncomingClassification.CONFLICT


@dataclass(frozen=True)
class ExtractedXml:
    """One XML reproducibly derived from a package.

    sha256 is the per-XML digest — the future `posting_snapshot.source_hash`.
    """

    uuid: str
    tipo: str
    sha256: str


def verify_source_integrity(data: bytes, expected_sha256: str) -> None:
    """Assert stored bytes still hash to the digest the record claims (§6a.2).

    Store integrity only: it proves *our* copy did not change since it was
    recorded, and never claims the CFDI itself is authentic (§6a.3). Callers
    re-read the stored ZIP or extracted XML they are about to trust, so tampering
    surfaces as a failure instead of silently feeding a downstream parse.
    """
    actual = sha256_hex(data)
    if actual != expected_sha256:
        raise SourceIntegrityError(
            f"stored artifact hash mismatch: recorded {expected_sha256}, found {actual}"
        )
