"""Rules 4.5a/4.5b — the REP (`pago20`), one entry per related document (§8:184/185/192).

A `P` comprobante states neither a sale nor a purchase: it states **payments** — one `Pago` per
cash movement, each naming the `DoctoRelacionado`s it settles, and each related document stating
its own `ImpPagado` and its own `impuestos_dr`. §8's table therefore gives it two rows, one per
side, and both are *reclassifications*: nothing is newly earned or spent, what was booked as a
receivable (or a payable) becomes cash, and the IVA that was booked as pending becomes the real
thing. §8:202 is explicit that 4.5a/4.5b add no role — they move the four IVA roles the income and
purchase rows already use.

- **4.5a** (`EMITIDO`, cash **in**): the client issued the original comprobante and is collecting.
  Per related document `DR Clearing = ImpPagado · CR Clientes = ImpPagado`, and the IVA reclass
  `DR IVA trasladado no cobrado · CR IVA trasladado cobrado`.
- **4.5b** (`RECIBIDO`, cash **out**): the client received a supplier's REP for a purchase it owes.
  Per related document `DR Proveedores = ImpPagado · CR Clearing = ImpPagado`, and the IVA reclass
  `DR IVA acreditable pagado · CR IVA acreditable pendiente`.

**One REP is one entry** (§8:192), dated by the comprobante's own `FiscalDocument.fecha` (§8a:209)
— never by a payment date, which is a source fact about the cash and not about the entry. Every
`Pago` and every `DoctoRelacionado` contributes to that one entry, so a multi-payment REP is read
whole rather than one payment at a time.

Six things about the calculation are load-bearing.

**The line key is the leg's identity, not its side.** Every leg is keyed
``<role>:<DoctoRelacionado UUID>:<NumParcialidad>``: one related document can be paid several times
(that is what `NumParcialidad` is for), so each of those payments is its own accounting fact with
its own key (§8:194) — a key carrying only the UUID would collide on the second partialidad (and
`ProposedJournalEntry` refuses a repeated key outright), while a ``:debe``/``:haber`` suffix would
make two *different* legs of one document look like one fact in two orientations. The role is in
the key for the same reason: one related document contributes a money leg *and* an IVA leg, and
those are different facts. The key flows unchanged into `PostingFingerprint.line_key`.

**The IVA comes from the document's own `impuestos_dr[]`, aggregated** — every traslado of impuesto
`002`, summed, never `impuestos_dr[0]` (§8:192): a related document may state several rates, so
reading the first element would book one rate's tax as if it were all of them. An absent
`impuestos_dr` is not a missing reading but the source's own statement that the related document
carries no taxes; zero IVA means no reclassification legs at all, since a zero leg is not a leg.

**The ledger, not the rule, decides whether a receivable was ever booked.** A rule may read nothing
but its `PostingContext`, so the answer — "at least one `POSTED` journal record exists for
``(contributor_rfc, DoctoRelacionado UUID)``" — is resolved by the application layer before
`propose()` runs and carried as `PostingContext.posted_source_uuids`. A `PROPOSED` or `SKIPPED`
record is *not* presence: the money it would have moved was never booked, so a collection against it
would have nothing to settle. When a related document is absent, §8:192's answer is the configured
unapplied-payment account (`PAGOS_NO_APLICADOS`) plus a `MISSING_REP_ORIGINAL` flag, and the IVA is
deliberately **not** realized or reclassified, so a half-done reclassification is never recorded as
if it were whole.

**A refusal is not a flag.** Two different "no"s are kept apart, because §8:192 asks for both:

- a **refusal** means the document contradicts itself or states nothing usable — no `pago20`
  complemento, no related document, a missing or corrupt amount, a broken
  `ImpSaldoAnt − ImpPagado == ImpSaldoInsoluto`, a `Σ ImpPagado != Pago.Monto`, the same
  `(UUID, NumParcialidad)` twice — and then **no legs at all** are proposed, because a partial entry
  would state a calculation nobody could stand behind; and
- a **blocking flag** means the deterministic part of the calculation is sound and must be
  recorded, while something else the document states cannot be settled — the related document is
  not in the ledger, `retenciones_dr`/`retenciones_p` are present (which this reclassification does
  not book), a `Pago`/`DoctoRelacionado` is in a currency §8:193's rate chain has not valued, or
  `ImpuestosP` disagrees with the related documents' own `ImpuestosDR`. The legs are proposed, the
  flag keeps the entry from posting, and the validator turns it into `PROPOSED`.

Nothing here is an advisory flag: a `POSTED` entry with an open blocking flag is unreachable by
construction, because the rule answers with a `ReviewRequest` and the validator honours it.

**FX is §8:193's resolution, not this rule's.** A payment or related document in a currency other
than `MXN` yields `FX_DIFFERENCE_UNCONFIRMED` and no legs for the amount nobody valued: no FX
gain/loss is computed, no rate is invented, and the entry stays un-postable. The `P` comprobante's
*header* currency is not what that test looks at — a REP states its own `MonedaP`/`MonedaDR`, and
the header is a `P` document's placeholder — which is why the `PostingEligibilityValidator`'s
header-currency condition does not apply to `TipoComprobante.PAGO`.

Routing is by `TipoComprobante.PAGO` alone (never `UsoCFDI`), and ``claims`` is the row's shape:
type and side. §8:192 says a `P` comprobante carries no header `MetodoPago` and no header
`FormaPago` at all, so there is no method to select on — the wildcard ``None`` expresses that rather
than inventing one.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sat_descarga_masiva.contabilidad.journal import (
    JournalLine,
    LineSide,
    ProposedJournalEntry,
)
from sat_descarga_masiva.contabilidad.roles import AccountRole
from sat_descarga_masiva.contabilidad.rules.contract import (
    _PRECONDITION_DETAIL,
    PostingContext,
    Proposal,
    ReviewRequest,
    _flag,
    _recordability_flags,
    _review,
)
from sat_descarga_masiva.domain.enums.comprobante import TipoComprobante
from sat_descarga_masiva.domain.model.fiscal_document import DoctoRelacionado, Pago, Pagos20
from sat_descarga_masiva.domain.model.perspective import Perspective
from sat_descarga_masiva.domain.model.review import ReviewFlag, ReviewFlagType
from sat_descarga_masiva.domain.model.value_objects import Uuid
from sat_descarga_masiva.domain.policy.money import NormalizedAmount

RULE_4_5A = "4.5a"
RULE_4_5B = "4.5b"

#: Bumped only when the rule's *decision* changes: §8:194 keys the posting by it, so a historical
#: entry stays explainable against the rule version that produced it.
RULE_4_5A_VERSION = "1"
RULE_4_5B_VERSION = "1"

#: `c_Impuesto` for IVA — the only traslado `impuestos_dr[]` reclassifies (§8:192). Declared here
#: rather than imported from `posting.py`, which imports this module for its rows: a shared catalog
#: home belongs in `domain/enums`, and a cycle between two rule modules does not.
IVA_IMPUESTO = "002"

#: `c_TipoFactor`'s exempt value: an exempt traslado moves no amount, so it contributes nothing.
EXENTO = "Exento"

#: The one currency a REP is valued in today: §8:193's rate chain (the source document's rate → the
#: REP's own exchange relationship → a configured rate → Banxico) is a *resolution* this rule must
#: not invent, so another currency is flagged and never valued.
_NATIONAL_CURRENCY = "MXN"

#: Why the entry carries legs *and* a flag: the deterministic part of §8:192's calculation is
#: recorded, and the part the source does not settle is left for a human — never a partial posting.
_REVIEW_DETAIL = (
    "the entry's deterministic legs are recorded but a source fact this reclassification cannot"
    " settle keeps them from posting, so a human decides and nothing partial is posted (§8:192)"
)

#: Why a REP with nothing to book is refused: §8:192's row is per related document, and a payment
#: that names none of them states no accounting fact this engine can compute.
_NO_RELATED_DOCUMENT_DETAIL = (
    "a REP books the related documents its payments state, and this document states none"
)


@dataclass(frozen=True)
class RepostingRule:
    """One of §8's two REP rows: the side it books, and the four roles the reclassification moves.

    ``money_role`` is where the money leg's counterpart lands when the related document *is* in the
    ledger — `CLIENTES` when collecting (§8:184), `PROVEEDORES` when paying (§8:185) — and
    ``unapplied_role`` is where it lands when it is not (§8:192). ``released_role`` is the role the
    original entry booked the tax to and ``realized_role`` the role the payment turns it into:
    §8:202 fixes all four, which is why 4.5a/4.5b add no role to the vocabulary.

    ``cash_in`` is the row's direction and the *only* thing that decides which side each of those
    four legs moves: a collection debits the clearing account and the not-yet-collected IVA, and
    credits the receivable and the realized IVA; a payment is the exact mirror. Expressing it once
    means the two rows cannot disagree about their own arithmetic, which is what makes every entry
    balance by construction.

    ``metodo_pago`` is the wildcard ``None``: §8:192 says a `P` comprobante carries no header
    ``MetodoPago``, so there is no method to select on and inventing one would be a claim about the
    source. It is on the row because `RuleRow` is what the engine's registry holds.
    """

    rule_id: str
    rule_version: str
    perspective: Perspective
    cash_in: bool
    money_role: AccountRole
    unapplied_role: AccountRole
    released_role: AccountRole
    realized_role: AccountRole
    propose: Callable[[PostingContext], Proposal]
    tipo: TipoComprobante = TipoComprobante.PAGO
    metodo_pago: str | None = None

    @property
    def clearing_side(self) -> LineSide:
        """The money leg's side: a collection debits it, a payment credits it (§8:184/185)."""
        return LineSide.DEBE if self.cash_in else LineSide.HABER

    @property
    def counterpart_side(self) -> LineSide:
        """The side the money leg's counterpart moves on — the receivable, payable or unapplied."""
        return self.clearing_side.opposite

    @property
    def released_side(self) -> LineSide:
        """The side the *pending* IVA role moves on: the same side the money moves (§8:202)."""
        return self.clearing_side

    @property
    def realized_side(self) -> LineSide:
        """The side the *realized* IVA role moves on: the mirror of the pending one (§8:202)."""
        return self.counterpart_side

    def claims(self, request: PostingContext) -> bool:
        """True when §8's row is the document's — a `P` comprobante on this row's side.

        The method is deliberately not consulted: §8:192's `P` rows carry no header ``MetodoPago``
        to choose between them, and a REP's *perspective* is the whole of what distinguishes
        collecting from paying.
        """
        return (
            TipoComprobante.of(request.document.tipo) is self.tipo
            and request.perspective is self.perspective
        )


def rule_4_5a(request: PostingContext) -> ProposedJournalEntry | ReviewRequest:
    """§8:184 — an issued REP (the client is collecting): cash in, reclassified per docto."""
    return _repost(request, _REP_COLLECTING)


def rule_4_5b(request: PostingContext) -> ProposedJournalEntry | ReviewRequest:
    """§8:185 — a received REP (the client paid): cash out, reclassified per docto."""
    return _repost(request, _REP_PAYING)


def _repost(request: PostingContext, row: RepostingRule) -> ProposedJournalEntry | ReviewRequest:
    """§8:192's calculation: one entry, one pair of legs per related document.

    The order is deliberate, and it is what keeps a refusal from looking like a flag. The document
    is keyed and dated first (§8a:209/§8:194), because a refusal that cannot name an entry must flag
    the *document* instead. Then the shape is read — a REP with no complemento states nothing this
    row can compute. Then every payment and related document is checked: a *refusal* ends the
    calculation with no legs, while a *blocking* flag is carried forward beside the legs it does not
    invalidate. Legs are then built per related document, and the entry is either returned as the
    rule's calculation or handed over *with* those legs and the reasons they cannot post together.
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
    pagos = document.pagos
    if pagos is None or not pagos.pagos:
        return _review(
            request,
            row,
            flags=(
                _flag(
                    ReviewFlagType.MISSING_SOURCE_FIELD,
                    "no pago20 complemento: §8:184/185's rows book the related documents a payment"
                    " states, and there are none to read",
                ),
            ),
            detail=_PRECONDITION_DETAIL,
        )
    findings = _check(pagos)
    if findings.refusals:
        return _review(request, row, flags=tuple(findings.refusals), detail=_PRECONDITION_DETAIL)
    lines, unresolved = _legs(row, tuple(findings.related), request.posted_source_uuids)
    blocking = (*findings.blocking, *unresolved)
    if blocking:
        return _review(request, row, flags=blocking, detail=_REVIEW_DETAIL, lines=lines)
    if not findings.related:
        return _review(
            request,
            row,
            flags=(
                _flag(
                    ReviewFlagType.MISSING_SOURCE_FIELD,
                    "the REP's payments state no DoctoRelacionado: §8:192's row is per related"
                    " document, so there is no accounting fact to book",
                ),
            ),
            detail=_NO_RELATED_DOCUMENT_DETAIL,
        )
    return _entry(request, row, lines, entry_date, source_uuid)


def _entry(
    request: PostingContext,
    row: RepostingRule,
    lines: tuple[JournalLine, ...],
    entry_date: date,
    source_uuid: Uuid,
) -> ProposedJournalEntry:
    """§8:169/194: the entry is the REP's own — its contributor, its UUID, its hash, its Fecha."""
    return ProposedJournalEntry(
        rule_id=row.rule_id,
        rule_version=row.rule_version,
        contributor_rfc=request.contributor_rfc,
        source_uuid=source_uuid,
        source_hash=request.document.source_hash,
        entry_date=entry_date,
        lines=lines,
    )


@dataclass(frozen=True)
class _RelatedDocument:
    """One related document's own numbers, once the source is proven to carry them (§8:192).

    ``uuid``/``num_parcialidad`` travel with the amounts because they are what each leg is *keyed*
    by (§8:194), so handing them over narrowed is what stops a leg from being keyed by anything
    else — and ``iva`` is the aggregate of this document's own `impuestos_dr[]`, computed once
    here so no leg can reach for a different reading of the same source.
    """

    uuid: Uuid
    num_parcialidad: int
    amount: NormalizedAmount
    iva: NormalizedAmount


@dataclass
class _Findings:
    """What the source check found: the related documents it can value, and why it cannot value all.

    Two reasons, kept apart because §8:192 needs both. A **refusal** says the document contradicts
    itself or states nothing usable, so no leg may be proposed; a **blocking** flag says the
    deterministic part of the calculation is sound and must be recorded, while something else the
    document states cannot be settled, so the legs travel with the flag and the entry stays
    un-postable. The accumulator is mutable by design: it is a scratch pad for one traversal, and
    the frozen entry it produces is the record.
    """

    related: list[_RelatedDocument] = field(default_factory=list)
    refusals: list[ReviewFlag] = field(default_factory=list)
    blocking: list[ReviewFlag] = field(default_factory=list)


def _check(pagos: Pagos20) -> _Findings:
    """§8:192's source check, over *every* payment and *every* related document.

    Collected in full rather than stopping at the first problem: a human clearing the entry wants
    every reason at once, and the order is the source's own.
    """
    findings = _Findings()
    seen: set[tuple[Uuid, int]] = set()
    for pago in pagos.pagos:
        _check_pago(pago, findings)
        for docto in pago.doctos_relacionados:
            _check_docto(docto, seen, findings)
    return findings


def _check_pago(pago: Pago, findings: _Findings) -> None:
    """§8:192: the payment's own currency, total and tax summary."""
    if not _is_national(pago.moneda_p):
        findings.blocking.append(
            _flag(
                ReviewFlagType.FX_DIFFERENCE_UNCONFIRMED,
                f"the payment is in {pago.moneda_p!r}: §8:193's rate chain has valued nothing here,"
                " and no FX gain or loss is computed on a rate the engine picked",
            )
        )
    _check_pago_totals(pago, findings)
    _check_impuestos_p(pago, findings)


def _check_pago_totals(pago: Pago, findings: _Findings) -> None:
    """`Σ ImpPagado == Pago.Monto` (§8:192), stated in the payment's *own* currency.

    Checked in the payment's currency, before any valuation could obscure it — and skipped when a
    related document's own amount is already unusable, because a sum over a figure nobody could read
    would add a second sentence about the same defect.
    """
    if pago.monto is None:
        findings.refusals.append(
            _flag(
                ReviewFlagType.MISSING_SOURCE_FIELD,
                f"the payment dated {pago.fecha_pago} states no Monto: §8:192 sums what it applied"
                " against the document's own claim",
            )
        )
        return
    stated = tuple(docto.imp_pagado for docto in pago.doctos_relacionados)
    if not stated or any(amount is None or not _is_usable(amount.amount) for amount in stated):
        # A payment naming no related document sums to nothing, and calling that a mismatch would
        # bury the real defect — the row is per related document — under a second sentence; and an
        # unreadable amount is already its own reason, so a sum over it adds nothing.
        return
    applied = sum((amount.amount for amount in stated if amount is not None), Decimal("0"))
    if applied != pago.monto.amount:
        findings.refusals.append(
            _flag(
                ReviewFlagType.REP_INVARIANT_VIOLATION,
                f"Σ ImpPagado {applied} != Pago.Monto {pago.monto.amount}: the payment's own"
                " arithmetic is inconsistent, so the cash it moved is not the cash it applied"
                " (§8:192)",
            )
        )


def _check_impuestos_p(pago: Pago, findings: _Findings) -> None:
    """§8:192: the payment's tax summary must not disagree with the related documents' own taxes.

    `ImpuestosP` restates, at payment level, what the `impuestos_dr[]` entries state per related
    document. §8:192 books the related documents' own taxes, so a disagreement is never resolved
    here — but it is never passed over silently either: the entry is flagged and stays un-postable,
    and a human says which statement the document meant.
    """
    impuestos = pago.impuestos_p
    if impuestos is None:
        return
    if impuestos.retenciones_p:
        findings.blocking.append(
            _flag(
                ReviewFlagType.UNSUPPORTED_RULE,
                "the payment's ImpuestosP states retenciones, which §8:192's reclassification does"
                " not book, and dropping them would misstate the document",
            )
        )
    for traslado in impuestos.traslados_p:
        stated = _stated_tax(pago, traslado.impuesto_p)
        if traslado.importe_p is None:
            findings.refusals.append(
                _flag(
                    ReviewFlagType.MISSING_SOURCE_FIELD,
                    f"ImpuestosP states no ImporteP for impuesto {traslado.impuesto_p}: the"
                    " payment's own tax summary cannot be checked against its related documents",
                )
            )
            continue
        if traslado.importe_p.amount != stated:
            findings.blocking.append(
                _flag(
                    ReviewFlagType.REP_INVARIANT_VIOLATION,
                    f"ImpuestosP states ImporteP {traslado.importe_p.amount} for impuesto"
                    f" {traslado.impuesto_p} while the related documents' ImpuestosDR sum to"
                    f" {stated}: the document's two statements disagree (§8:192)",
                )
            )


def _stated_tax(pago: Pago, impuesto: str) -> Decimal:
    """What the payment's related documents themselves state for one impuesto (§8:192)."""
    return sum(
        (
            traslado.importe_dr.amount
            for docto in pago.doctos_relacionados
            if docto.impuestos_dr is not None
            for traslado in docto.impuestos_dr.traslados_dr
            if traslado.impuesto_dr == impuesto and traslado.importe_dr is not None
        ),
        Decimal("0"),
    )


def _check_docto(docto: DoctoRelacionado, seen: set[tuple[Uuid, int]], findings: _Findings) -> None:
    """§8:192: one related document — its identity, its currency, its amount, its own arithmetic.

    ``seen`` is what makes §8:194's key unique by construction: the same partialidad of the same
    related document stated twice would be two different facts under one key, which the ledger
    cannot hold — and `ProposedJournalEntry` refuses a repeated key outright, so it becomes a reason
    a human can read instead of a raise.
    """
    key = (docto.uuid, docto.num_parcialidad)
    if key in seen:
        findings.refusals.append(
            _flag(
                ReviewFlagType.REP_INVARIANT_VIOLATION,
                f"related document {docto.uuid.value} partialidad {docto.num_parcialidad} is stated"
                " twice: §8:194 identifies a leg by that key, so one fact would appear as two",
            )
        )
    seen.add(key)
    if not _is_national(docto.moneda_dr):
        findings.blocking.append(
            _flag(
                ReviewFlagType.FX_DIFFERENCE_UNCONFIRMED,
                f"related document {docto.uuid.value} is in {docto.moneda_dr!r}: §8:193's rate"
                " chain has valued nothing here, so its amount is not booked at a rate nobody"
                " resolved",
            )
        )
    iva = _docto_iva(docto, findings)
    if docto.imp_pagado is None:
        findings.refusals.append(
            _flag(
                ReviewFlagType.MISSING_SOURCE_FIELD,
                f"related document {docto.uuid.value} states no ImpPagado: §8:192 books the amount"
                " the payment applied, which the source does not state",
            )
        )
        return
    if not _is_usable(docto.imp_pagado.amount):
        findings.refusals.append(
            _flag(
                ReviewFlagType.INVALID_AMOUNT,
                f"ImpPagado {docto.imp_pagado.amount} of related document {docto.uuid.value} is"
                " not a positive, finite amount: a corrupt source amount is never posted (§12)",
            )
        )
        return
    _check_balance_invariant(docto, findings)
    if not _is_national(docto.moneda_dr):
        return  # §8:193: an amount nobody valued is not booked, so it contributes no legs
    findings.related.append(
        _RelatedDocument(
            uuid=docto.uuid,
            num_parcialidad=docto.num_parcialidad,
            amount=docto.imp_pagado,
            iva=iva,
        )
    )


def _check_balance_invariant(docto: DoctoRelacionado, findings: _Findings) -> None:
    """§8:192: when the source states all three balances, they must reconcile.

    Only checked when all three are present — `ImpSaldoAnt − ImpPagado == ImpSaldoInsoluto` is the
    document's own arithmetic, and a REP that omits a term states nothing to check. Filling one in
    from the others would be the engine's own arithmetic masquerading as the source's.
    """
    previous, paid, outstanding = docto.imp_saldo_ant, docto.imp_pagado, docto.imp_saldo_insoluto
    if previous is None or paid is None or outstanding is None:
        return
    if previous.amount - paid.amount != outstanding.amount:
        findings.refusals.append(
            _flag(
                ReviewFlagType.REP_INVARIANT_VIOLATION,
                f"related document {docto.uuid.value} partialidad {docto.num_parcialidad}:"
                f" ImpSaldoAnt {previous.amount} − ImpPagado {paid.amount} !="
                f" ImpSaldoInsoluto {outstanding.amount} (§8:192)",
            )
        )


def _docto_iva(docto: DoctoRelacionado, findings: _Findings) -> NormalizedAmount:
    """The related document's own IVA, aggregated over `impuestos_dr[]` (§8:192).

    Every traslado of impuesto `002` contributes its `ImporteDR`, whatever its rate: one document
    may state several, so reading `impuestos_dr[0]` would book one rate's tax as if it were all of
    them. An exempt traslado moves nothing and contributes nothing; a rate-bearing traslado with no
    `ImporteDR`, a non-finite `ImporteDR` and an unreadable `impuestos_dr[]` are sources the
    reclassification cannot compute from, so each appends a *refusal* and the caller proposes no
    legs at all.

    `retenciones_dr` and a traslado of another impuesto are different: the IVA reclassification is
    still perfectly deterministic from what the document does state, so §8:192 keeps every one of
    those legs and adds a *blocking* `UNSUPPORTED_RULE` — what the document says about taxes it does
    not book is never dropped silently, and the entry it produces can never post.

    An absent `impuestos_dr` is not a missing reading but the source's own statement that the
    related document carries no taxes (§8:192's `impuestos_dr[]` is where a REP states them), so it
    contributes zero rather than a reason.
    """
    impuestos = docto.impuestos_dr
    if impuestos is None:
        return NormalizedAmount(Decimal("0"))
    if impuestos.retenciones_dr:
        findings.blocking.append(
            _flag(
                ReviewFlagType.UNSUPPORTED_RULE,
                f"related document {docto.uuid.value} carries retenciones_dr, which §8:192's"
                " reclassification does not book: the deterministic reclassification legs are kept"
                " and this flag blocks the entry",
            )
        )
    others = sorted(
        set(traslado.impuesto_dr for traslado in impuestos.traslados_dr) - {IVA_IMPUESTO}
    )
    if others:
        findings.blocking.append(
            _flag(
                ReviewFlagType.UNSUPPORTED_RULE,
                f"related document {docto.uuid.value} carries traslados of impuesto"
                f" {', '.join(others)}: §8:192 reclassifies IVA only, so the IVA legs are kept and"
                " this flag blocks the entry",
            )
        )
    total = Decimal("0")
    for traslado in impuestos.traslados_dr:
        if traslado.impuesto_dr != IVA_IMPUESTO:
            continue
        if traslado.importe_dr is None:
            if traslado.tipo_factor_dr == EXENTO:
                continue
            findings.refusals.append(
                _flag(
                    ReviewFlagType.MISSING_SOURCE_FIELD,
                    f"IVA traslado ({traslado.tipo_factor_dr}) of related document"
                    f" {docto.uuid.value} carries no ImporteDR: the reclassified tax cannot be"
                    " computed from it",
                )
            )
            continue
        if not traslado.importe_dr.amount.is_finite():
            findings.refusals.append(
                _flag(
                    ReviewFlagType.INVALID_AMOUNT,
                    f"IVA traslado of related document {docto.uuid.value} carries a non-finite"
                    f" ImporteDR {traslado.importe_dr.amount}: a corrupt source amount is never"
                    " posted (§12)",
                )
            )
            continue
        total += traslado.importe_dr.amount
    return NormalizedAmount(total)


def _legs(
    row: RepostingRule,
    related: tuple[_RelatedDocument, ...],
    posted_source_uuids: frozenset[Uuid],
) -> tuple[tuple[JournalLine, ...], tuple[ReviewFlag, ...]]:
    """§8:184/185's legs, per related document, plus the payments the ledger cannot settle.

    A related document whose original the ledger holds as ``POSTED`` reclassifies: the money leg's
    counterpart is the receivable or payable that original created, and the IVA moves from the role
    the original entry booked it to the role the payment realizes (§8:202). One whose original is
    absent lands on the unapplied-payment account instead and is deliberately **not** reclassified
    (§8:192) — and its reason travels back beside the legs, so the caller records the cash movement
    and the gap together rather than choosing between them. Either way every related document
    contributes the same two balanced money legs, so the entry balances whatever the mix is.
    """
    lines: list[JournalLine] = []
    unresolved: list[ReviewFlag] = []
    for document in related:
        settled = document.uuid in posted_source_uuids
        counterpart = row.money_role if settled else row.unapplied_role
        lines.append(_leg(AccountRole.CLEARING, row.clearing_side, document, document.amount))
        lines.append(_leg(counterpart, row.counterpart_side, document, document.amount))
        if not settled:
            unresolved.append(
                _flag(
                    ReviewFlagType.MISSING_REP_ORIGINAL,
                    f"related document {document.uuid.value} is not in the ledger as a POSTED"
                    " entry: the cash movement is booked against the unapplied-payment account and"
                    " the IVA is neither realized nor reclassified (§8:192)",
                )
            )
            continue
        if document.iva.amount > 0:
            lines.append(_leg(row.released_role, row.released_side, document, document.iva))
            lines.append(_leg(row.realized_role, row.realized_side, document, document.iva))
    return tuple(lines), tuple(unresolved)


def _leg(
    role: AccountRole, side: LineSide, related: _RelatedDocument, amount: NormalizedAmount
) -> JournalLine:
    """One leg, keyed by the related document it settles and its partialidad (§8:194).

    The role is in the key because one related document contributes a money leg *and* an IVA leg;
    the partialidad is in it because the same document may be paid more than once, each payment its
    own fact. The side is deliberately *not* in it: which way a leg faces is how it is read, not
    which fact it is, so a key cannot be redefined by re-orienting a leg.
    """
    return JournalLine(
        account_role=role,
        line_key=f"{role.value}:{related.uuid.value}:{related.num_parcialidad}",
        side=side,
        amount=amount,
    )


def _is_national(currency: str) -> bool:
    """True for the one currency this engine values without an FX decision (§8:193)."""
    return currency.upper() == _NATIONAL_CURRENCY


def _is_usable(amount: Decimal) -> bool:
    """True for an amount a leg may carry: finite and strictly positive (§8:159/§12).

    Non-finite is answered first because a comparison against ``NaN`` is not a decision: only after
    it is known to be a number can the amount be asked whether it moves anything.
    """
    return amount.is_finite() and amount > 0


#: §8's two REP rows (§8:184/185), and the ledger's answer when a related document is absent
#: (§8:192). Both rows move the *same* four roles §8:202 gives them — the income rows' two
#: receivable-side IVA roles for a collection, the purchase rows' two for a payment — which is what
#: "4.5a/4.5b reclassify existing IVA roles" means in the registry. ``metodo_pago`` is the wildcard
#: ``None`` a `P` comprobante's missing header method needs (§8:192).
_REP_COLLECTING = RepostingRule(
    rule_id=RULE_4_5A,
    rule_version=RULE_4_5A_VERSION,
    perspective=Perspective.EMITIDO,
    cash_in=True,
    money_role=AccountRole.CLIENTES,
    unapplied_role=AccountRole.PAGOS_NO_APLICADOS,
    released_role=AccountRole.IVA_TRASLADADO_NO_COBRADO,
    realized_role=AccountRole.IVA_TRASLADADO_COBRADO,
    propose=rule_4_5a,
)

_REP_PAYING = RepostingRule(
    rule_id=RULE_4_5B,
    rule_version=RULE_4_5B_VERSION,
    perspective=Perspective.RECIBIDO,
    cash_in=False,
    money_role=AccountRole.PROVEEDORES,
    unapplied_role=AccountRole.PAGOS_NO_APLICADOS,
    released_role=AccountRole.IVA_ACRED_PENDIENTE,
    realized_role=AccountRole.IVA_ACRED_PAGADO,
    propose=rule_4_5b,
)

#: §8's REP rows, in the order the engine's registry appends them: after `posting.py`'s `I` rows,
#: so selection stays ordered and deterministic and a `P` comprobante is claimed here and nowhere.
REP_ROWS: tuple[RepostingRule, ...] = (_REP_COLLECTING, _REP_PAYING)
