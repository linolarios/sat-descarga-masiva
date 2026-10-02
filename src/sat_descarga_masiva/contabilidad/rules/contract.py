"""What a rule reads, and the only three answers it may give (§8:173's rule contract).

A rule is a pure function over a `PostingContext` — one contributor's books, one fiscal
document, one resolved perspective — and it answers with **exactly one of**

* a `ProposedJournalEntry` (its calculation of the document's bookkeeping expression),
* a `Skip`: a zero-line entry plus the declaration that the document is *deliberately
  outside accounting scope* (§8:161), or
* a `ReviewRequest`: the entry it could not safely post, plus why a human must decide
  (§8:173's "preconditions … unmet ⇒ `NEEDS_REVIEW`").

The three-way split is the engine's safety margin, not a convenience. ``POSTED`` is absent
from this module's vocabulary because §8:159 gives that word to the
`PostingEligibilityValidator` alone; `ProposedJournalEntry.posting_state` is derived and
always ``PROPOSED``, so a rule cannot assign a posting state even by mistake. Nor can a
rule *refuse* by proposing nothing: "deliberately out of scope" (`Skip`) and "cannot safely
determine" (`ReviewRequest`) are different claims with different consequences, and both are
refused when they contradict themselves — a `Skip` cannot carry legs, a `ReviewRequest`
cannot be silent. Each still carries the source provenance the ledger keys on
(§8:169/194): skipping or flagging a document is an auditable fact, not a silence.
"""

from __future__ import annotations

from dataclasses import dataclass

from sat_descarga_masiva.contabilidad.journal import ProposedJournalEntry
from sat_descarga_masiva.domain.model.fiscal_document import FiscalDocument
from sat_descarga_masiva.domain.model.perspective import Perspective
from sat_descarga_masiva.domain.model.review import ReviewFlag
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


@dataclass(frozen=True)
class ReviewRequest:
    """A rule's declaration that §8:173's preconditions are unmet: a human must decide.

    The mirror of :class:`Skip`, and the reason neither ``POSTED`` nor ``NEEDS_REVIEW`` is a
    word this module can say: ``SKIPPED`` asserts *no human decision is needed*, this asserts
    one is, and §8:159 gives both posting words to the `PostingEligibilityValidator`. A rule
    that cannot compute therefore *proposes and says so* — it never returns a silent empty
    entry, because §8:173's unmet precondition is a review fact with a reason, not a blank.

    ``flags`` is non-empty by construction: a review request that cannot name why would be
    indistinguishable from a rule that simply stopped. Each flag carries its own ``reason``,
    the specific sentence a human reads — the flag *type* is the machine-readable bit, so a
    reworded reason can never change a decision (§8:167).

    ``entry`` is the entry the rule could not safely post, and may carry legs: §8a:212's
    credit note is exactly "propose the commercial correction, flag the IVA reversal". It is
    ``None`` only when the document itself cannot be keyed — §8:194 keys an entry by its
    source UUID and §8a:209 forbids inventing a date — so an undatable or unidentified
    document is flagged on the *document* rather than on an entry that could not exist.
    """

    flags: tuple[ReviewFlag, ...]
    detail: str
    entry: ProposedJournalEntry | None = None

    def __post_init__(self) -> None:
        if not self.flags:
            raise ValueError(
                "a review request must name why a human is needed: §8:173's unmet"
                " precondition is a reason, not an empty proposal"
            )
        if not self.detail.strip():
            raise ValueError("a review request must say what it could not decide (§8:173)")
