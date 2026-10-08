"""M3: the metadata-snapshot store contract — an observation is recorded once and read back (§6).

Behavioral assertions are shared, so ``InMemoryMetadataSnapshotStore`` and
``SqliteMetadataSnapshotStore`` cannot diverge. Metadata is a **historical observation, not a
flag** (§6), so this suite pins three truths:

- what is stored is exactly what was recorded: the status (as a ``FiscalDocumentStatus``, never a
  word — the vocabulary mapping lives in the SQLite adapter, §8a:206), the three cancellation
  fields, the retrieval instant and the observation hash all round-trip;
- the history is append-only and idempotent, keyed by
  ``(uuid, contributor_rfc, retrieved_at, source_hash)``: identical evidence appends nothing, and a
  refreshed observation is a *new* fact rather than a rewrite of the old one;
- ``latest_for`` answers the current status: the observation with the greatest ``retrieved_at``
  (never insertion order), scoped to one contributor so one client's read cannot see another's.
"""

import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import cast

import pytest

from sat_descarga_masiva.application.ports.persistence import MetadataSnapshotStore
from sat_descarga_masiva.domain.model.fiscal_document import FiscalDocumentStatus
from sat_descarga_masiva.domain.model.metadata_snapshot import MetadataSnapshot
from sat_descarga_masiva.domain.model.value_objects import Rfc, Uuid
from sat_descarga_masiva.infrastructure.persistence.memory import (
    InMemoryMetadataSnapshotStore,
)
from sat_descarga_masiva.infrastructure.persistence.sqlite import (
    SqliteMetadataSnapshotStore,
    init_schema,
)

RFC = Rfc("AAA010101AAA")
OTHER_RFC = Rfc("BBB010101BBB")
UUID = Uuid("4e80345d-917f-40bb-a98f-4a73939353c5")
OTHER_UUID = Uuid("5e80345d-917f-40bb-a98f-4a73939353c5")
SUBSTITUTION_UUID = Uuid("7f1b2c3d-4e5f-4a6b-8c9d-0e1f2a3b4c5d")
SOURCE_HASH = "a" * 64
OTHER_HASH = "b" * 64
WHEN = datetime(2026, 2, 1, 9, 30, tzinfo=UTC)
LATER = datetime(2026, 2, 5, 9, 30, tzinfo=UTC)
CANCELLATION_DATE = date(2026, 2, 1)


@dataclass(frozen=True)
class Stores:
    """The store under test, typed as the port, so both adapters answer the same protocol."""

    metadata: MetadataSnapshotStore


def _memory_store() -> Stores:
    return Stores(metadata=InMemoryMetadataSnapshotStore())


def _sqlite_store() -> Stores:
    return Stores(metadata=SqliteMetadataSnapshotStore(_conn()))


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    return conn


@pytest.fixture(
    params=[
        pytest.param(_memory_store, id="in-memory"),
        pytest.param(_sqlite_store, id="sqlite"),
    ]
)
def stores(request: pytest.FixtureRequest) -> Stores:
    return cast(Stores, request.param())


def _cancelled(
    *, retrieved_at: datetime = WHEN, source_hash: str = SOURCE_HASH
) -> MetadataSnapshot:
    return MetadataSnapshot(
        uuid=UUID,
        contributor_rfc=RFC,
        status=FiscalDocumentStatus.CANCELLED,
        retrieved_at=retrieved_at,
        source_hash=source_hash,
        cancellation_date=CANCELLATION_DATE,
        cancellation_reason="01",
        substitution_uuid=SUBSTITUTION_UUID,
    )


def _vigente(*, retrieved_at: datetime = WHEN, source_hash: str = SOURCE_HASH) -> MetadataSnapshot:
    return MetadataSnapshot(
        uuid=UUID,
        contributor_rfc=RFC,
        status=FiscalDocumentStatus.VIGENTE,
        retrieved_at=retrieved_at,
        source_hash=source_hash,
    )


def test_a_snapshot_is_read_back_exactly_as_recorded(stores: Stores) -> None:
    """§6: the status, the cancellation evidence, the instant and the hash all round-trip."""
    snapshot = _cancelled()
    stores.metadata.append(snapshot)

    assert stores.metadata.latest_for(RFC, UUID) == snapshot


def test_an_absent_observation_reads_as_none(stores: Stores) -> None:
    """§6/§8a:206: with no observation there is no status to read — the domain's own ``None``."""
    assert stores.metadata.latest_for(RFC, UUID) is None


def test_re_observing_the_same_evidence_is_an_idempotent_no_op(stores: Stores) -> None:
    """§6: keyed by ``(uuid, contributor, retrieved_at, hash)``, so a re-read appends nothing."""
    stores.metadata.append(_cancelled())
    stores.metadata.append(_cancelled())

    assert stores.metadata.latest_for(RFC, UUID) == _cancelled()


def test_a_refreshed_observation_is_kept_beside_the_earlier_one(stores: Stores) -> None:
    """§4: history is append-only — a later observation is a new fact, not a rewrite."""
    stores.metadata.append(_vigente(retrieved_at=WHEN))
    stores.metadata.append(_cancelled(retrieved_at=LATER, source_hash=OTHER_HASH))

    assert stores.metadata.latest_for(RFC, UUID) == _cancelled(
        retrieved_at=LATER, source_hash=OTHER_HASH
    )


def test_the_latest_observation_is_the_newest_by_retrieved_at_not_insertion(stores: Stores) -> None:
    """§6: the current status is the most recently *observed* one, whatever order it was stored."""
    stores.metadata.append(_cancelled(retrieved_at=LATER, source_hash=OTHER_HASH))
    stores.metadata.append(_vigente(retrieved_at=WHEN))

    assert stores.metadata.latest_for(RFC, UUID) == _cancelled(
        retrieved_at=LATER, source_hash=OTHER_HASH
    )


def test_one_contributor_cannot_see_anothers_observation(stores: Stores) -> None:
    """§8: the same CFDI UUID is observed separately per contributor, so the reads stay separate."""
    stores.metadata.append(_vigente())

    assert stores.metadata.latest_for(OTHER_RFC, UUID) is None
    assert stores.metadata.latest_for(RFC, OTHER_UUID) is None
