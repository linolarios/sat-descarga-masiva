"""ExecuteAccountingUseCase — the M3 accounting stage, one document at a time (§8/§11 M3).

Orchestration only. §8's rules 4.1/4.2/4.3/4.4, §8:161's out-of-scope skips (4.11/4.12/4.14),
§8:194's cancellation rows (4.15a/4.15b) and the `PostingEligibilityValidator` already exist and
already decide everything; this module adds no accounting judgment of its own. It *sequences* them
for one projected document and writes the single record the decision produced.

The sequence, and why each step sits where it does:

- **the guard runs first.** A quarantined projection carries no fiscal document (§6), so there
  is nothing to account. That is a caller-wiring error, not a document outcome — no §8:194
  identity exists to hang a review flag on — so it raises instead of producing a verdict nobody
  could act on.
- **the source state is resolved from its metadata observation.** M2.6 leaves the fiscal status
  ``UNKNOWN`` (no metadata was read), and §8:159 refuses exactly that. So the document's latest
  `MetadataSnapshot` is joined in here — a VIGENTE observation lets it post, a CANCELLED one
  refuses it (§6:123). With no store wired, or no observation for the document, the status is
  left ``UNKNOWN`` and nothing is posted on a presumption (§5/§6).
- **the review state is merged, then the context is built.** §6's projection composes flags
  from three producers (parser, perspective, signature) onto the `ProcessedDocument`, while a
  rule reads the document. The additive union is taken here, because §8:159's "no unresolved
  review flags" condition is only honest if M2's flags actually reach it: a document that must
  be reviewed cannot post because a producer's flag was left on the wrong object.
- **the mapping is resolved once.** §8a:207's chart is loaded once per document and handed to
  both the classification and the validator, so a decision is versioned by exactly one mapping
  (§8:194) rather than by two reads that could disagree.
- **the ledger is read, once, for the one rule that needs it.** §8:192's REP rows have to know
  whether each related document's original was ever booked, and a rule may read nothing but its
  context — so the *presence* is resolved here, through the store's own reader, and carried as
  `PostingContext.posted_source_uuids`. Only a `POSTED` record counts: a `PROPOSED` refusal or a
  `SKIPPED` row moved nothing, so a collection against it would have no receivable to settle. The
  set is built per document and only when the document actually states related documents, so an
  ordinary comprobante costs no query at all.
- **the cancellation history is read, and only for a cancellation.** §8:194's 4.15 rows need the
  client's own history for the source before they can choose between a `SKIPPED` row and a
  compensating reversal, so the store is asked for that source's records — once, and only when the
  latest observation is `CANCELLED`. No line, side or account is computed here: the reversal is the
  rule's arithmetic, computed from the persisted fact.
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
from datetime import date
from decimal import Decimal

from sat_descarga_masiva.application.ports.accounting import AccountMapping, MappingProvider
from sat_descarga_masiva.application.ports.fiscal_parser import FiscalXmlParser
from sat_descarga_masiva.application.ports.persistence import (
    DocumentRepository,
    JournalEntryStore,
    MetadataSnapshotStore,
)
from sat_descarga_masiva.application.ports.services import Clock
from sat_descarga_masiva.application.ports.source import ExtractedXmlReader
from sat_descarga_masiva.contabilidad.classification import Classification
from sat_descarga_masiva.contabilidad.fx import FXRateResolutionPolicy
from sat_descarga_masiva.contabilidad.journal import (
    JournalEntryRecord,
    LineSide,
    PostingState,
    ProposedJournalEntry,
)
from sat_descarga_masiva.contabilidad.rules.contract import PostingContext, RelatedDocument
from sat_descarga_masiva.contabilidad.rules.posting import propose
from sat_descarga_masiva.contabilidad.validator import (
    PostingDecision,
    PostingEligibilityValidator,
)
from sat_descarga_masiva.contabilidad.valuation import value_entry
from sat_descarga_masiva.domain.enums.comprobante import TipoComprobante
from sat_descarga_masiva.domain.errors import UnbalancedJournalCommit
from sat_descarga_masiva.domain.model.fiscal_document import FiscalDocument, FiscalDocumentStatus
from sat_descarga_masiva.domain.model.perspective import Perspective
from sat_descarga_masiva.domain.model.review import ReviewFlags
from sat_descarga_masiva.domain.model.source import ExtractedXml
from sat_descarga_masiva.domain.model.value_objects import Rfc, Uuid
from sat_descarga_masiva.domain.policy.money import NormalizedAmount
from sat_descarga_masiva.fiscal.parse import build_fiscal_document
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

    def __init__(  # noqa: PLR0913 (each collaborator is a real boundary)
        self,
        *,
        mapping: MappingProvider,
        validator: PostingEligibilityValidator,
        store: JournalEntryStore,
        clock: Clock,
        snapshots: MetadataSnapshotStore | None = None,
        documents: DocumentRepository | None = None,
        xml_reader: ExtractedXmlReader | None = None,
        parser: FiscalXmlParser | None = None,
        fx: FXRateResolutionPolicy | None = None,
    ) -> None:
        self._mapping = mapping
        self._validator = validator
        self._store = store
        self._clock = clock
        self._snapshots = snapshots
        #: §8a:226's chain. Without an injected policy the source document's own `TipoCambio`
        #: is the only rung — truthful, because no configured provider was approved or wired,
        #: and an absent rate is then `AMBIGUOUS_FX` rather than a guessed conversion.
        self._fx = fx if fx is not None else FXRateResolutionPolicy()
        self._sources = _OriginalSources(documents, xml_reader, parser)

    def execute(self, processed: ProcessedDocument, *, contributor_rfc: Rfc) -> AccountingResult:
        """Account one projected document for ``contributor_rfc``'s books (§8:169/194).

        The whole per-document sequence lives here, in the order the module docstring explains.
        The one branching point is ``decision.entry is None``: a decision that names no entry is
        left to the document's own review state (§8:167) and never reaches the ledger.
        """
        document = _projectable_document(processed)
        fiscal = _with_resolved_status(document, contributor_rfc, self._snapshots)
        document = fiscal.document
        merged = replace(document, review_flags=_merged_review_flags(processed, document))
        mapping = self._mapping.mapping_for(contributor_rfc)
        context = _posting_context(
            processed,
            merged,
            contributor_rfc,
            mapping,
            self._store,
            related=_related_documents(document, contributor_rfc, self._store, self._sources),
            cancellation_effective_on=fiscal.cancellation_effective_on,
        )
        # §8a:226 — the conversion is resolved once, before the gate, for a proposal that could
        # become a posting. A skip or a rule's own refusal posts nothing, so no rate is asked
        # for a document whose treatment is already `PROPOSED`.
        proposal = propose(context)
        valued = (
            value_entry(proposal, document=merged, policy=self._fx)
            if isinstance(proposal, ProposedJournalEntry)
            else None
        )
        decision = self._validator.decide(context, proposal, mapping=mapping, valued=valued)
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


@dataclass(frozen=True)
class _FiscalState:
    """One document as the ledger sees it, plus §8:194's cancellation date when there is one.

    ``cancellation_effective_on`` is the latest *cancelled* observation's ``FechaCancelacion`` —
    the same fact `contabilidad.fiscal_state` turns into ``FiscalEvent.effective_at`` — and never
    the retrieval instant, the invoice's ``Fecha`` or today (§8a:203/209). It stays `None` for
    every other observation, so no rule can date a reversal by a guess.
    """

    document: FiscalDocument
    cancellation_effective_on: date | None = None


def _with_resolved_status(
    document: FiscalDocument, contributor_rfc: Rfc, snapshots: MetadataSnapshotStore | None
) -> _FiscalState:
    """Resolve the document's fiscal status from its latest metadata observation (§6:123).

    M2.6 leaves a projected document ``UNKNOWN`` — no metadata has been read yet — and §8:159
    refuses exactly that. The accounting stage is where the observation is joined in: the
    document is booked against whatever the SAT last said about it, so a VIGENTE observation lets
    it post while a CANCELLED one hands the decision to §8:194's rows. With no store wired, or no
    observation for this document, the status is left untouched, so an unresolved document stays
    ``UNKNOWN`` and is never posted on a presumption (never guess, §5/§6). The *latest*
    observation is the authority (§6), so it always wins — including over M2.6's ``UNKNOWN``.

    The cancellation date travels beside the document rather than inside it: §8:194's reversal is
    dated by the cancellation's effective date, and the document's own ``fecha`` is the invoice's.
    """
    if snapshots is None or document.source_uuid is None:
        return _FiscalState(document)
    snapshot = snapshots.latest_for(contributor_rfc, document.source_uuid)
    if snapshot is None:
        return _FiscalState(document)
    resolved = replace(document, status=snapshot.status)
    if snapshot.status is not FiscalDocumentStatus.CANCELLED:
        return _FiscalState(resolved)
    return _FiscalState(resolved, cancellation_effective_on=snapshot.cancellation_date)


def _merged_review_flags(processed: ProcessedDocument, document: FiscalDocument) -> ReviewFlags:
    """§6's review state as one additive union: no producer's flag can erase another's.

    The flags M2's three producers (parser, perspective, signature) raise ride on the
    `ProcessedDocument`, while §8:159's "no unresolved review flags" condition is evaluated by
    the validator against the document. Merging here is what keeps that condition honest — a
    document that must be reviewed cannot post because a flag was left on the wrong object.
    """
    return document.review_flags.merged_with(processed.review_flags)


def _posting_context(  # noqa: PLR0913, PLR0917 (the context is assembled from these facts)
    processed: ProcessedDocument,
    document: FiscalDocument,
    contributor_rfc: Rfc,
    mapping: AccountMapping,
    store: JournalEntryStore,
    related: tuple[RelatedDocument, ...],
    cancellation_effective_on: date | None = None,
) -> PostingContext:
    """The engine's view of one document: whose books, which document, as whom (§8).

    ``document`` is the one already carrying §6's merged review state, so the context the rule
    and the validator both read is the same object and neither can see a different document.

    ``previous_entries`` is §8:194's cancellation seam: the client's own accounting history for
    this source, read once here because a rule may read nothing but its context. It is populated
    only for a *cancelled* source — nothing is queried for a document the SAT has not cancelled —
    and it is handed over whole: which records are reversal candidates (``POSTED`` only, at most
    one) is accounting policy and belongs to the rule.
    """
    return PostingContext(
        contributor_rfc=contributor_rfc,
        document=document,
        perspective=processed.perspective,
        classification=_classification_for(processed, document, mapping),
        posted_source_uuids=_posted_source_uuids(document, contributor_rfc, store),
        related_documents=related,
        previous_entries=_previous_entries(document, contributor_rfc, store),
        cancellation_effective_on=cancellation_effective_on,
    )


def _previous_entries(
    document: FiscalDocument, contributor_rfc: Rfc, store: JournalEntryStore
) -> tuple[JournalEntryRecord, ...]:
    """§8:194's history: every record the client's books hold for this source, or none.

    A reversal is computed from the persisted accounting fact, so the rule has to be handed the
    history — but only a cancelled source needs it, and asking for one that is merely ``VIGENTE``
    (or ``UNKNOWN``) would cost a query per document for an answer no rule reads.

    No filtering happens here: the store's reader answers by source, and the rule decides which
    states are reversal candidates (§8:194) — the seam resolves *facts*, the rule makes the
    *accounting* judgment.
    """
    if document.status is not FiscalDocumentStatus.CANCELLED or document.source_uuid is None:
        return ()
    return store.for_source(contributor_rfc, document.source_uuid)


@dataclass(frozen=True)
class _OriginalSources:
    """The three ports an original is re-read through, each `None` when unwired (§8a:212).

    They travel together because an original can be read only when all three are wired: a caller
    that supplies two of them has not made the seam work, it has made it fail somewhere nobody
    would look. Grouping them is also what keeps the assembly to one argument per fact, so the
    signature of a helper still says what it needs rather than how it happens to get it.
    """

    documents: DocumentRepository | None = None
    xml_reader: ExtractedXmlReader | None = None
    parser: FiscalXmlParser | None = None


def _related_documents(
    document: FiscalDocument,
    contributor_rfc: Rfc,
    store: JournalEntryStore,
    sources: _OriginalSources,
) -> tuple[RelatedDocument, ...]:
    """The documentos this comprobante relates to, resolved from the client archive.

    §8a:212 credit notes mirror the roles of the original, so the facts a rule needs from it —
    its ``MetodoPago``, its header ``Total`` and whether the books ever posted it — are
    assembled here, once per document, and carried on `PostingContext.related_documents`: a rule
    reads nothing but its context, so this is the only place an original can be read at all.

    Three properties make the seam safe to add to every document:

    - **nothing is read when nothing is stated.** A comprobante with no `CfdiRelacionados`
      gets `()` and costs no query, so §8:179–185 behaves exactly as before;
    - **never raise, never guess.** A missing row, an unwired port or an unreadable artifact is a
      *document-level* condition, not a wiring error: the original is recorded with
      `metodo_pago=None`/`total=None` and the rule reviews it, because losing a whole run to
      one unreadable XML would be worse than one honest review;
    - **the ledger fact is POSTED-only.** `posted` is true when a `POSTED` record exists for
      the original: a `PROPOSED` refusal or a `SKIPPED` row moved nothing to reverse.
    """
    if not document.cfdi_relacionados:
        return ()
    return tuple(
        _related_document(
            tipo_relacion,
            uuid,
            contributor_rfc,
            store,
            sources,
        )
        for relacion in document.cfdi_relacionados
        for uuid in relacion.uuids
        for tipo_relacion in (relacion.tipo_relacion,)
    )


def _related_document(
    tipo_relacion: str,
    uuid: Uuid,
    contributor_rfc: Rfc,
    store: JournalEntryStore,
    sources: _OriginalSources,
) -> RelatedDocument:
    """One related document's facts, or the same record with them left unknown.

    ``metodo_pago``/``total`` stay `None` whenever the original cannot be re-read: no repository
    wired, no row for the UUID, an artifact that fails its own integrity check, a parse that
    fails, or a projection with no document. Each is a reason for the rule to review (§8a:212),
    never a reason to crash the stage or to presume what the original said.
    """
    posted = any(
        record.posting_state is PostingState.POSTED
        for record in store.for_source(contributor_rfc, uuid)
    )
    metodo_pago, total, moneda = _original_facts(
        uuid, sources.documents, sources.xml_reader, sources.parser
    )
    return RelatedDocument(
        uuid=uuid,
        tipo_relacion=tipo_relacion,
        metodo_pago=metodo_pago,
        total=total,
        posted=posted,
        moneda=moneda,
    )


def _original_facts(
    uuid: Uuid,
    documents: DocumentRepository | None,
    xml_reader: ExtractedXmlReader | None,
    parser: FiscalXmlParser | None,
) -> tuple[str | None, NormalizedAmount | None, str | None]:
    """The original's MetodoPago, header Total and currency, or three unknowns.

    The bytes are re-read through the same three ports M2.8 uses, so the facts come from the
    stored artifact and not from the related document's header: `CfdiRelacionados` states a
    UUID, never what the original said. `build_fiscal_document` is the one translation M2 has,
    so a Total this stage hands on is normalized exactly as the posting rules see one.
    """
    if documents is None or xml_reader is None or parser is None:
        return None, None, None
    try:
        record = documents.get(uuid)
        if record is None:
            return None, None, None
        artifact = ExtractedXml(record.uuid.value, record.tipo, record.source_hash)
        raw = parser.parse(xml_reader.read(artifact), record.source_hash)
        original = build_fiscal_document(raw, record.source_hash).document
    except Exception:  # noqa: BLE001 (a per-document read must never abort the stage)
        return None, None, None
    if original is None:
        return None, None, None
    return original.metodo_pago, original.total, original.moneda


def _posted_source_uuids(
    document: FiscalDocument, contributor_rfc: Rfc, store: JournalEntryStore
) -> frozenset[Uuid]:
    """Which of the document's related documents the client's own books already hold (§8:192).

    A REP's related documents are *other* CFDI UUIDs, and whether one of them was ever booked is a
    ledger fact — not something the REP states and not something a rule may look up for itself.
    So the answer is assembled here, once per document, from the store's reader:

    - **only `POSTED` counts.** `PROPOSED` is a refusal that moved nothing and `SKIPPED` is a
      document deliberately outside accounting scope, so neither is a receivable or a payable the
      payment could settle; treating either as presence would book a settlement against a booking
      that never existed.
    - **per UUID, deduplicated.** A related document may appear under several payments (each its own
      partialidad), and presence is a property of the *document*, so the reader is asked once per
      distinct UUID, in the order the document states them (deterministic, and a repeated UUID does
      not become a second query).
    - **nothing is asked when there is nothing to ask about.** A document with no `pago20`
      complemento gets the empty frozenset and costs no query, which keeps this seam invisible to
      every rule that does not need it (§8:179–182).
    """
    pagos = document.pagos
    if pagos is None:
        return frozenset()
    related = dict.fromkeys(
        docto.uuid for pago in pagos.pagos for docto in pago.doctos_relacionados
    )
    return frozenset(
        uuid
        for uuid in related
        if any(
            record.posting_state is PostingState.POSTED
            for record in store.for_source(contributor_rfc, uuid)
        )
    )


def _classification_for(
    processed: ProcessedDocument, document: FiscalDocument, mapping: AccountMapping
) -> Classification | None:
    """§8a:207's answer — for a received comprobante that books a base, else `None`.

    §8's rows 4.3/4.4 and §8a:212's 4.8/4.9 are the only rows whose base role the row
    itself does not fix, so they are the only ones that need the client's `ClaveProdServ →
    AccountingCategory` table. An emitted document's base role comes from §8:179/180, and a
    note over a PUE original simply reverses it; every other comprobante is a skip or an unbuilt
    row, and classifying one would ask a question this engine does not have to answer — and could
    turn an unrelated product into a review reason.
    """
    if processed.perspective is not Perspective.RECIBIDO:
        return None
    if TipoComprobante.of(document.tipo) not in (TipoComprobante.INGRESO, TipoComprobante.EGRESO):
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
