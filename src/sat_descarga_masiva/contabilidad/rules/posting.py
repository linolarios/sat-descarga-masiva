"""Rules 4.1/4.2 — `I` EMITIDO, and the one door that selects between them (§8:179/180).

§8's table gives an *issued* income comprobante two rows, one per payment method:

- **4.1** (PUE): the SAT presumption is a single-exhibition payment, so the cash leg is booked
  to `CLEARING` and the entry says so — `ASSUMED_PUE` (§8:171). It is an *accounting
  assumption*, never payment evidence (§8a:208), and it travels as an
  `AccountingAssumption` **on the entry**, not inside a ``line_key``: the key is §8:194's
  structural identity for the leg, so an assumption that a later bank reconciliation may
  promote to `SUPPORTED_BY_BANK` cannot redefine it.
- **4.2** (PPD): nothing is presumed paid, so the receivable is booked to `CLIENTES`.

Both compute the same base — §8:175/191 under M3's `discount_policy = net` (§8a:204): `base =
SubTotal − Descuento` — and both take the IVA from the document's own `002` traslados
(§8:179/180), one leg however many rates contribute to it.

A document whose shape is not that one gets no entry rather than a partial one. §8:173's
preconditions are explicit here: an unbookable tax shape (retenciones, or a traslado of
another impuesto) is `UNSUPPORTED_RULE`; an absent `SubTotal`/`Total` or an unreadable
traslado is `MISSING_SOURCE_FIELD`; a corrupt or self-inconsistent amount is `INVALID_AMOUNT`;
a document that cannot be dated or identified is refused before anything is computed. Nothing
posts unless `Total == base + IVA` holds, which is what makes the proposed entry balance.

`propose(request)` is the engine's one door per document — skip, propose, or review
(§8:161/173) — so the pipeline asks one question and gets one of the three answers.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sat_descarga_masiva.contabilidad.journal import (
    AccountingAssumption,
    JournalLine,
    LineSide,
    ProposedJournalEntry,
)
from sat_descarga_masiva.contabilidad.roles import AccountRole
from sat_descarga_masiva.contabilidad.rules.contract import PostingContext, ReviewRequest, Skip
from sat_descarga_masiva.contabilidad.rules.scope import out_of_scope
from sat_descarga_masiva.domain.enums.comprobante import TipoComprobante
from sat_descarga_masiva.domain.model.fiscal_document import FiscalDocument
from sat_descarga_masiva.domain.model.perspective import Perspective
from sat_descarga_masiva.domain.model.review import ReviewFlag, ReviewFlagType
from sat_descarga_masiva.domain.model.value_objects import Uuid
from sat_descarga_masiva.domain.policy.money import NormalizedAmount

#: One of §8's payment methods, as the CFDI writes it (`MetodoPago`). Never normalized: §8:179
#: and §8:180 are chosen by this exact word, so a document that does not carry one is reviewed.
METODO_PUE = "PUE"
METODO_PPD = "PPD"

#: `c_Impuesto` for IVA — the only traslado these two rules book (§8:179/180).
IVA_IMPUESTO = "002"

#: `c_TipoFactor`'s exempt value: an exempt traslado moves no amount, so it books no leg.
EXENTO = "Exento"

RULE_4_1 = "4.1"
RULE_4_2 = "4.2"

#: Bumped only when the rule's *decision* changes: §8:194 keys the posting by it, so a historical
#: entry stays explainable against the rule version that produced it.
RULE_4_1_VERSION = "1"
RULE_4_2_VERSION = "1"

#: The legs' structural keys: §8:179/180 name one cash/receivable leg, one sold base and one
#: IVA line, whatever the rates. §8:194 keys each leg by these, so they stay structural —
#: `ASSUMED_PUE` is evidence about the entry (`AccountingAssumption`) and never appears here.
CLEARING_KEY = "clearing"
BASE_KEY = "base"
IVA_KEY = "iva"

#: What a rule (and the selector below) may answer with (§8:173).
Proposal = ProposedJournalEntry | Skip | ReviewRequest

#: Detail sentence for every "the document is not what this rule can book" refusal — the
#: machine-readable part is the flag's *type*, this is the sentence a human reads in the log
#: (§8:167), so a reworded sentence can never change a decision.
_PRECONDITION_DETAIL = (
    "the rule's source preconditions are not met, so no entry is proposed rather than a"
    " partial one (§8:173)"
)


def _flag(flag_type: ReviewFlagType, reason: str) -> ReviewFlag:
    return ReviewFlag(flag_type=flag_type, reason=reason)


@dataclass(frozen=True)
class PostingRule:
    """One row of §8's table: the document shape it books, where the money lands, and how.

    ``debit_role``/``debit_key`` are the leg the money is received into — `CLEARING` for a PUE
    comprobante (§8:171) and `CLIENTES` for a PPD one (§8:180) — and ``iva_role`` is the IVA
    line's role, which follows the same distinction (`IVA_TRASLADADO_COBRADO` vs
    `IVA_TRASLADADO_NO_COBRADO`). ``debit_key`` is structural (§8:194) and ``assumptions`` is
    the row's evidence (§8:171): a PUE row states `ASSUMED_PUE` on the entry, a PPD row states
    nothing, and neither puts an assumption into a leg's key.
    """

    rule_id: str
    rule_version: str
    tipo: TipoComprobante
    perspective: Perspective
    metodo_pago: str
    debit_role: AccountRole
    debit_key: str
    iva_role: AccountRole
    propose: Callable[[PostingContext], Proposal]
    assumptions: tuple[AccountingAssumption, ...] = ()

    def claims(self, request: PostingContext) -> bool:
        """True when §8's row is the document's: its type, its perspective and its method."""
        document = request.document
        return (
            TipoComprobante.of(document.tipo) is self.tipo
            and request.perspective is self.perspective
            and document.metodo_pago == self.metodo_pago
        )


@dataclass(frozen=True)
class _IncomeAmounts:
    """The three amounts §8:175/179/180 book, once the document is proven to carry them.

    ``source_uuid``/``entry_date`` travel with them because they are what the posting is keyed
    and dated by (§8:194, §8a:209): proving they exist is part of the same precondition check,
    and handing them over narrowed is what stops a rule from inventing either.
    """

    source_uuid: Uuid
    entry_date: date
    base: NormalizedAmount
    iva: NormalizedAmount
    total: NormalizedAmount


def rule_4_1(request: PostingContext) -> ProposedJournalEntry | ReviewRequest:
    """§8:179 — an issued PUE income comprobante: the cash leg lands on the clearing account."""
    return _issued_income(request, _RULE_4_1)


def rule_4_2(request: PostingContext) -> ProposedJournalEntry | ReviewRequest:
    """§8:180 — an issued PPD income comprobante: nothing is presumed paid, the client owes."""
    return _issued_income(request, _RULE_4_2)


def _issued_income(
    request: PostingContext, row: PostingRule
) -> ProposedJournalEntry | ReviewRequest:
    """§8:179/180's shared calculation: the shape, then the preconditions, then the legs."""
    if not row.claims(request):
        document = request.document
        return _review(
            request,
            row,
            flags=(
                _flag(
                    ReviewFlagType.UNSUPPORTED_RULE,
                    f"rule {row.rule_id} books {row.tipo.value} {row.perspective.value}"
                    f" {row.metodo_pago}; this document is {document.tipo!r}"
                    f" {request.perspective.value} {document.metodo_pago!r}",
                ),
            ),
            detail="the document is not the row this rule books, so the rule computed nothing",
        )
    amounts = _income_amounts(request, row)
    if isinstance(amounts, ReviewRequest):
        return amounts
    return _entry(request, row, amounts)


def _income_amounts(request: PostingContext, row: PostingRule) -> _IncomeAmounts | ReviewRequest:
    """§8:173's preconditions: what `base`, `IVA` and `Total` come from, or why they cannot.

    ``row`` is only carried so a refusal can be keyed by the rule that made it.
    """
    document = request.document
    entry_date, source_uuid = document.fecha, document.source_uuid
    if entry_date is None or source_uuid is None:
        return _review(
            request,
            row,
            flags=_recordability_flags(document),
            detail="the document cannot be dated or keyed, so no entry can record it",
        )

    unbookable = _unbookable_tax(document)
    if unbookable is not None:
        return _review(request, row, flags=(unbookable,), detail=_PRECONDITION_DETAIL)

    base = _net_base(document)
    total = document.total
    if base is None or total is None:
        return _review(
            request,
            row,
            flags=(
                _flag(
                    ReviewFlagType.MISSING_SOURCE_FIELD,
                    "SubTotal/Total are absent: the entry's base and its total come from them",
                ),
            ),
            detail=_PRECONDITION_DETAIL,
        )

    flags: list[ReviewFlag] = []
    iva = _iva_traslado(document, flags)
    if flags:
        return _review(request, row, flags=tuple(flags), detail=_PRECONDITION_DETAIL)

    refusals = _amount_refusals(base, iva, total)
    if refusals:
        return _review(request, row, flags=refusals, detail=_PRECONDITION_DETAIL)

    return _IncomeAmounts(
        source_uuid=source_uuid, entry_date=entry_date, base=base, iva=iva, total=total
    )


def _entry(
    request: PostingContext, row: PostingRule, amounts: _IncomeAmounts
) -> ProposedJournalEntry:
    """§8:179/180's legs: the money lands on the row's account, base and IVA are the credits.

    The entry balances by construction: `Total == base + IVA` was proven before this point, so
    the debit total equals the credit total — and §8:159 checks it again before posting. The
    row's ``assumptions`` travel on the entry (§8:171): what was presumed, recorded beside the
    legs whose keys stay structural (§8:194).
    """
    lines = [
        JournalLine(
            account_role=row.debit_role,
            line_key=row.debit_key,
            side=LineSide.DEBE,
            amount=amounts.total,
        ),
        JournalLine(
            account_role=AccountRole.INGRESOS,
            line_key=BASE_KEY,
            side=LineSide.HABER,
            amount=amounts.base,
        ),
    ]
    if amounts.iva.amount > 0:
        lines.append(
            JournalLine(
                account_role=row.iva_role,
                line_key=IVA_KEY,
                side=LineSide.HABER,
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
        assumptions=row.assumptions,
    )


def _review(
    request: PostingContext,
    row: PostingRule,
    *,
    flags: tuple[ReviewFlag, ...],
    detail: str,
) -> ReviewRequest:
    """A refusal keyed as an entry when the document can be (§8:169/194), else on the document.

    A review fact is recorded against an entry's fingerprint, so it needs the same provenance a
    posting needs: contributor, source, rule and version — and a date (§8a:209) and a TFD UUID
    (§8:194). When either is missing there is no entry to key, and inventing one would be worse
    than flagging the document itself (§8:167); so ``entry`` stays ``None``.
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
            lines=(),
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


def _net_base(document: FiscalDocument) -> NormalizedAmount | None:
    """§8:175/191 + §8a:204: `base = SubTotal − Descuento` under M3's `discount_policy = net`.

    ``None`` when the source carries no ``SubTotal`` (an unnormalized currency nulls it, M2) —
    the caller reviews instead of treating a missing base as zero, which would credit revenue
    that the document does not state.
    """
    if document.subtotal is None:
        return None
    descuento = document.descuento.amount if document.descuento is not None else Decimal("0")
    return NormalizedAmount(document.subtotal.amount - descuento)


def _iva_traslado(document: FiscalDocument, flags: list[ReviewFlag]) -> NormalizedAmount:
    """The document's own IVA traslados, summed into the one leg §8:179/180 names.

    An exempt traslado moves nothing and contributes nothing. A traslado that carries a rate but
    no `Importe` is a source the rule cannot compute from, so it appends its reason to ``flags``
    and the caller refuses — the returned total is then meaningless by construction.
    """
    total = Decimal("0")
    for traslado in document.impuestos.traslados:
        if traslado.importe is None:
            if traslado.tipo_factor == EXENTO:
                continue
            flags.append(
                _flag(
                    ReviewFlagType.MISSING_SOURCE_FIELD,
                    f"IVA traslado ({traslado.tipo_factor}) carries no Importe: the tax leg"
                    " cannot be computed from it",
                )
            )
            continue
        total += traslado.importe.amount
    return NormalizedAmount(total)


def _amount_refusals(
    base: NormalizedAmount, iva: NormalizedAmount, total: NormalizedAmount
) -> tuple[ReviewFlag, ...]:
    """§12 + §8:175: the amounts must be usable *and* consistent with the document's own total.

    A corrupt amount — non-finite, zero or negative — is never posted (non-finite is checked
    first, because a comparison against ``NaN`` raises rather than answering, and a raise is not
    a review decision either). And with no retenciones and only IVA traslados (§8a's plain
    shape) a consistent CFDI states `Total == base + IVA`; checking it here is also what makes
    the proposed entry balance, so an inconsistent source is a corrupt document, not an entry.
    """
    values = (("base", base), ("IVA", iva), ("Total", total))
    non_finite = [
        f"{name} {value.amount}" for name, value in values if not value.amount.is_finite()
    ]
    if non_finite:
        return (
            _flag(
                ReviewFlagType.INVALID_AMOUNT,
                f"non-finite source amounts: {', '.join(non_finite)}",
            ),
        )
    # The base and the total must move something — a comprobante that sells nothing is not a
    # posting. IVA may legitimately be zero: an exempt document has no tax to book, so it gets
    # no IVA leg at all (§8:179) rather than a zero leg, which is not a leg.
    if base.amount <= 0 or total.amount <= 0 or iva.amount < 0:
        return (
            _flag(
                ReviewFlagType.INVALID_AMOUNT,
                f"unusable source amounts: base {base.amount}, IVA {iva.amount},"
                f" Total {total.amount}",
            ),
        )
    if total.amount != base.amount + iva.amount:
        return (
            _flag(
                ReviewFlagType.INVALID_AMOUNT,
                f"Total {total.amount} != base {base.amount} + IVA {iva.amount}: the document's"
                " own arithmetic is inconsistent",
            ),
        )
    return ()


def _unbookable_tax(document: FiscalDocument) -> ReviewFlag | None:
    """The one tax shape these rules book: IVA traslados and nothing else (§8:179/180).

    Retenciones and every other impuesto are *not* silently dropped — dropping them would
    produce an entry that does not state what the document says — so the document is reviewed
    instead, with the elements named.
    """
    if document.impuestos.retenciones:
        return _flag(
            ReviewFlagType.UNSUPPORTED_RULE,
            "the document carries retenciones, which §8:179/180's entry does not book",
        )
    others = sorted(
        {
            traslado.impuesto
            for traslado in document.impuestos.traslados
            if traslado.impuesto != IVA_IMPUESTO
        }
    )
    if others:
        return _flag(
            ReviewFlagType.UNSUPPORTED_RULE,
            f"the document carries traslados of impuesto {', '.join(others)}: §8:179/180 book"
            " IVA only, and a partial entry would misstate the document",
        )
    return None


#: §8's rows this module builds, one per (type, perspective, method). The rows are disjoint in
#: what they claim, so selection is deterministic, and the tuple *is* the engine's registry of
#: postable rules — the validator is wired with it (§8:159), so a rule id or version that is not
#: here cannot post. Each rule function books its own row and nothing else.
_RULE_4_1 = PostingRule(
    rule_id=RULE_4_1,
    rule_version=RULE_4_1_VERSION,
    tipo=TipoComprobante.INGRESO,
    perspective=Perspective.EMITIDO,
    metodo_pago=METODO_PUE,
    debit_role=AccountRole.CLEARING,
    debit_key=CLEARING_KEY,
    iva_role=AccountRole.IVA_TRASLADADO_COBRADO,
    propose=rule_4_1,
    assumptions=(AccountingAssumption.ASSUMED_PUE,),
)

_RULE_4_2 = PostingRule(
    rule_id=RULE_4_2,
    rule_version=RULE_4_2_VERSION,
    tipo=TipoComprobante.INGRESO,
    perspective=Perspective.EMITIDO,
    metodo_pago=METODO_PPD,
    debit_role=AccountRole.CLIENTES,
    debit_key="total",
    iva_role=AccountRole.IVA_TRASLADADO_NO_COBRADO,
    propose=rule_4_2,
)

POSTING_RULES: tuple[PostingRule, ...] = (_RULE_4_1, _RULE_4_2)

#: The engine's registry as the validator is wired with it: ``rule_id`` → current version.
SUPPORTED_RULES: dict[str, str] = {row.rule_id: row.rule_version for row in POSTING_RULES}


def propose(request: PostingContext) -> Proposal:
    """The engine's one door per document (§8:161/173): skip it, propose it, or review it.

    ``SKIPPED`` is reachable only through §8:161's out-of-scope table, and a document no rule
    can claim is a `ReviewRequest` — never a silent skip, and never a guess. The refusals here
    are deliberately *document-level* (no entry): no rule owns the document's shape, so there is
    no §8 number to key an entry by, and the review fact belongs to the document itself
    (§8:167).
    """
    skip = out_of_scope(request)
    if skip is not None:
        return skip
    for row in POSTING_RULES:
        if row.claims(request):
            return row.propose(request)
    document = request.document
    if (
        TipoComprobante.of(document.tipo) is TipoComprobante.INGRESO
        and request.perspective is Perspective.EMITIDO
    ):
        return ReviewRequest(
            flags=(
                _flag(
                    ReviewFlagType.MISSING_SOURCE_FIELD,
                    f"MetodoPago {document.metodo_pago!r}: §8:179/180 choose between 4.1 (PUE)"
                    " and 4.2 (PPD) by it, and presuming PUE would book a payment the document"
                    " does not state (§8:171)",
                ),
            ),
            detail="the payment method is absent or unknown, so neither row of §8's table applies",
        )
    return ReviewRequest(
        flags=(
            _flag(
                ReviewFlagType.UNSUPPORTED_RULE,
                f"no posting rule accounts tipo {document.tipo!r} {request.perspective.value}"
                " yet (§8:177)",
            ),
        ),
        detail="this document's row of §8's table is not built, so it waits for a human",
    )
