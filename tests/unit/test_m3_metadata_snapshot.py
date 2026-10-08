"""M3 step 1: the metadata snapshot — a timestamped observation of a CFDI's status (§6).

**Metadata is a historical observation, not a flag** (§6). These tests pin what that sentence
means at the type level: a snapshot carries the *received* fiscal status (``FiscalDocumentStatus``),
the moment it was read, and the hash that proves what was read — and nothing else the domain is
not allowed to hold. In particular:

- the status is the received fiscal vocabulary, never the SAT query's internal codes (D-M3-7b);
- the cancellation evidence is optional and, when absent, is the domain's own ``None`` — not a
  default standing in for a value that was never stated (§6);
- a substitution UUID is *recorded, not netted* — M3 never auto-offsets the replacement against
  the CFDI it replaces;
- the snapshot is frozen, so a later refresh appends a new observation rather than rewriting the
  recorded facts of an earlier one (§4).
"""

from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime

import pytest

from sat_descarga_masiva.domain.enums.catalog import DocumentStatus
from sat_descarga_masiva.domain.model.fiscal_document import FiscalDocumentStatus
from sat_descarga_masiva.domain.model.metadata_snapshot import MetadataSnapshot
from sat_descarga_masiva.domain.model.value_objects import Rfc, Uuid

RFC = Rfc("AAA010101AAA")
UUID = Uuid("4e80345d-917f-40bb-a98f-4a73939353c5")
SUBSTITUTION_UUID = Uuid("7f1b2c3d-4e5f-4a6b-8c9d-0e1f2a3b4c5d")
RETRIEVED_AT = datetime(2026, 2, 1, 9, 30, tzinfo=UTC)
SOURCE_HASH = "a" * 64
CANCELLATION_DATE = date(2026, 2, 1)


def _vigente() -> MetadataSnapshot:
    return MetadataSnapshot(
        uuid=UUID,
        contributor_rfc=RFC,
        status=FiscalDocumentStatus.VIGENTE,
        retrieved_at=RETRIEVED_AT,
        source_hash=SOURCE_HASH,
    )


def test_a_vigente_observation_carries_no_cancellation_evidence() -> None:
    """§6: an observation states only what the SAT stated; nothing is assumed to be cancelled."""
    snapshot = _vigente()

    assert snapshot.status is FiscalDocumentStatus.VIGENTE
    assert snapshot.cancellation_date is None
    assert snapshot.cancellation_reason is None
    assert snapshot.substitution_uuid is None


def test_a_cancelled_observation_records_what_the_sat_stated() -> None:
    """§6/§8a:203: the effective date and the substitution UUID are captured verbatim."""
    snapshot = MetadataSnapshot(
        uuid=UUID,
        contributor_rfc=RFC,
        status=FiscalDocumentStatus.CANCELLED,
        retrieved_at=RETRIEVED_AT,
        source_hash=SOURCE_HASH,
        cancellation_date=CANCELLATION_DATE,
        cancellation_reason="01",
        substitution_uuid=SUBSTITUTION_UUID,
    )

    assert snapshot.status is FiscalDocumentStatus.CANCELLED
    assert snapshot.cancellation_date == CANCELLATION_DATE
    assert snapshot.cancellation_reason == "01"
    assert snapshot.substitution_uuid == SUBSTITUTION_UUID


def test_the_status_is_the_received_fiscal_vocabulary_not_the_query_codes() -> None:
    """D-M3-7b: the observation speaks ``vigente``/``cancelled``, not the query's codes."""
    snapshot = _vigente()

    assert isinstance(snapshot.status, FiscalDocumentStatus)
    assert not isinstance(snapshot.status, DocumentStatus)
    assert snapshot.status.value == "vigente"


def test_the_snapshot_is_frozen_so_a_refresh_appends_rather_than_rewrites() -> None:
    """§4: history is append-only — a recorded observation cannot be mutated in place."""
    snapshot = _vigente()

    with pytest.raises(FrozenInstanceError):
        snapshot.status = FiscalDocumentStatus.CANCELLED  # type: ignore[misc]


def test_snapshots_are_equal_when_their_evidence_is_equal() -> None:
    """Idempotency key is ``(uuid, contributor, retrieved_at, hash)`` — equal evidence, one fact."""
    assert _vigente() == _vigente()
