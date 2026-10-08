"""MetadataSnapshot — one timestamped observation of a CFDI's fiscal status (§6/§4). Domain-only.

Metadata is a **historical observation, not a flag** (§6): each snapshot is what the SAT said
about one CFDI at one `retrieved_at`, with the hash that proves what was read. It is the join
partner of the fiscal document (`MetadataSnapshot.uuid` == `FiscalDocument.source_uuid`) and the
only authority from which a document's posted-time status is projected; the `documents` row stays
a mutable projection and is never the source of truth (§4).

Two deliberate choices live in this type:

- ``status`` is ``FiscalDocumentStatus`` (§6a) — the *received* fiscal vocabulary
  (``unknown``/``vigente``/``cancelled``) — and never the query-side ``DocumentStatus`` codes
  (D-M3-7b). The SAT's own words (``'Vigente'``/``'Cancelado'``/``'Todos'``) are mapped to it at
  the infrastructure adapter boundary (§5/§8a:206); a raw word string never reaches the domain.
- the cancellation evidence (``cancellation_date``/``cancellation_reason``/``substitution_uuid``)
  is captured verbatim but interpreted nowhere here. ``substitution_uuid`` is recorded, not netted:
  M3 never auto-offsets a replacement CFDI against the one it replaces — a human reconciles the
  pair. ``cancellation_date`` is the cancellation's effective date, which is also when it enters
  the journal as a 4.15 reversal (§8a:203).

History is append-only and idempotent, keyed by ``(uuid, contributor_rfc, retrieved_at,
source_hash)``: re-observing the identical evidence is a no-op, and a *new* observation is a
new row — a later refresh never rewrites the recorded facts of an earlier one (§4/§6).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from sat_descarga_masiva.domain.model.fiscal_document import FiscalDocumentStatus
from sat_descarga_masiva.domain.model.value_objects import Rfc, Uuid


@dataclass(frozen=True)
class MetadataSnapshot:
    """One CFDI's status as the SAT reported it, at the moment it was read (§6).

    The idempotency key is ``(uuid, contributor_rfc, retrieved_at, source_hash)`` — the fields the
    store's ``UNIQUE`` constraint spans — so identical evidence appends nothing while a refreshed
    observation is a distinct, appended fact. ``cancellation_date``/``_reason``/
    ``substitution_uuid`` are present only when a cancellation states them; a vigente observation
    carries none of them (``None`` is the domain's own value for "not stated", never a default).
    """

    uuid: Uuid
    contributor_rfc: Rfc
    status: FiscalDocumentStatus
    retrieved_at: datetime
    source_hash: str
    #: The cancellation's effective date (§8a:203) — the day a 4.15 reversal would be dated by.
    #: ``None`` for a vigente observation or a cancellation that states no date.
    cancellation_date: date | None = None
    #: The SAT's cancellation motive (a bare source code, e.g. ``01``/``02``/``04``); what it
    #: means for accounting is decided downstream, never here.
    cancellation_reason: str | None = None
    #: The CFDI that replaces this one, when the cancellation states one. Recorded, not netted:
    #: M3 never auto-offsets the pair; a human reconciles it.
    substitution_uuid: Uuid | None = None
