"""Immutable source identity & incoming-classification (§6). Domain-only, no I/O."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum


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
