"""DocumentRecord — the current `documents` projection (§4). Domain-only.

§4 is explicit: `documents` is "one row per UUID = current identity/projection
only (issuer/receiver/type/version/hashes), **not** a mutable status field".
Fiscal state lives in `fiscal_events` (the single authoritative append-only
history); status observations live in `metadata_snapshots` (M3). So this record
has no status, no cancellation date and no amounts: those are other truths.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sat_descarga_masiva.domain.model.perspective import Perspective
from sat_descarga_masiva.domain.model.value_objects import Rfc, Uuid


@dataclass(frozen=True)
class DocumentRecord:
    """One UUID's current projection for one contributor run.

    Identity is `(uuid, source_hash)`: `uuid` + the extracted-XML SHA-256. A
    re-save of the same identity may only move the *projection* fields
    (`last_seen_at`, `last_run_id`); everything else is the identity of the
    artifact and is write-once (§6: a different hash is `SourceHashConflict`,
    never a silent replacement).
    """

    uuid: Uuid
    contributor_rfc: Rfc
    perspective: Perspective
    tipo: str
    version: str
    source_hash: str
    emisor_rfc: Rfc
    first_seen_at: datetime
    last_seen_at: datetime
    receptor_rfc: Rfc | None = None
    moneda: str | None = None
    last_run_id: str | None = None
