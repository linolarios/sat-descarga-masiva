"""What a rule reads, and the only two answers it may give (§8:173's rule contract).

A rule is a pure function over a `PostingContext` — one contributor's books, one fiscal
document, one resolved perspective — and it answers with **either**

* a `ProposedJournalEntry` (its calculation of the document's bookkeeping expression), or
* a `Skip`: a zero-line entry plus the declaration that the document is *deliberately
  outside accounting scope* (§8:161).

The distinction is the engine's safety margin, not a convenience. ``POSTED`` is absent
from this module's vocabulary because §8:159 gives that word to the
`PostingEligibilityValidator` alone; `ProposedJournalEntry.posting_state` is derived and
always ``PROPOSED``, so a rule cannot assign a posting state even by mistake. `Skip`
cannot carry legs either — "deliberately out of scope" and "has journal lines" are
contradictory — so the combination is refused when it is constructed rather than
discovered later. And a `Skip` still carries the source provenance the ledger keys on
(§8:169/194): skipping a document is an auditable fact, not a silence.
"""

from __future__ import annotations

from dataclasses import dataclass

from sat_descarga_masiva.contabilidad.journal import ProposedJournalEntry
from sat_descarga_masiva.domain.model.fiscal_document import FiscalDocument
from sat_descarga_masiva.domain.model.perspective import Perspective
from sat_descarga_masiva.domain.model.value_objects import Rfc


@dataclass(frozen=True)
class PostingContext:
    """One document as the engine sees it: whose books, which document, as whom (§8).

    ``contributor_rfc`` is the managed client the books belong to — §8:194 keeps it in
    the fingerprint because the same CFDI UUID legitimately appears in two clients'
    folders, once EMITIDO and once RECIBIDO, in two different sets of books. The engine
    reads nothing else: no client aggregate, no repository, no clock, no cache. A rule is
    therefore a pure function of the source facts and is replayable from the ledger.
    """

    contributor_rfc: Rfc
    document: FiscalDocument
    perspective: Perspective


@dataclass(frozen=True)
class Skip:
    """A rule's declaration that a document is deliberately outside accounting scope.

    ``entry`` is the zero-line ``ProposedJournalEntry`` the posting would have been:
    §8:169 persists ``SKIPPED`` records for audit, and the persisted row is keyed by the
    entry's `PostingFingerprint` (§8:194), so a skip needs exactly the provenance a
    posting has — contributor, source, rule, version, date — with no legs. ``detail`` is
    prose for a human reading a log; the machine-readable reason is the rule id itself
    (§8:161 enumerates exactly three such rules), so nothing is inferred from the
    sentence and a reworded sentence cannot change a decision.
    """

    entry: ProposedJournalEntry
    detail: str

    def __post_init__(self) -> None:
        if self.entry.lines:
            raise ValueError(
                "a skip posts nothing, so its entry cannot have legs:"
                f" {len(self.entry.lines)} given (§8:161)"
            )
        if not self.detail.strip():
            raise ValueError("a skip must say why the document is out of scope (§8:161)")
