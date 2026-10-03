"""ExecuteAccountingUseCase — the M3 accounting stage, one document at a time (§8/§11 M3).

Orchestration only. §8's rules 4.1/4.2/4.3/4.4, §8:161's out-of-scope skips (4.11/4.12/4.14)
and the `PostingEligibilityValidator` already exist and already decide everything; this module
adds no accounting judgment of its own. It *sequences* them for one projected document and
writes the single record the decision produced.

The sequence, and why each step sits where it does:

- **the guard runs first.** A quarantined projection carries no fiscal document (§6), so there
  is nothing to account. That is a caller-wiring error, not a document outcome — no §8:194
  identity exists to hang a review flag on — so it raises instead of producing a verdict nobody
  could act on.
- **the review state is merged, then the context is built.** §6's projection composes flags
  from three producers (parser, perspective, signature) onto the `ProcessedDocument`, while a
  rule reads the document. The additive union is taken here, because §8:159's "no unresolved
  review flags" condition is only honest if M2's flags actually reach it: a document that must
  be reviewed cannot post because a producer's flag was left on the wrong object.
- **the mapping is resolved once.** §8a:207's chart is loaded once per document and handed to
  both the classification and the validator, so a decision is versioned by exactly one mapping
  (§8:194) rather than by two reads that could disagree.
- **classification happens only where it means something.** §8a:207's
  ``ClaveProdServ → AccountingCategory`` answer is a precondition of the *received* rules
  (4.3/4.4), whose base role §8's row does not fix. An emitted document or a non-``I``
  comprobante gets ``None``: asking would classify a purchase that is not being booked.
- **one `propose`, one `decide`.** A rule makes the accounting judgment; only the validator may
  say ``POSTED`` (§8:159). Nothing here re-runs a rule or re-derives a verdict.
- **the clock is read once, and only when something becomes durable.** ``recorded_at`` is when
  the posting became durable (§8:166), so it is read *after* the decision and only for a
  decision that names an entry. A document-level refusal has no posting to date, and inventing
  §8:194's identity for one is the single thing this stage must never do.
- **the second balance gate is here, and it aborts.** §8:158 is checked twice on purpose. The
  validator's check is a review case; this one runs on the way to the ledger, is ``POSTED``-only
  and ``Decimal``-only, and raises `UnbalancedJournalCommit` *before* ``append``.
  ``SKIPPED``/``PROPOSED`` records are exempt — a skip has no legs by construction and a
  refusal is not a commitment, so neither may be blocked or demoted by arithmetic.

What this module deliberately is **not**: it holds no unit of work, no session and no commit.
``JournalEntryStore.append`` is the whole write (§8:166) and the caller owns the transaction
boundary — the same seam M2.8's per-document transaction uses.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

from sat_descarga_masiva.application.ports.accounting import AccountMapping, MappingProvider
from sat_descarga_masiva.application.ports.persistence import JournalEntryStore
from sat_descarga_masiva.application.ports.services import Clock
from sat_descarga_masiva.contabilidad.classification import Classification
from sat_descarga_masiva.contabilidad.journal import JournalEntryRecord, LineSide, PostingState
from sat_descarga_masiva.contabilidad.rules.contract import PostingContext
from sat_descarga_masiva.contabilidad.rules.posting import propose
from sat_descarga_masiva.contabilidad.validator import (
    PostingDecision,
    PostingEligibilityValidator,
)
from sat_descarga_masiva.domain.enums.comprobante import TipoComprobante
from sat_descarga_masiva.domain.errors import UnbalancedJournalCommit
from sat_descarga_masiva.domain.model.fiscal_document import FiscalDocument
from sat_descarga_masiva.domain.model.perspective import Perspective
from sat_descarga_masiva.domain.model.review import ReviewFlags
from sat_descarga_masiva.domain.model.value_objects import Rfc
from sat_descarga_masiva.fiscal.projection import ProcessedDocument


@dataclass(frozen=True)
class AccountingResult:
    """One document's accounting outcome: the verdict, and the record when there was one.

    ``record`` is ``None`` exactly when the decision names no entry — §8:167's document-level
    refusal, where there is no `Fecha` to date by (§8a:209) or no TFD UUID to key by (§8:194).
    A caller can therefore tell "nothing to write" from "wrote something" without re-reading the
    decision, which matters because a *refusal that names an entry is still written*: that
    record is the audit evidence §8:167 asks for.

    There is deliberately no second posting-state vocabulary here. ``decision.posting_state``
    *is* the outcome (§8:166); restating it would only create a word that can drift from the
    engine's.
    """

    decision: PostingDecision
    record: JournalEntryRecord | None = None


class ExecuteAccountingUseCase:
    """Turn one projected document into at most one durable posting (§8/§11 M3)."""

    def __init__(
        self,
        *,
        mapping: MappingProvider,
        validator: PostingEligibilityValidator,
        store: JournalEntryStore,
        clock: Clock,
    ) -> None:
        self._mapping = mapping
        self._validator = validator
        self._store = store
        self._clock = clock

    def execute(self, processed: ProcessedDocument, *, contributor_rfc: Rfc) -> AccountingResult:
        """Account one projected document for ``contributor_rfc``'s books (§8:169/194).

        The whole per-document sequence lives here, in the order the module docstring explains.
        The one branching point is ``decision.entry is None``: a decision that names no entry is
        left to the document's own review state (§8:167) and never reaches the ledger.
        """
        document = _projectable_document(processed)
        merged = replace(document, review_flags=_merged_review_flags(processed, document))
        mapping = self._mapping.mapping_for(contributor_rfc)
        context = _posting_context(processed, merged, contributor_rfc, mapping)
        decision = self._validator.decide(context, propose(context), mapping=mapping)
        if decision.entry is None:
            return AccountingResult(decision=decision)
        record = decision.to_record(recorded_at=self._clock.now())
        _assert_balanced_for_commit(record)
        self._store.append(record)
        return AccountingResult(decision=decision, record=record)


def _projectable_document(processed: ProcessedDocument) -> FiscalDocument:
    """The fiscal facts to account for, or a raise: a quarantined projection is not one (§6).

    §6 quarantines a result whose identity is missing or self-contradictory, and such a result
    may still carry the parsed facts. It was *not* projected, though, so accounting for it would
    book a document the run refused — there is no `documents` row, no review subject and no
    §8:194 identity to attach the outcome to. Refusing here keeps that a loud wiring error
    instead of a quietly unaccountable ledger.
    """
    if processed.is_quarantined or processed.document is None:
        raise ValueError(
            "a quarantined projection was never projectable, so there is nothing to account"
            f" (§6): {processed.quarantine_reason or 'no quarantine reason given'}"
        )
    return processed.document


def _merged_review_flags(processed: ProcessedDocument, document: FiscalDocument) -> ReviewFlags:
    """§6's review state as one additive union: no producer's flag can erase another's.

    The flags M2's three producers (parser, perspective, signature) raise ride on the
    `ProcessedDocument`, while §8:159's "no unresolved review flags" condition is evaluated by
    the validator against the document. Merging here is what keeps that condition honest — a
    document that must be reviewed cannot post because a flag was left on the wrong object.
    """
    return document.review_flags.merged_with(processed.review_flags)


def _posting_context(
    processed: ProcessedDocument,
    document: FiscalDocument,
    contributor_rfc: Rfc,
    mapping: AccountMapping,
) -> PostingContext:
    """The engine's view of one document: whose books, which document, as whom (§8).

    ``document`` is the one already carrying §6's merged review state, so the context the rule
    and the validator both read is the same object and neither can see a different document.
    """
    return PostingContext(
        contributor_rfc=contributor_rfc,
        document=document,
        perspective=processed.perspective,
        classification=_classification_for(processed, document, mapping),
    )


def _classification_for(
    processed: ProcessedDocument, document: FiscalDocument, mapping: AccountMapping
) -> Classification | None:
    """§8a:207's classification answer — only for a received ``I`` comprobante, else ``None``.

    §8's rows 4.3/4.4 are the only ones whose base role is not fixed by the row itself, so they
    are the only ones that need the client's ``ClaveProdServ → AccountingCategory`` table. An
    emitted document's base role comes from §8:179/180, and a non-``I`` comprobante is either a
    skip or an unbuilt row; classifying either would ask a question this engine does not have to
    answer, and could turn an unrelated product into a review reason.
    """
    if processed.perspective is not Perspective.RECIBIDO:
        return None
    if TipoComprobante.of(document.tipo) is not TipoComprobante.INGRESO:
        return None
    return mapping.classify(document)


def _assert_balanced_for_commit(record: JournalEntryRecord) -> None:
    """§8:158's second ``Debe == Haber`` gate, on the way to the ledger: it aborts, never flags.

    The validator already refused an unbalanced *proposal* (``UNBALANCED_ENTRY`` + ``PROPOSED``),
    so reaching here out of balance means a ``POSTED`` record was built that contradicts its own
    verdict — a defect, not a review case. Only ``POSTED`` is gated: a ``SKIPPED`` record has no
    legs by construction (§8:161) and a ``PROPOSED`` one is a refusal, not a commitment (§8:167),
    so neither may be blocked or demoted by arithmetic. The totals are summed as ``Decimal``
    (§12), so the comparison cannot succeed or fail by float rounding.
    """
    if record.posting_state is not PostingState.POSTED:
        return
    debe = _side_total(record, LineSide.DEBE)
    haber = _side_total(record, LineSide.HABER)
    if debe != haber:
        raise UnbalancedJournalCommit(
            f"§8:158: record {record.entry_key} says POSTED but does not balance:"
            f" Debe {debe} != Haber {haber} — the commitment is aborted, not downgraded"
        )


def _side_total(record: JournalEntryRecord, side: LineSide) -> Decimal:
    """One side of a record as ``Decimal`` (§12): no float ever enters the balance check."""
    return sum(
        (line.amount.amount for line in record.lines if line.side is side),
        Decimal("0"),
    )
