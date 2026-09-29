"""FiscalEvent — the append-only authoritative fiscal history (§4). Domain-only.

§4: `fiscal_events` is "the single authoritative append-only history from which
current fiscal state is projected"; `documents` and any cached state are
projections, never the authority. *Append-only event history, not full
event-sourcing/replay.*

`kind` is deliberately a bare `str`: AGENT.md §4 defines the table and its
authority but does **not** enumerate an event vocabulary, so no constants are
minted here. A producer must have a specified kind before it can write one.

Two times, never one:
- ``effective_at`` — when the fact applies in the fiscal world (a cancellation
  date, a "vigente from" date). Historical: a later refresh never rewrites it.
- ``recorded_at`` — when this system observed/recorded the fact; history order.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sat_descarga_masiva.domain.model.value_objects import Rfc, Uuid


@dataclass(frozen=True)
class FiscalEvent:
    """One immutable fiscal fact about one document, in append order.

    Idempotency key: ``(uuid, contributor_rfc, kind, effective_at, source_hash)``
    — re-recording the identical fact is a no-op (the row is never rewritten), so
    a later ``recorded_at`` cannot revise the recorded ``effective_at``.
    """

    uuid: Uuid
    contributor_rfc: Rfc
    kind: str
    effective_at: datetime
    recorded_at: datetime
    source_hash: str
    detail_json: str | None = None
