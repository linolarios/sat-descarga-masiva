"""Rules 4.6–4.9 — CFDI Egreso credit notes: the correction of a booked original (§8a:212).

A credit note (`E`, `Egreso`) is neither a sale nor a purchase: it *cancels part or all of* one,
so what it books is the reversal of the roles the original used. Which roles those are is not in
the note — it is in the **original**, and the one fact of the original that decides it is its
`MetodoPago`: a PUE original was presumed paid in one exhibition (§8:171), a PPD one left a
receivable or a payable open (§8:180/182). The family is selected by that method, never by the
note's own:

- **4.6** — PUE original, issued: DR Ingresos=base · DR IVA Cobrado=IVA · CR Clearing=Total
- **4.7** — PPD original, issued: DR Ingresos=base · DR IVA No Cobrado=IVA · CR Clientes=Total
- **4.8** — PUE original, received: DR Clearing=Total · CR Gasto/Inv=base · CR IVA Pagado=IVA
- **4.9** — PPD original, received: DR Proveedores=Total · CR Gasto/Inv=base · CR IVA Pend.=IVA

Each is the exact mirror of the original's row (4.1/4.2/4.3/4.4): the same accounts, the
opposite sides. That is the whole accounting content of §8a:212, and it is why the note needs no
role the vocabulary does not already have — the mirror is read off the original, not invented.

**What this slice cannot prove, and therefore never posts.** Two pieces of evidence are not
observable yet, and each family says so with its own blocking flag instead of a guess:

- **4.6/4.8** reverse cash that nothing in this engine observes going back, so every entry
  carries `EGRESO_IVA_EVIDENCE_MISSING` — the approved ⚠ flag — and stays `PROPOSED`;
- **4.7/4.9** reverse a PPD original whose payments and REPs are in no read model either, so every
  entry carries `EGRESO_PAYMENT_HISTORY_UNAVAILABLE` and stays `PROPOSED`.

That second word is deliberately *not* `PARTIAL_PAYMENT_REVERSAL_UNSPECIFIED` (a 4.15
condition: the engine knows the original was partially paid and cannot say how to reverse it) and
not `MISSING_REP_ORIGINAL` (a 4.5 condition: a payment's own document is absent). Those
words name different gaps — this one is epistemic, *the history was not read* — and keeping them
apart is what lets a later milestone close this one by reading a payment index rather than
rewriting a decision.

A note the family cannot claim is reviewed by `credit_note_review`, never silently skipped
(§8:161) and never booked against a guessed original: the four relation facts it names are all
resolvable by a human, which is exactly what a review is for (§8:173).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sat_descarga_masiva.contabilidad.classification import ClassificationRefusal
from sat_descarga_masiva.contabilidad.journal import JournalLine, LineSide, ProposedJournalEntry
from sat_descarga_masiva.contabilidad.roles import AccountRole
from sat_descarga_masiva.contabilidad.rules.amounts import (
    BASE_KEY,
    CLEARING_KEY,
    IVA_KEY,
    METODO_PPD,
    METODO_PUE,
    REFUSAL_FLAGS,
    TOTAL_KEY,
    BaseAmounts,
    base_amounts,
)
from sat_descarga_masiva.contabilidad.rules.contract import (
    _PRECONDITION_DETAIL,
    PostingContext,
    Proposal,
    RelatedDocument,
    ReviewRequest,
    RuleRow,
    _flag,
    _review,
)
from sat_descarga_masiva.domain.enums.comprobante import TipoComprobante
from sat_descarga_masiva.domain.model.perspective import Perspective
from sat_descarga_masiva.domain.model.review import ReviewFlag, ReviewFlagType
from sat_descarga_masiva.domain.model.value_objects import Uuid

#: `c_TipoRelacion` 01 — "nota de crédito": the note corrects the documents it names (§8a:212).
RELACION_NOTA_CREDITO = "01"

#: The one currency this engine values without an FX decision (§8:193).
NATIONAL_CURRENCY = "MXN"

RULE_4_6 = "4.6"
RULE_4_7 = "4.7"
RULE_4_8 = "4.8"
RULE_4_9 = "4.9"

#: Bumped only when the rule's *decision* changes: §8:194 keys the posting by it, so a historical
#: entry stays explainable against the rule version that produced it.
RULE_4_6_VERSION = "1"
RULE_4_7_VERSION = "1"
RULE_4_8_VERSION = "1"
RULE_4_9_VERSION = "1"


@dataclass(frozen=True)
class CreditNoteRule:
    """One row of §8a:212's family: the reversal it books, and the method it is chosen by.

    ``original_metodo_pago`` is the **original's** ``MetodoPago`` and the only method this
    family selects on. The note's own header method is never read: a note is not paid, it
    corrects something that was, so a PUE note against a PPD original is still 4.7/4.9. That is
    also why ``metodo_pago`` — the wildcard `RuleRow` asks every row for — is `None` here: the
    row states "any method" rather than a value the rule would then have to un-learn.

    ``money_role``/``money_key``/``money_side`` are the leg the money moves on, and
    ``iva_role``/``iva_side``/``base_side`` complete the mirror: each side is the opposite of
    what the original's row moved, because a credit note takes back what that row put down.
    Stating the sides per row is what lets the four rules share one body and still differ
    exactly where §8a:212 differs.

    ``blocking_flag``/``blocking_detail`` are the evidence this slice cannot observe (see the module
    docstring): the row always *names* it, so an entry that is well formed still says what is
    missing and can never be posted by an accident of arithmetic.
    """

    rule_id: str
    rule_version: str
    perspective: Perspective
    original_metodo_pago: str
    money_role: AccountRole
    money_key: str
    money_side: LineSide
    iva_role: AccountRole
    iva_side: LineSide
    base_side: LineSide
    blocking_flag: ReviewFlagType
    blocking_detail: str
    propose: Callable[[PostingContext], Proposal]
    tipo: TipoComprobante = TipoComprobante.EGRESO
    metodo_pago: str | None = None

    def claims(self, request: PostingContext) -> bool:
        """True when §8a:212's row is the note's: its type, its side, the original's method.

        The original is the selector, so a note whose relation names no single resolvable
        original claims nothing here — `credit_note_review` names that gap instead, which keeps
        "no row applies" from becoming "no rule accounts tipo E".
        """
        if TipoComprobante.of(request.document.tipo) is not self.tipo:
            return False
        if request.perspective is not self.perspective:
            return False
        original = credit_note_original(request)
        return original is not None and original.metodo_pago == self.original_metodo_pago


def _credit_note_uuids(request: PostingContext) -> tuple[Uuid, ...]:
    """Every UUID the note states under `TipoRelacion` 01, in source order (§8a:212).

    The note's own `CfdiRelacionados` is the only statement of what it corrects, so this
    is read as sourced: a group with any other `TipoRelacion` (04 substitution, 07 cancellation)
    is a different claim, and contributes nothing to a credit-note reversal.
    """
    return tuple(
        uuid
        for relacion in request.document.cfdi_relacionados
        if relacion.tipo_relacion == RELACION_NOTA_CREDITO
        for uuid in relacion.uuids
    )


def credit_note_original(request: PostingContext) -> RelatedDocument | None:
    """The single resolved original a credit note reverses, or `None` when there is not one.

    Exactly one is what §8a:212's rows book: a note that names several originals states an
    allocation this slice does not compute, and a note that names one the engine could not resolve
    has nothing to mirror. Both are `credit_note_review` cases, never a first-wins pick — taking
    the first of two would book a reversal against a document the note did not say it was
    correcting.
    """
    uuids = _credit_note_uuids(request)
    if len(uuids) != 1:
        return None
    return {related.uuid: related for related in request.related_documents}.get(uuids[0])


def credit_note_review(request: PostingContext) -> ReviewRequest:
    """Why a credit note this family does not claim cannot be booked yet (§8a:212).

    The four rows are the only treatment M3 has for a credit note, so the selector falls back here
    rather than to §8's general "no rule accounts this type": each reason a note is unclaimed is a
    *resolvable* fact, and naming which one is the difference between a human fixing a relation and
    a human re-reading the design. None of them can be keyed — no rule says how the note books
    until the original is known — so every one is a document-level review (§8:167).
    """
    uuids = _credit_note_uuids(request)
    if not uuids:
        return ReviewRequest(
            flags=(
                _flag(
                    ReviewFlagType.EGRESO_RELATION_MISSING,
                    "the note states no CfdiRelacionados with TipoRelacion 01: §8a:212 books a"
                    " credit note against the original it cancels, and none is named",
                ),
            ),
            detail="the note names no original, so its reversal has nothing to mirror",
        )
    if len(uuids) > 1:
        named = ", ".join(uuid.value for uuid in uuids)
        return ReviewRequest(
            flags=(
                _flag(
                    ReviewFlagType.EGRESO_MULTIPLE_ORIGINALS,
                    f"the note names {len(uuids)} originals ({named}): §8a:212 reverses one"
                    " original, and allocating one note across several is not in this slice",
                ),
            ),
            detail="the note corrects several originals at once, which this engine cannot allocate",
        )
    resolved = {related.uuid: related for related in request.related_documents}
    original = resolved.get(uuids[0])
    if original is None:
        return ReviewRequest(
            flags=(
                _flag(
                    ReviewFlagType.EGRESO_ORIGINAL_NOT_FOUND,
                    f"the original {uuids[0].value} is not in the client archive: without it the"
                    " roles to reverse are unknown, and guessing them would book a reversal of"
                    " something the books never held",
                ),
            ),
            detail="the original could not be resolved, so no credit-note row applies",
        )
    return ReviewRequest(
        flags=(
            _flag(
                ReviewFlagType.MISSING_SOURCE_FIELD,
                f"the original {original.uuid.value} could not be read (no MetodoPago): §8a:212"
                " selects 4.6/4.8 (PUE) or 4.7/4.9 (PPD) by it, and presuming PUE would"
                " reverse a payment the original never stated (§8:171)",
            ),
        ),
        detail="the original payment method is unknown, so neither row of the family applies",
    )


def rule_4_6(request: PostingContext) -> ProposedJournalEntry | ReviewRequest:
    """§8a:212 with a PUE original, issued: the mirror of §8:179 (4.1)."""
    return _credit_note(request, _RULE_4_6)


def rule_4_7(request: PostingContext) -> ProposedJournalEntry | ReviewRequest:
    """§8a:212 with a PPD original, issued: the mirror of §8:180 (4.2)."""
    return _credit_note(request, _RULE_4_7)


def rule_4_8(request: PostingContext) -> ProposedJournalEntry | ReviewRequest:
    """§8a:212 with a PUE original, received: the mirror of §8:181 (4.3)."""
    return _credit_note(request, _RULE_4_8)


def rule_4_9(request: PostingContext) -> ProposedJournalEntry | ReviewRequest:
    """§8a:212 with a PPD original, received: the mirror of §8:182 (4.4)."""
    return _credit_note(request, _RULE_4_9)


def _credit_note(  # noqa: PLR0911 (each gate is a distinct §8a:212 precondition)
    request: PostingContext, row: CreditNoteRule
) -> ProposedJournalEntry | ReviewRequest:
    """§8a:212's calculation: the shape, the evidence, then the mirror of the original.

    The order keeps a refusal from looking like a flag: claimable first (a direct call is still
    a call), then the original must be *booked* — a note cannot reverse a receivable or payable
    the books never held — then a received note must classify (§8a:207's base role is the
    one thing the row does not fix), then the note's own amounts must be usable, then the two
    facts it cannot compute from the note alone (its currency, and the cap its original sets), and
    only then legs: a note that exceeds its original must not have produced an entry first. Every
    gate but the last returns a review keyed by the row (§8:169/194); the last returns the legs
    *with* the blocking flag this slice's evidence cannot clear.
    """
    if not row.claims(request):
        document = request.document
        claimed = credit_note_original(request)
        stated = f"original {claimed.uuid.value}" if claimed is not None else "none"
        return _review(
            request,
            row,
            flags=(
                _flag(
                    ReviewFlagType.UNSUPPORTED_RULE,
                    f"rule {row.rule_id} reverses an original paid {row.original_metodo_pago}"
                    f" {row.perspective.value}; this note is {document.tipo!r}"
                    f" {request.perspective.value} with {stated}",
                ),
            ),
            detail="this note is not the row this rule books",
        )
    original = credit_note_original(request)
    if original is None:
        return credit_note_review(request)
    if not original.posted:
        return _review(
            request,
            row,
            flags=(
                _flag(
                    ReviewFlagType.EGRESO_ORIGINAL_NOT_POSTED,
                    f"the original {original.uuid.value} has no POSTED entry: a credit note"
                    " reverses a receivable or payable the books hold, and this one never"
                    " reached the ledger — a PROPOSED refusal or a SKIPPED row moved nothing",
                ),
            ),
            detail=_PRECONDITION_DETAIL,
        )
    base_role = AccountRole.INGRESOS
    if row.perspective is Perspective.RECIBIDO:
        classification = request.classification
        if classification is None:
            return _review(
                request,
                row,
                flags=(
                    _flag(
                        ReviewFlagType.MISSING_SOURCE_FIELD,
                        "no classification was resolved for this received credit note:"
                        " §8a:207's ClaveProdServ answer is a precondition",
                    ),
                ),
                detail=_PRECONDITION_DETAIL,
            )
        if isinstance(classification, ClassificationRefusal):
            return _review(
                request,
                row,
                flags=(_flag(REFUSAL_FLAGS[classification.kind], classification.detail),),
                detail=_PRECONDITION_DETAIL,
            )
        base_role = classification
    amounts = base_amounts(request, row)
    if isinstance(amounts, ReviewRequest):
        return amounts
    unvalued = _unvalued_currency(request, original)
    if unvalued is not None:
        return _review(request, row, flags=(unvalued,), detail=_PRECONDITION_DETAIL)
    if original.total is None:
        return _review(
            request,
            row,
            flags=(
                _flag(
                    ReviewFlagType.MISSING_SOURCE_FIELD,
                    f"the original {original.uuid.value} states no Total: the only cap a"
                    " credit note is held to is missing, so the note cannot be proven to"
                    " correct it",
                ),
            ),
            detail=_PRECONDITION_DETAIL,
        )
    if amounts.total.amount > original.total.amount:
        return _review(
            request,
            row,
            flags=(
                _flag(
                    ReviewFlagType.EGRESO_EXCEEDS_ORIGINAL,
                    f"the note Total {amounts.total.amount} exceeds the original"
                    f" {original.total.amount} ({original.uuid.value}): §8a:212 reverses an"
                    " original, it does not book more than the original ever stated. Prior"
                    " credit notes are deliberately not counted, so this is the single-note cap",
                ),
            ),
            detail="the note exceeds what it corrects, so an entry would overstate it",
        )
    return ReviewRequest(
        entry=_entry(request, row, amounts, base_role),
        flags=(_flag(row.blocking_flag, row.blocking_detail),),
        detail=(
            "the reversal is computed and balanced, but the evidence this engine can"
            " observe does not cover it yet (§8a:212): the entry is proposed, and it must"
            " not post"
        ),
    )


def _unvalued_currency(request: PostingContext, original: RelatedDocument) -> ReviewFlag | None:
    """The note's own valuation, and whether it can be netted against the original (§8:193).

    A note in another currency is a conversion this engine has no policy for, so it is reviewed
    rather than valued; and a note whose currency differs from its original's would net two
    amounts the rate chain never tied together — the same gap seen from the other side.
    """
    document = request.document
    if document.moneda != NATIONAL_CURRENCY:
        return _flag(
            ReviewFlagType.AMBIGUOUS_FX,
            f"the note is in {document.moneda}: §8:193 rate chain has valued nothing here,"
            " so a reversal in another currency would be a conversion it cannot state",
        )
    if original.moneda is not None and original.moneda != document.moneda:
        return _flag(
            ReviewFlagType.AMBIGUOUS_FX,
            f"the note is in {document.moneda} and its original in {original.moneda}:"
            " netting them would mix two valuations the rate chain never tied together",
        )
    return None


def _entry(
    request: PostingContext, row: CreditNoteRule, amounts: BaseAmounts, base_role: AccountRole
) -> ProposedJournalEntry:
    """§8a:212's legs: the mirror of the original's row, balanced by the same proof.

    ``row`` supplies every side, so the four rules cannot disagree about their own
    arithmetic; ``base_role`` is the classified `Gasto`/`Inventario` for a received original
    (§8a:207) and `Ingresos` for an issued one, which is the one role the row does not fix.
    The entry balances because `base_amounts` proved `Total == base + IVA` before this point
    (§8:158), and the IVA leg is dropped — not zeroed — when there is no tax to reverse.
    """
    lines = [
        JournalLine(
            account_role=row.money_role,
            line_key=row.money_key,
            side=row.money_side,
            amount=amounts.total,
        ),
        JournalLine(
            account_role=base_role,
            line_key=BASE_KEY,
            side=row.base_side,
            amount=amounts.base,
        ),
    ]
    if amounts.iva.amount > 0:
        lines.append(
            JournalLine(
                account_role=row.iva_role,
                line_key=IVA_KEY,
                side=row.iva_side,
                amount=amounts.iva,
            )
        )
    return ProposedJournalEntry(
        rule_id=row.rule_id,
        rule_version=row.rule_version,
        contributor_rfc=request.contributor_rfc,
        source_uuid=amounts.source_uuid,
        source_hash=request.document.source_hash,
        entry_date=amounts.entry_date,
        lines=tuple(lines),
    )


#: Why 4.6/4.8's entry may not post: the refund is not observable (§8a:212).
_IVA_EVIDENCE = (
    "§8a:212 reverses the IVA the original booked, but nothing this engine observes shows"
    " the refund happening: that evidence seam does not exist yet, so the entry is"
    " proposed and must not post"
)

#: Why 4.7/4.9's entry may not post: the payment/REP history is not observable (§8a:212).
_PAYMENT_HISTORY = (
    "the original was PPD, so what it reverses depends on payments and REPs this engine"
    " cannot read yet: that history is not observable, so the entry is proposed and must"
    " not post"
)


#: §8a:212's four rows, one per (side, the original's method). The note's own method is on none
#: of them — it is not read at all — and each row names the evidence this slice cannot observe,
#: so the entry a row proposes always says why it may not post.
_RULE_4_6 = CreditNoteRule(
    rule_id=RULE_4_6,
    rule_version=RULE_4_6_VERSION,
    perspective=Perspective.EMITIDO,
    original_metodo_pago=METODO_PUE,
    money_role=AccountRole.CLEARING,
    money_key=CLEARING_KEY,
    money_side=LineSide.HABER,
    iva_role=AccountRole.IVA_TRASLADADO_COBRADO,
    iva_side=LineSide.DEBE,
    base_side=LineSide.DEBE,
    blocking_flag=ReviewFlagType.EGRESO_IVA_EVIDENCE_MISSING,
    blocking_detail=_IVA_EVIDENCE,
    propose=rule_4_6,
)

_RULE_4_7 = CreditNoteRule(
    rule_id=RULE_4_7,
    rule_version=RULE_4_7_VERSION,
    perspective=Perspective.EMITIDO,
    original_metodo_pago=METODO_PPD,
    money_role=AccountRole.CLIENTES,
    money_key=TOTAL_KEY,
    money_side=LineSide.HABER,
    iva_role=AccountRole.IVA_TRASLADADO_NO_COBRADO,
    iva_side=LineSide.DEBE,
    base_side=LineSide.DEBE,
    blocking_flag=ReviewFlagType.EGRESO_PAYMENT_HISTORY_UNAVAILABLE,
    blocking_detail=_PAYMENT_HISTORY,
    propose=rule_4_7,
)

_RULE_4_8 = CreditNoteRule(
    rule_id=RULE_4_8,
    rule_version=RULE_4_8_VERSION,
    perspective=Perspective.RECIBIDO,
    original_metodo_pago=METODO_PUE,
    money_role=AccountRole.CLEARING,
    money_key=CLEARING_KEY,
    money_side=LineSide.DEBE,
    iva_role=AccountRole.IVA_ACRED_PAGADO,
    iva_side=LineSide.HABER,
    base_side=LineSide.HABER,
    blocking_flag=ReviewFlagType.EGRESO_IVA_EVIDENCE_MISSING,
    blocking_detail=_IVA_EVIDENCE,
    propose=rule_4_8,
)

_RULE_4_9 = CreditNoteRule(
    rule_id=RULE_4_9,
    rule_version=RULE_4_9_VERSION,
    perspective=Perspective.RECIBIDO,
    original_metodo_pago=METODO_PPD,
    money_role=AccountRole.PROVEEDORES,
    money_key=TOTAL_KEY,
    money_side=LineSide.DEBE,
    iva_role=AccountRole.IVA_ACRED_PENDIENTE,
    iva_side=LineSide.HABER,
    base_side=LineSide.HABER,
    blocking_flag=ReviewFlagType.EGRESO_PAYMENT_HISTORY_UNAVAILABLE,
    blocking_detail=_PAYMENT_HISTORY,
    propose=rule_4_9,
)

#: §8a:212's rows as the engine's one registry holds them: `posting.py` appends this
#: tuple after its own `I` rows and the REP rows, so selection order stays deterministic
#: and an `E` comprobante is claimed here and nowhere else.
CREDIT_NOTE_ROWS: tuple[RuleRow, ...] = (
    _RULE_4_6,
    _RULE_4_7,
    _RULE_4_8,
    _RULE_4_9,
)
