"""M3 step 4: a metadata observation → the `document_status_changed` fiscal fact (§8a:203).

`fiscal_events` is the authoritative append-only fiscal history (§4); a `MetadataSnapshot` is a
single observation. These tests pin the narrow seam between them:

- only a transition between two *observed* fiscal states is a fact; a first observation or an
  ``UNKNOWN`` observation is a baseline, not a change;
- a cancellation is dated by its ``FechaCancelacion`` (``effective_at``), never by the retrieval
  instant (``recorded_at``) — the two times are kept apart (§8a:203);
- the kind is always the single ``document_status_changed``; a substitution rides in
  ``detail_json`` as the same transition's evidence, never a second kind;
- what cannot be dated honestly is refused, not guessed: a cancellation with no date, and a
  reinstatement whose "vigente-from" date a snapshot does not carry, both raise.
"""

import json
from datetime import UTC, date, datetime

import pytest

from sat_descarga_masiva.contabilidad.fiscal_state import (
    DOCUMENT_STATUS_CHANGED,
    document_status_changed,
)
from sat_descarga_masiva.domain.model.fiscal_document import FiscalDocumentStatus
from sat_descarga_masiva.domain.model.metadata_snapshot import MetadataSnapshot
from sat_descarga_masiva.domain.model.value_objects import Rfc, Uuid

RFC = Rfc("AAA010101AAA")
UUID = Uuid("4e80345d-917f-40bb-a98f-4a73939353c5")
SUBSTITUTION_UUID = Uuid("7f1b2c3d-4e5f-4a6b-8c9d-0e1f2a3b4c5d")
SOURCE_HASH = "a" * 64
RETRIEVED_AT = datetime(2026, 2, 10, 9, 30, tzinfo=UTC)
CANCELLATION_DATE = date(2026, 2, 1)


def _snapshot(
    status: FiscalDocumentStatus,
    *,
    cancellation_date: date | None = None,
    cancellation_reason: str | None = None,
    substitution_uuid: Uuid | None = None,
) -> MetadataSnapshot:
    return MetadataSnapshot(
        uuid=UUID,
        contributor_rfc=RFC,
        status=status,
        retrieved_at=RETRIEVED_AT,
        source_hash=SOURCE_HASH,
        cancellation_date=cancellation_date,
        cancellation_reason=cancellation_reason,
        substitution_uuid=substitution_uuid,
    )


def test_a_cancellation_becomes_a_document_status_changed_event() -> None:
    """§8a:203: the transition is the single kind, carrying the observation's provenance."""
    event = document_status_changed(
        _snapshot(FiscalDocumentStatus.CANCELLED, cancellation_date=CANCELLATION_DATE),
        previous=FiscalDocumentStatus.VIGENTE,
    )

    assert event is not None
    assert event.kind == DOCUMENT_STATUS_CHANGED
    assert event.uuid == UUID
    assert event.contributor_rfc == RFC
    assert event.source_hash == SOURCE_HASH
    assert event.recorded_at == RETRIEVED_AT


def test_the_effective_date_is_the_cancellation_date_not_the_retrieval_instant() -> None:
    """§8a:203: ``effective_at`` is the fiscal ``FechaCancelacion``, never ``recorded_at``."""
    event = document_status_changed(
        _snapshot(FiscalDocumentStatus.CANCELLED, cancellation_date=CANCELLATION_DATE),
        previous=FiscalDocumentStatus.VIGENTE,
    )

    assert event is not None
    assert event.effective_at == datetime(2026, 2, 1, tzinfo=UTC)
    assert event.effective_at.date() != event.recorded_at.date()


def test_an_unchanged_status_records_no_event() -> None:
    """§8a:203: only a *transition* between observed states is a fact to append."""
    assert (
        document_status_changed(
            _snapshot(FiscalDocumentStatus.VIGENTE), previous=FiscalDocumentStatus.VIGENTE
        )
        is None
    )
    assert (
        document_status_changed(
            _snapshot(FiscalDocumentStatus.CANCELLED, cancellation_date=CANCELLATION_DATE),
            previous=FiscalDocumentStatus.CANCELLED,
        )
        is None
    )


def test_the_first_vigente_observation_establishes_a_baseline_not_an_event() -> None:
    """§8a:203: from no prior state there is nothing to move from — the status is a baseline."""
    assert document_status_changed(_snapshot(FiscalDocumentStatus.VIGENTE), previous=None) is None


def test_an_unknown_observation_records_nothing() -> None:
    """§6a:206: an ``UNKNOWN`` observation states no fiscal state, so it is not a transition."""
    assert document_status_changed(_snapshot(FiscalDocumentStatus.UNKNOWN), previous=None) is None
    assert (
        document_status_changed(
            _snapshot(FiscalDocumentStatus.UNKNOWN), previous=FiscalDocumentStatus.VIGENTE
        )
        is None
    )


def test_a_reinstatement_is_refused_for_having_no_derivable_date() -> None:
    """§8a:203: a snapshot carries no 'vigente-from' date, so the change is refused, not dated."""
    with pytest.raises(ValueError, match="reinstatement"):
        document_status_changed(
            _snapshot(FiscalDocumentStatus.VIGENTE), previous=FiscalDocumentStatus.CANCELLED
        )


def test_a_cancellation_without_a_date_is_refused_rather_than_dated_by_retrieval() -> None:
    """§8a:203: with no ``FechaCancelacion`` there is no effective date — refuse, never guess."""
    with pytest.raises(ValueError, match="FechaCancelacion"):
        document_status_changed(
            _snapshot(FiscalDocumentStatus.CANCELLED, cancellation_date=None),
            previous=FiscalDocumentStatus.VIGENTE,
        )


def test_the_substitution_evidence_rides_in_detail_json() -> None:
    """§8a:203: a substitution is the same transition's evidence, carried in ``detail_json``."""
    event = document_status_changed(
        _snapshot(
            FiscalDocumentStatus.CANCELLED,
            cancellation_date=CANCELLATION_DATE,
            cancellation_reason="01",
            substitution_uuid=SUBSTITUTION_UUID,
        ),
        previous=FiscalDocumentStatus.VIGENTE,
    )

    assert event is not None
    assert event.detail_json is not None
    assert json.loads(event.detail_json) == {
        "status": "cancelled",
        "cancellation_reason": "01",
        "substitution_uuid": SUBSTITUTION_UUID.value,
    }
