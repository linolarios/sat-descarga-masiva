"""Project a `MetadataSnapshot` into the `document_status_changed` fiscal fact (§8a:203).

`fiscal_events` is the authoritative, append-only fiscal history (§4); a `MetadataSnapshot` is
one *observation*. This module is the pure seam between the two: it decides whether an
observation records a **transition between two observed fiscal states**, and if so emits exactly
one `FiscalEvent` of §8a:203's single M3 kind.

Two times, kept apart (§8a:203):

- ``effective_at`` is the snapshot's **fiscal** status date — the ``FechaCancelacion`` of a
  cancellation. It is never the observation instant: a later refresh cannot move when the
  cancellation took effect.
- ``recorded_at`` is when this system observed the fact — the snapshot's ``retrieved_at``.

The producer is deliberately narrow. It can date a transition only when the snapshot carries a
fiscal date, and a `MetadataSnapshot` carries exactly one — ``cancellation_date``. So the
**cancellation** direction is implemented (it is the transition that both carries a date and
needs one for 4.15). The two other cases are *not* guessed at:

- a first observation (``previous is None``) or an ``UNKNOWN`` observation establishes no
  transition and emits nothing — a baseline is not a change;
- a **reinstatement** (``CANCELLED → VIGENTE``) is a real transition whose fiscal date a
  `MetadataSnapshot` does not carry (AGENT.md §8a defines no "vigente-from" date), so it is
  refused loudly rather than dated by the retrieval instant, which §8a:203 forbids.

A substitution (``motivo 01`` + ``TipoRelacion 04``) is **the same transition carried in
``detail_json``**, never a second kind (§8a:203); `detail_json` records the cancellation reason
and substitution UUID as the observation stated them, using the domain's own vocabulary.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from sat_descarga_masiva.domain.model.fiscal_document import FiscalDocumentStatus
from sat_descarga_masiva.domain.model.fiscal_event import FiscalEvent
from sat_descarga_masiva.domain.model.metadata_snapshot import MetadataSnapshot

#: §8a:203's one M3 kind. A substitution is this same transition carried in ``detail_json``.
DOCUMENT_STATUS_CHANGED = "document_status_changed"


def document_status_changed(
    snapshot: MetadataSnapshot,
    *,
    previous: FiscalDocumentStatus | None,
) -> FiscalEvent | None:
    """The `document_status_changed` event ``snapshot`` records, or ``None`` for no transition.

    ``previous`` is the fiscal status the document was last observed to hold (from the prior
    snapshot), or ``None`` when it had none. Only a transition between two *observed* states is
    a fact to append; establishing a baseline (``previous is None``) or observing ``UNKNOWN``
    (no fiscal state stated) is not.
    """
    status = snapshot.status
    if status is previous or status is FiscalDocumentStatus.UNKNOWN:
        return None
    if status is FiscalDocumentStatus.CANCELLED:
        return _cancellation_event(snapshot)
    # A change *to* vigente. From no prior state (or a prior ``UNKNOWN``) it is a baseline, not a
    # transition, so it emits nothing; from a cancellation it is a reinstatement whose effective
    # date the snapshot does not carry — refused, never dated by ``retrieved_at``.
    if previous is FiscalDocumentStatus.CANCELLED:
        raise ValueError(
            "a reinstatement (CANCELLED -> VIGENTE) has no derivable effective date: a "
            "MetadataSnapshot carries no 'vigente from' date, and §8a:203 forbids falling back "
            "to the retrieval instant"
        )
    return None


def _cancellation_event(snapshot: MetadataSnapshot) -> FiscalEvent:
    return FiscalEvent(
        uuid=snapshot.uuid,
        contributor_rfc=snapshot.contributor_rfc,
        kind=DOCUMENT_STATUS_CHANGED,
        effective_at=_cancellation_effective_at(snapshot),
        recorded_at=snapshot.retrieved_at,
        source_hash=snapshot.source_hash,
        detail_json=_detail_json(snapshot),
    )


def _cancellation_effective_at(snapshot: MetadataSnapshot) -> datetime:
    """The cancellation's ``FechaCancelacion`` as an instant (§8a:203); refused if absent."""
    if snapshot.cancellation_date is None:
        raise ValueError(
            "a cancelled observation carries no FechaCancelacion, so the status change has no "
            "effective date (§8a:203: effective_at never falls back to recorded_at)"
        )
    return datetime(
        snapshot.cancellation_date.year,
        snapshot.cancellation_date.month,
        snapshot.cancellation_date.day,
        tzinfo=UTC,
    )


def _detail_json(snapshot: MetadataSnapshot) -> str:
    """The transition's evidence, in the domain's own vocabulary (§8a:203)."""
    return json.dumps(
        {
            "status": snapshot.status.value,
            "cancellation_reason": snapshot.cancellation_reason,
            "substitution_uuid": (
                snapshot.substitution_uuid.value if snapshot.substitution_uuid is not None else None
            ),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
