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

The module also owns the two things *every* rule shares rather than re-derives: the ``Proposal``
union plus the refusal helper every precondition failure funnels through (``_review``), and the
``RuleRow`` protocol — the shape the engine's registry is a tuple of, so a row that books a
comprobante by its type and side (4.1–4.4, `posting.py`) and a row that books a REP by walking its
related documents (4.5a/4.5b, `reposting.py`) can sit in one ordered tuple and be selected the same
way. Sharing them here is what keeps a rule module a *rule* module: it declares rows and computes
legs, and never re-invents what a refusal looks like.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

from sat_descarga_masiva.contabilidad.classification import Classification
from sat_descarga_masiva.contabilidad.journal import (
    JournalEntryRecord,
    JournalLine,
    ProposedJournalEntry,
)
from sat_descarga_masiva.contabilidad.roles import AccountRole
from sat_descarga_masiva.domain.model.fiscal_document import FiscalDocument
from sat_descarga_masiva.domain.model.perspective import Perspective
from sat_descarga_masiva.domain.model.review import ReviewFlag, ReviewFlagType
from sat_descarga_masiva.domain.model.value_objects import Rfc, Uuid
from sat_descarga_masiva.domain.policy.money import NormalizedAmount


@dataclass(frozen=True)
class RelatedDocument:
    """One document the comprobante under audit states it relates to (§8a:212).

    A credit note is a *commercial correction* of something that already exists, and the CFDI
    states which: `CfdiRelacionados` with `TipoRelacion` 01 carries the UUIDs of the
    documentos it cancels. §8a:212 books the correction against the roles the original used, so a
    rule must read the original's facts — and a rule may read nothing but its context. They
    therefore arrive here, resolved before the rule runs, exactly as `classification` and
    `posted_source_uuids` do:

    - `tipo_relacion` stays the bare source code: 01 is the note this engine books, and any
      other code is a fact a rule must not read as "relates to a credit note";
    - `metodo_pago` is the **original's** `MetodoPago`, never the note's: the credit-note rows
      are selected by how the original was to be paid (PUE/PPD), because that is which receivable
      or payable the note reverses. `None` means the original could not be read — an epistemic
      limit, never a guess;
    - `total` is the original's header `Total`, the only cap a credit note may be held to;
    - `posted` is a ledger fact: at least one `POSTED` entry exists for the original, so the
      receivable or payable the note reverses was ever recorded. A `PROPOSED` refusal or a
      `SKIPPED` row moved nothing and does not count.

    `moneda` is the original's currency, carried because a note may only be netted against an
    original the engine values the same way (§8:193). Every field but `moneda` is a fact the
    rule names; `moneda` is what a later partial-credit allocation will need, and it is `None`
    exactly when the original could not be read.
    """

    uuid: Uuid
    tipo_relacion: str
    metodo_pago: str | None
    total: NormalizedAmount | None
    posted: bool
    moneda: str | None = None


@dataclass(frozen=True)
class PostingContext:
    """One document as the engine sees it: whose books, which document, as whom (§8).

    ``contributor_rfc`` is the managed client the books belong to — §8:194 keeps it in
    the fingerprint because the same CFDI UUID legitimately appears in two clients'
    folders, once EMITIDO and once RECIBIDO, in two different sets of books. The engine
    reads nothing else: no client aggregate, no repository, no clock, no cache. A rule is
    therefore a pure function of the source facts and is replayable from the ledger.

    ``classification`` carries §8a:207's ``ClaveProdServ → AccountingCategory → AccountRole``
    answer for a *received* document, resolved from the client's versioned mapping before the
    rule runs (``AccountMapping.classify``). It is ``None`` for the emitter-side rules, which
    need no product classification, and a `ClassificationRefusal` when the concepts do not
    reduce to one role: the receiving rules consult it, so the role is never guessed inside a
    rule and the mapping stays the one place a chart of accounts is decided.

    ``posted_source_uuids`` is the ledger fact §8's REP rows need and a rule cannot read for
    itself: which of the document's ``DoctoRelacionado`` originals the client's books already
    hold as a ``POSTED`` entry. A REP's cash movement is only a *collection* (or a payment) if
    the receivable (or payable) it settles was ever recorded; when the original is absent, §8
    has the money land on the configured unapplied-payment account with the gap flagged. Like
    ``classification`` this is resolved from the client's own books *before* the rule runs, so
    the rule stays the pure function of its context that makes it replayable.

    ``related_documents`` is the seam the credit-note rows need and a rule cannot read for
    itself: the facts of the documentos the comprobante states it relates to (``CfdiRelacionados``),
    read from the client's own archive and ledger *before* the rule runs. A note selects its row
    by the **original's** ``MetodoPago``, so that fact has to be resolved outside the rule — a rule
    reads nothing but its context, and it may not open a repository to learn what it reverses.
    The tuple is empty for every document that states no relation (§8:179–185), so nothing is
    queried for one that states none.

    ``previous_entries`` is the cancellation seam of §8:194 and nothing else: the journal
    records the client already holds for this source, read from the ledger *before* the rule
    runs. A cancellation reversal is computed from the persisted accounting fact, never
    re-derived from the source document, so the rule has to be handed the history it reverses.
    Only ``POSTED`` records are reversal candidates: a ``PROPOSED`` refusal or a ``SKIPPED`` row
    moved nothing and does not count. The tuple is empty for every document that is not
    cancelled, so nothing is queried for one that is not.

    ``cancellation_effective_on`` is the effective date §8:194 dates the reversal by: the
    ``FechaCancelacion`` the latest metadata observation carries, which is the same fact the
    fiscal-event contract turns into ``FiscalEvent.effective_at``. It is ``None`` whenever the
    source is not cancelled or the observation states no date — and then no reversal is dated at
    all, because §8a:209 forbids dating it by ``retrieved_at``, by the ``Fecha`` of the invoice or
    by today.
    """

    contributor_rfc: Rfc
    document: FiscalDocument
    perspective: Perspective
    classification: Classification | None = None
    posted_source_uuids: frozenset[Uuid] = frozenset()
    related_documents: tuple[RelatedDocument, ...] = ()
    previous_entries: tuple[JournalEntryRecord, ...] = ()
    cancellation_effective_on: date | None = None


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

    ``accounts`` is the one thing a proposal may know and the mapping must not re-decide: a
    reversal of a posted fact reverses the accounts that fact was posted to (§8:194), so the
    historical resolution travels with the proposal instead of being asked of the chart as it
    stands today. Every rule that resolves roles the ordinary way leaves it empty, and a refusal
    then records no new account decision at all.
    """

    flags: tuple[ReviewFlag, ...]
    detail: str
    entry: ProposedJournalEntry | None = None
    #: The accounts this proposal already names (§8:194). Empty for every rule that resolves
    #: roles through the current mapping; non-empty only when the proposal reverses a recorded
    #: fact and has to keep the accounts that fact used.
    accounts: Mapping[AccountRole, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.flags:
            raise ValueError(
                "a review request must name why a human is needed: §8:173's unmet"
                " precondition is a reason, not an empty proposal"
            )
        if not self.detail.strip():
            raise ValueError("a review request must say what it could not decide (§8:173)")


class RuleRow(Protocol):
    """One row of the engine's rule registry: the shape it claims, and how it answers (§8:177).

    The registry is a tuple of these, which is what lets §8's two *kinds* of row be selected
    identically: a row that books a comprobante by its type, side and payment method (4.1–4.4,
    `posting.py`) and one that books a REP by walking the documents each payment states (4.5a/4.5b,
    `reposting.py`) answer the same ``claims``/``propose`` pair and nothing else. The engine
    therefore never asks *which kind* of rule owns a document — only which row claims it — so
    adding a row cannot change how an existing one is chosen.

    ``metodo_pago`` is part of the shape for the same reason it is on both: §8's table is selected
    by (type, side, method), and a REP comprobante carries no header ``MetodoPago`` at all (§8's
    ``P`` rows), which the wildcard ``None`` expresses rather than hides.

    The members are declared as properties because every row is a frozen dataclass: a rule row is
    read-only by construction, so the protocol says "readable" rather than "assignable" and a frozen
    field satisfies it.
    """

    @property
    def rule_id(self) -> str: ...

    @property
    def rule_version(self) -> str: ...

    @property
    def metodo_pago(self) -> str | None: ...

    @property
    def propose(self) -> Callable[[PostingContext], Proposal]: ...

    def claims(self, request: PostingContext) -> bool: ...


#: What a rule (and the selector in `posting.py`) may answer with (§8:173).
Proposal = ProposedJournalEntry | Skip | ReviewRequest

#: Detail sentence for every "the document is not what this rule can book" refusal — the
#: machine-readable part is the flag's *type*, this is the sentence a human reads in the log
#: (§8:167), so a reworded sentence can never change a decision.
_PRECONDITION_DETAIL = (
    "the rule's source preconditions are not met, so no entry is proposed rather than a"
    " partial one (§8:173)"
)


def _flag(flag_type: ReviewFlagType, reason: str) -> ReviewFlag:
    """The one-word flag helper every rule shares, so a reason is written where it is known."""
    return ReviewFlag(flag_type=flag_type, reason=reason)


class EntryKey(Protocol):
    """The slice of a rule row :func:`_review` needs to key the entry it refuses: §8:194's ids.

    Narrower than `RuleRow` on purpose. ``_review`` reads only the rule id and version to build the
    entry's fingerprint, so a case that declines to post — §8a:205's drafts (`drafts.py`) — can key
    its entry without pretending to be a posting row (a REP or `I`/`E` row also claims a shape and
    proposes legs, which a draft never does).
    """

    @property
    def rule_id(self) -> str: ...

    @property
    def rule_version(self) -> str: ...


def _review(
    request: PostingContext,
    row: EntryKey,
    *,
    flags: tuple[ReviewFlag, ...],
    detail: str,
    lines: tuple[JournalLine, ...] = (),
) -> ReviewRequest:
    """A refusal keyed as an entry when the document can be (§8:169/194), else on the document.

    A review fact is recorded against an entry's fingerprint, so it needs the same provenance a
    posting needs: contributor, source, rule and version — and a date (§8a:209) and a TFD UUID
    (§8:194). When either is missing there is no entry to key, and inventing one would be worse
    than flagging the document itself (§8:167); so ``entry`` stays ``None``.

    ``lines`` is what the rule *did* compute before it found the reason it cannot stand behind —
    empty for a precondition that stopped the calculation, and carrying the legs when the
    document's own arithmetic is sound but the treatment is not (§8's REP rows: a payment whose
    original is not in the ledger is recorded against the unapplied-payment account and flagged,
    never silently dropped). Either way the decision is ``PROPOSED``: a rule that asks for review
    has said its calculation is not the whole treatment.
    """
    document = request.document
    entry_date, source_uuid = document.fecha, document.source_uuid
    if entry_date is None or source_uuid is None:
        return ReviewRequest(flags=flags, detail=detail)
    return ReviewRequest(
        entry=ProposedJournalEntry(
            rule_id=row.rule_id,
            rule_version=row.rule_version,
            contributor_rfc=request.contributor_rfc,
            source_uuid=source_uuid,
            source_hash=document.source_hash,
            entry_date=entry_date,
            lines=lines,
        ),
        flags=flags,
        detail=detail,
    )


def _recordability_flags(document: FiscalDocument) -> tuple[ReviewFlag, ...]:
    """Why an entry cannot be keyed: §8a:209's date and §8:194's TFD UUID, each its own reason."""
    flags: list[ReviewFlag] = []
    if document.fecha is None:
        flags.append(
            _flag(
                ReviewFlagType.MISSING_SOURCE_FIELD,
                "no Fecha: an entry must be dated by the document, never by 'today' (§8a:209)",
            )
        )
    if document.source_uuid is None:
        flags.append(
            _flag(
                ReviewFlagType.MISSING_POSTING_IDENTITY,
                "no TFD UUID: §8:194 keys the entry by it, so there is nothing to record",
            )
        )
    return tuple(flags)
