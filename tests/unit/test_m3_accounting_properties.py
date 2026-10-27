"""M3/T4a — the accounting invariants, as properties rather than examples (§12).

Every other M3 module pins *a* case. This one states what must hold for **any** coherent source
the engine claims, so a future rule or a refactor cannot satisfy the examples and break the
arithmetic:

- **P1** — a `POSTED` entry balances, and every leg it carries is a positive, finite `Decimal`;
- **P2** — an ineligible fiscal state never silently produces the ordinary `POSTED` fact;
- **P3** — the `I` family (4.1–4.4) conserves debits, credits, base and IVA, with the roles and
  sides §8:179–182 approves;
- **P4/P5** — a REP's own arithmetic (`Σ ImpPagado == Pago.Monto`, `ImpSaldoAnt − ImpPagado ==
  ImpSaldoInsoluto`) is what the accounting consumes, and an incoherent one never posts;
- **P6** — no monetary scalar becomes a `float`, and the one serializer round-trips exactly;
- **P7** — the valuation evidence a posting records is the one its currency requires (§8a:231).

Two things this module deliberately does **not** do. It never asserts that a given input *must*
post — a property is an implication (`POSTED ⇒ …`), so a refusal is always a pass. And it never
weakens an assertion to make a counterexample go away: a failure here means the engine or the
property is wrong, and that is reported, not papered over.

Production is not touched by this module. The generation is exact (`Decimal` cents, no `float`
anywhere), so shrinking yields a readable counterexample rather than a rounding artefact.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from sat_descarga_masiva.application.ports.accounting import AccountMapping, FXSource
from sat_descarga_masiva.contabilidad.fx import FXRateResolutionPolicy
from sat_descarga_masiva.contabilidad.journal import (
    JournalEntryRecord,
    LineSide,
    PostingState,
    ProposedJournalEntry,
)
from sat_descarga_masiva.contabilidad.roles import AccountRole
from sat_descarga_masiva.contabilidad.rules.contract import PostingContext, ReviewRequest, Skip
from sat_descarga_masiva.contabilidad.rules.posting import SUPPORTED_RULES, propose
from sat_descarga_masiva.contabilidad.validator import (
    PostingDecision,
    PostingEligibilityValidator,
)
from sat_descarga_masiva.contabilidad.valuation import value_entry
from sat_descarga_masiva.domain.model.fiscal_document import (
    FiscalDocument,
    FiscalDocumentStatus,
)
from sat_descarga_masiva.domain.model.perspective import Perspective
from sat_descarga_masiva.domain.model.raw_cfd import (
    RawCfd,
    RawConcepto,
    RawDoctoRelacionado,
    RawImpuestos,
    RawImpuestosDR,
    RawPago,
    RawPagos20,
    RawTraslado,
    RawTrasladoDR,
)
from sat_descarga_masiva.domain.model.review import ReviewFlagType
from sat_descarga_masiva.domain.model.value_objects import Rfc, Uuid
from sat_descarga_masiva.domain.policy.money import (
    MoneyPolicy,
    NormalizedAmount,
    amount_as_text,
    amount_from_text,
)
from sat_descarga_masiva.fiscal.parse import build_fiscal_document

#: The client whose books every document is booked into — emisor when emitted, receptor when
#: received, because §8:194 keys a posting by the client as well as the source.
CONTRIBUTOR = Rfc("AAA010101AAA")
COUNTERPARTY = Rfc("BBB010101BBB")
SOURCE_UUID = Uuid("4E80345D-917F-40BB-A98F-4A73939353C5")
SOURCE_HASH = "a" * 64
FECHA_TEXT = "2026-01-15T10:30:00"
FECHA = date(2026, 1, 15)
RECORDED_AT = datetime(2026, 6, 30, 12, 0, tzinfo=UTC)
MAPPING_VERSION = "2026.01"
REP_UUID = Uuid("333E4567-E89B-12D3-A456-426614174000")
PAID_UUIDS = (
    Uuid("111E4567-E89B-12D3-A456-426614174000"),
    Uuid("222E4567-E89B-12D3-A456-426614174000"),
)

#: Every role the generated documents can reach, so an `UNMAPPED_ACCOUNT` refusal never turns a
#: property into "nothing posted, therefore true". The accounts are arbitrary but distinct.
_ACCOUNTS = {
    AccountRole.INGRESOS: "401-001",
    AccountRole.CLEARING: "102-099",
    AccountRole.CLIENTES: "105-001",
    AccountRole.PROVEEDORES: "201-001",
    AccountRole.GASTO: "601-001",
    AccountRole.INVENTARIO: "115-001",
    AccountRole.IVA_TRASLADADO_COBRADO: "208-001",
    AccountRole.IVA_TRASLADADO_NO_COBRADO: "209-001",
    AccountRole.IVA_ACRED_PAGADO: "118-001",
    AccountRole.IVA_ACRED_PENDIENTE: "119-001",
    AccountRole.PAGOS_NO_APLICADOS: "102-098",
}

#: §8:179–182's approved shape, stated as the roles each row must produce. Written out rather than
#: read from `POSTING_RULES`: a property pins the *approved semantics*, not whatever the registry's
#: internal metadata happens to say today.
_EMITTED_BASE_ROLE = AccountRole.INGRESOS
_IVA_ROLE = {
    "4.1": AccountRole.IVA_TRASLADADO_COBRADO,
    "4.2": AccountRole.IVA_TRASLADADO_NO_COBRADO,
    "4.3": AccountRole.IVA_ACRED_PAGADO,
    "4.4": AccountRole.IVA_ACRED_PENDIENTE,
}
#: `4.3`/`4.4` book the *classified* base — `Gasto` or `Inventario` — never a guessed default.
_CLASSIFIED_ROWS = frozenset({"4.3", "4.4"})
_ORDINARY_RULE_IDS = frozenset({"4.1", "4.2", "4.3", "4.4"})

#: The national currency §8:193 books without a valuation decision.
_NATIONAL = "MXN"

_MONEY = MoneyPolicy()

#: Fifty examples per property: enough to exercise the branches this module cares about without
#: turning the unit suite into a benchmark. `deadline=None` because a CI runner's first call pays
#: for imports and shrinking, which is not what a property is measuring.
_PROPERTY = settings(deadline=None, max_examples=50)


class _Provider:
    """A `MappingProvider` over a chart that resolves every role the rules can name."""

    def mapping_for(self, client_rfc: Rfc) -> AccountMapping:  # noqa: ARG002 - fixed chart
        return AccountMapping(MAPPING_VERSION, _ACCOUNTS)


_VALIDATOR = PostingEligibilityValidator(_Provider(), supported_rules=SUPPORTED_RULES)


def _from_cents(cents: int) -> Decimal:
    """Exact 2-dp money from an integer number of cents — never through `float`."""
    return Decimal(cents).scaleb(-2)


def _text(amount: Decimal) -> str:
    """The source's own text form, so the parser reads exactly what a CFDI would state."""
    return format(amount, "f")


def _quantize(amount: Decimal) -> Decimal:
    """`MoneyPolicy`'s MXN scale, so a generated source is arithmetically coherent."""
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class _Income:
    """One generated `I` document, with the facts a property needs to state its implication."""

    context: PostingContext
    base: Decimal
    iva: Decimal
    base_role: AccountRole

    @property
    def document(self) -> FiscalDocument:
        return self.context.document


def _income_document(
    *,
    perspective: Perspective,
    metodo_pago: str,
    subtotal: Decimal,
    descuento: Decimal,
    iva: Decimal,
    tasa: Decimal,
    moneda: str,
    tipo_cambio: str | None,
    status: FiscalDocumentStatus,
) -> FiscalDocument:
    """One coherent `I` comprobante as *source* facts, in the currency the property needs.

    `Total := SubTotal − Descuento + IVA` by construction, so every generated document is
    admissible input: a refusal in a property is the engine's answer, never a fixture defect.
    """
    base = subtotal - descuento
    traslados = () if iva == 0 else (RawTraslado("002", "Tasa", _text(tasa), _text(iva)),)
    emitted = perspective is Perspective.EMITIDO
    raw = RawCfd(
        tipo="I",
        version="4.0",
        moneda=moneda,
        tipo_cambio=tipo_cambio,
        emisor_rfc=(CONTRIBUTOR if emitted else COUNTERPARTY).value,
        receptor_rfc=(COUNTERPARTY if emitted else CONTRIBUTOR).value,
        conceptos=(RawConcepto("84111506", "1", _text(base), _text(base), None),),
        descuento=_text(descuento),
        impuestos=RawImpuestos(
            traslados=traslados,
            total_traslados=None if iva == 0 else _text(iva),
        ),
        total=_text(base + iva),
        uuid=SOURCE_UUID.value,
        subtotal=_text(subtotal),
        fecha=FECHA_TEXT,
        metodo_pago=metodo_pago,
    )
    parsed = build_fiscal_document(raw, SOURCE_HASH)
    assert parsed.document is not None
    # §8:159 — only a *resolved* vigente source can post; the parse's own default is UNKNOWN.
    return replace(parsed.document, status=status)


@st.composite
def _incomes(
    draw: st.DrawFn,
    *,
    currencies: st.SearchStrategy[str] | None = None,
    statuses: st.SearchStrategy[FiscalDocumentStatus] | None = None,
) -> _Income:
    """A coherent `I` document drawn from the 4.1–4.4 family (§8:179–182).

    Restricted to `Tipo = I` with both perspectives and both payment methods: this *is* the
    4.1–4.4 family, so a `Tipo = E` example is never generated merely to fall through. Discounts
    are drawn across their range and one non-zero case is pinned below, so `base = SubTotal −
    Descuento` is genuinely exercised rather than accidentally left at zero.
    """
    perspective = draw(st.sampled_from([Perspective.EMITIDO, Perspective.RECIBIDO]))
    metodo_pago = draw(st.sampled_from(["PUE", "PPD"]))
    subtotal_cents = draw(st.integers(min_value=1_000, max_value=5_000_000))
    descuento_cents = draw(st.integers(min_value=0, max_value=min(999, subtotal_cents - 100)))
    tasa = draw(st.sampled_from([Decimal("0.16"), Decimal("0.08"), Decimal("0")]))
    moneda = draw(currencies if currencies is not None else st.just(_NATIONAL))
    status = draw(statuses if statuses is not None else st.just(FiscalDocumentStatus.VIGENTE))
    tipo_cambio = (
        None
        if moneda == _NATIONAL
        else draw(st.sampled_from(["17.5", "17.53", "19.4321", "1.0001"]))
    )
    subtotal = _from_cents(subtotal_cents)
    descuento = _from_cents(descuento_cents)
    base = subtotal - descuento
    iva = _quantize(base * tasa) if tasa else Decimal("0.00")
    classification = draw(st.sampled_from([AccountRole.GASTO, AccountRole.INVENTARIO]))
    document = _income_document(
        perspective=perspective,
        metodo_pago=metodo_pago,
        subtotal=subtotal,
        descuento=descuento,
        iva=iva,
        tasa=tasa,
        moneda=moneda,
        tipo_cambio=tipo_cambio,
        status=status,
    )
    context = PostingContext(
        contributor_rfc=CONTRIBUTOR,
        document=document,
        perspective=perspective,
        # §8a:207 — the receiving rows read the client's classification; the emitting ones need
        # none. Supplied for both so one generator covers the whole family.
        classification=classification,
    )
    return _Income(
        context=context,
        base=base,
        iva=iva,
        base_role=(_EMITTED_BASE_ROLE if perspective is Perspective.EMITIDO else classification),
    )


@dataclass(frozen=True)
class _Outcome:
    """What the rule answered and what the gate returned, in that order (§8:161/173/226).

    ``proposal`` is kept as the rule wrote it — an entry, a skip or a refusal — because several
    properties need to distinguish "no entry was proposed" from "an entry was proposed and
    refused", and that distinction is the rule's answer, not the gate's.
    """

    proposal: ProposedJournalEntry | Skip | ReviewRequest
    decision: PostingDecision

    def entry_proposal(self) -> ProposedJournalEntry:
        """The proposal as an entry, for the properties that only run when one was posted."""
        assert isinstance(self.proposal, ProposedJournalEntry)
        return self.proposal


def _run(context: PostingContext) -> _Outcome:
    """The production sequence for one document: propose → value → decide (§8a:226).

    The conversion is resolved only for a `ProposedJournalEntry`, exactly as
    `ExecuteAccountingUseCase.execute` does, so a property measures the path production takes and
    never a shortcut that skips valuation.
    """
    proposal = propose(context)
    valued = (
        value_entry(proposal, document=context.document, policy=FXRateResolutionPolicy())
        if isinstance(proposal, ProposedJournalEntry)
        else None
    )
    return _Outcome(
        proposal=proposal,
        decision=_VALIDATOR.decide(context, proposal, valued=valued),
    )


def _decide(context: PostingContext) -> PostingDecision:
    """The verdict alone, for the properties that only read it."""
    return _run(context).decision


def _flag_types(decision: PostingDecision) -> set[ReviewFlagType]:
    return {flag.flag_type for flag in decision.review_flags}


# --- the source: a REP built the way a payment complemento states it (§8:192) -----------------


def _rep_traslado(base: Decimal, iva: Decimal) -> RawTrasladoDR:
    return RawTrasladoDR(
        base_dr=_text(base), impuesto_dr="002", tipo_factor_dr="Tasa", importe_dr=_text(iva)
    )


@dataclass(frozen=True)
class _Rep:
    """One generated REP with the facts P4/P5 need to state their implications."""

    context: PostingContext
    paid_by_key: dict[tuple[str, str], Decimal]
    applied: Decimal
    monto: Decimal
    coherent_monto: bool
    coherent_balance: bool


def _rep_document(
    *, perspective: Perspective, pago: RawPago, status: FiscalDocumentStatus
) -> FiscalDocument:
    """One `P` comprobante as source facts: the REP's header states no amount of its own."""
    emitted = perspective is Perspective.EMITIDO
    raw = RawCfd(
        tipo="P",
        version="4.0",
        moneda=_NATIONAL,
        tipo_cambio=None,
        emisor_rfc=(CONTRIBUTOR if emitted else COUNTERPARTY).value,
        receptor_rfc=(COUNTERPARTY if emitted else CONTRIBUTOR).value,
        conceptos=(RawConcepto("84111506", "1", "0.00", "0.00", None),),
        impuestos=RawImpuestos(),
        total="0.00",
        uuid=REP_UUID.value,
        subtotal="0.00",
        fecha=FECHA_TEXT,
        metodo_pago=None,
        pagos=RawPagos20(version="2.0", pagos=(pago,)),
    )
    parsed = build_fiscal_document(raw, SOURCE_HASH)
    assert parsed.document is not None
    return replace(parsed.document, status=status)


@st.composite
def _reps(draw: st.DrawFn) -> _Rep:
    """A REP whose *own* arithmetic is drawn, coherent or not (§8:192).

    Two independent defects are generated, because they are two different sentences in the rule:
    the payment's total may disagree with what it applied (`P4`), and one related document's
    balance may not reconcile (`P5`). A drawn ``delta`` of zero is the coherent branch, so both
    the implication and its contrapositive are exercised by the same property.
    """
    perspective = draw(st.sampled_from([Perspective.EMITIDO, Perspective.RECIBIDO]))
    count = draw(st.integers(min_value=1, max_value=2))
    paid_by_key: dict[tuple[str, str], Decimal] = {}
    doctos: list[RawDoctoRelacionado] = []
    applied = Decimal("0.00")
    coherent_balance = True
    for index in range(count):
        pagado = _from_cents(draw(st.integers(min_value=100, max_value=100_000)))
        extra = _from_cents(draw(st.integers(min_value=0, max_value=50_000)))
        saldo_ant = pagado + extra
        delta = _from_cents(draw(st.sampled_from([0, 0, 1, 250])))
        insoluto = saldo_ant - pagado + delta
        coherent_balance = coherent_balance and delta == 0
        uuid = PAID_UUIDS[index]
        paid_by_key[(uuid.value, "1")] = pagado
        applied += pagado
        doctos.append(
            RawDoctoRelacionado(
                id_documento=uuid.value,
                moneda_dr=_NATIONAL,
                num_parcialidad="1",
                imp_saldo_ant=_text(saldo_ant),
                imp_pagado=_text(pagado),
                objeto_imp_dr="01",
                imp_saldo_insoluto=_text(insoluto),
                impuestos_dr=RawImpuestosDR(
                    traslados_dr=(_rep_traslado(pagado, _quantize(pagado * Decimal("0.16"))),)
                ),
            )
        )
    monto_delta = _from_cents(draw(st.sampled_from([0, 0, 1, 250])))
    monto = applied + monto_delta
    pago = RawPago(
        fecha_pago="2026-02-01T09:00:00",
        forma_de_pago_p="03",
        moneda_p=_NATIONAL,
        monto=_text(monto),
        doctos_relacionados=tuple(doctos),
    )
    document = _rep_document(
        perspective=perspective, pago=pago, status=FiscalDocumentStatus.VIGENTE
    )
    context = PostingContext(
        contributor_rfc=CONTRIBUTOR,
        document=document,
        perspective=perspective,
        # §8:192 — the ledger, not the rule, decides whether an original was posted. Both
        # documents are booked, so the settled path is the one these properties measure.
        posted_source_uuids=frozenset(PAID_UUIDS[:count]),
    )
    return _Rep(
        context=context,
        paid_by_key=paid_by_key,
        applied=applied,
        monto=monto,
        coherent_monto=monto == applied,
        coherent_balance=coherent_balance,
    )


# --- P1: a posted entry is balanced, in positive finite Decimals (§8:158/159) ----------------


@given(_incomes())
@_PROPERTY
def test_p1_a_posted_entry_balances_in_positive_finite_decimals(income: _Income) -> None:
    """§8:158/§12: whatever the source states, what posts balances — and never as a float.

    An implication only: a refusal is a pass. The amounts are checked for their *strict* type
    because §4 bans `float` in fiscal/accounting code, and a `Decimal` that merely compares equal
    would not prove that.
    """
    decision = _decide(income.context)

    if decision.posting_state is not PostingState.POSTED:
        assert decision.review_flags, "a PROPOSED decision always carries a reason (§8:167)"
        return

    assert decision.entry is not None
    entry = decision.entry
    assert entry.is_balanced()
    debe = sum(
        (line.amount.amount for line in entry.lines if line.side is LineSide.DEBE), Decimal("0")
    )
    haber = sum(
        (line.amount.amount for line in entry.lines if line.side is LineSide.HABER), Decimal("0")
    )
    assert debe == haber
    for line in entry.lines:
        assert type(line.amount.amount) is Decimal
        assert line.amount.amount.is_finite()
        assert line.amount.amount > 0
        assert line.line_key


# --- P2: an ineligible fiscal state never silently posts (§8:159/194) ------------------------


@given(
    _incomes(
        statuses=st.sampled_from(
            [
                FiscalDocumentStatus.VIGENTE,
                FiscalDocumentStatus.CANCELLED,
                FiscalDocumentStatus.UNKNOWN,
            ]
        )
    )
)
@_PROPERTY
def test_p2_an_ineligible_fiscal_state_never_produces_the_ordinary_posting(
    income: _Income,
) -> None:
    """§8:159/194: the ordinary fact is reachable only from a resolved *vigente* source.

    A cancelled source answers through §8:194's own rows (4.15a skip, 4.15b reversal) and never
    by quietly re-booking the shape row; an unresolved one waits for the metadata join. The
    exhaustive field-by-field immutability of a historical record stays with the 4.15 tests —
    this property states the state-to-verdict implication and nothing more.
    """
    decision = _decide(income.context)
    status = income.document.status

    if decision.posting_state is PostingState.POSTED:
        assert status is FiscalDocumentStatus.VIGENTE

    if status is FiscalDocumentStatus.CANCELLED:
        assert decision.posting_state is not PostingState.POSTED
        if decision.entry is not None:
            # A cancelled source is never re-booked as the ordinary 4.1–4.4 fact: it is either
            # the 4.15a skip or the 4.15b compensating reversal.
            assert decision.entry.rule_id not in _ORDINARY_RULE_IDS

    if status is FiscalDocumentStatus.UNKNOWN:
        assert decision.posting_state is not PostingState.POSTED
        assert decision.review_flags


# --- P3: the I family conserves debits, credits, base and IVA (§8:179–182) -------------------


@given(_incomes())
@_PROPERTY
def test_p3_a_posted_income_conserves_its_debits_credits_base_and_iva(income: _Income) -> None:
    """§8:179–182: both sides carry the document's total, the base is net of the discount, and
    the IVA is the document's own traslados.

    `Σ Debe == Σ Haber == Total` is the conservation the approved shape guarantees — and it is
    *each* side, not the sum of all legs, which would only be true of a single-entry entry.
    """
    decision = _decide(income.context)

    if decision.posting_state is not PostingState.POSTED:
        return

    entry = decision.entry
    assert entry is not None
    total = income.document.total
    assert total is not None
    assert entry.debe_total() == total.amount
    assert entry.haber_total() == total.amount

    # §8:175/191 + §8a:204: `base = SubTotal − Descuento`, on the side the row approves.
    base_side = LineSide.HABER if entry.rule_id in {"4.1", "4.2"} else LineSide.DEBE
    base_role = income.base_role if entry.rule_id in _CLASSIFIED_ROWS else _EMITTED_BASE_ROLE
    base_legs = [line for line in entry.lines if line.account_role is base_role]
    assert len(base_legs) == 1, f"rule {entry.rule_id} books one base leg"
    assert base_legs[0].side is base_side
    assert base_legs[0].amount.amount == income.base

    iva_legs = [line for line in entry.lines if line.account_role is _IVA_ROLE[entry.rule_id]]
    if income.iva == 0:
        # §8:179/181: an exempt document gets no IVA leg — a zero leg is not a leg.
        assert iva_legs == []
    else:
        assert len(iva_legs) == 1
        assert iva_legs[0].side is base_side
        assert iva_legs[0].amount.amount == income.iva


def test_a_discounted_income_books_the_net_base() -> None:
    """§8a:204: a non-zero `Descuento` is deducted before the base is booked.

    Pinned as a worked example as well as generated, because a property whose discount happened
    to shrink to zero would never have exercised the subtraction at all.
    """
    document = _income_document(
        perspective=Perspective.EMITIDO,
        metodo_pago="PUE",
        subtotal=Decimal("1000.00"),
        descuento=Decimal("250.00"),
        iva=Decimal("120.00"),  # 16 % of the *net* 750.00
        tasa=Decimal("0.16"),
        moneda=_NATIONAL,
        tipo_cambio=None,
        status=FiscalDocumentStatus.VIGENTE,
    )
    context = PostingContext(
        contributor_rfc=CONTRIBUTOR, document=document, perspective=Perspective.EMITIDO
    )

    decision = _decide(context)

    assert decision.posting_state is PostingState.POSTED
    assert decision.entry is not None
    ingresos = [line for line in decision.entry.lines if line.account_role is AccountRole.INGRESOS]
    assert [line.amount.amount for line in ingresos] == [Decimal("750.00")]
    assert decision.entry.haber_total() == Decimal("870.00")


# --- P4: a REP consumes exactly what the payment applied (§8:192) ----------------------------


@given(_reps())
@_PROPERTY
def test_p4_a_posted_rep_consumes_exactly_what_the_payment_applied(rep: _Rep) -> None:
    """§8:192: `Σ ImpPagado == Pago.Monto`, and the ledger books that sum — never more.

    The payment's own arithmetic is checked from both ends: an incoherent `Monto` is refused whole
    (`REP_INVARIANT_VIOLATION`, so nothing posts), and a coherent one is what the cash legs add up
    to. That second half is the conservation — a REP that posted a different figure would be
    inventing a movement the document never stated.
    """
    decision = _decide(rep.context)

    if not rep.coherent_monto:
        assert decision.posting_state is not PostingState.POSTED
        assert ReviewFlagType.REP_INVARIANT_VIOLATION in _flag_types(decision)
        return

    assert rep.applied == rep.monto
    if decision.posting_state is not PostingState.POSTED:
        return

    entry = decision.entry
    assert entry is not None
    clearing = sum(
        (line.amount.amount for line in entry.lines if line.account_role is AccountRole.CLEARING),
        Decimal("0"),
    )
    assert clearing == rep.applied
    assert entry.debe_total() == entry.haber_total()


# --- P5: a REP's balance equation is the source's own arithmetic (§8:192) --------------------


@given(_reps())
@_PROPERTY
def test_p5_a_posted_rep_respects_the_related_documents_balance_equation(rep: _Rep) -> None:
    """§8:192: `ImpSaldoAnt − ImpPagado == ImpSaldoInsoluto`, or nothing posts.

    A document whose three balances do not reconcile is refused whole — the engine does not
    complete the arithmetic for it. When they do reconcile, every money leg states exactly the
    `ImpPagado` of the document its key names, so the accounting never consumes more than the
    source says was paid.

    Duplicate `(UUID, NumParcialidad)` identity is deliberately *not* asserted here: it is its own
    invariant, and `test_m3_rule_4_5.py`'s
    `test_the_same_partialidad_of_the_same_document_twice_is_refused` already owns it
    deterministically.
    """
    decision = _decide(rep.context)

    if not rep.coherent_balance:
        assert decision.posting_state is not PostingState.POSTED
        assert ReviewFlagType.REP_INVARIANT_VIOLATION in _flag_types(decision)
        return

    if decision.posting_state is not PostingState.POSTED:
        return

    entry = decision.entry
    assert entry is not None
    clearing = [line for line in entry.lines if line.account_role is AccountRole.CLEARING]
    assert len(clearing) == len(rep.paid_by_key)
    for line in clearing:
        _, uuid, parcialidad = line.line_key.split(":")
        assert line.amount.amount == rep.paid_by_key[(uuid, parcialidad)]


# --- P6: no monetary scalar becomes a float (§4/§12) ----------------------------------------


@given(st.integers(min_value=1, max_value=10**10))
@_PROPERTY
def test_p6_an_amount_round_trips_through_the_one_serializer_exactly(cents: int) -> None:
    """§4: money is TEXT, serialized once, and comes back as the same `Decimal`."""
    amount = NormalizedAmount(_from_cents(cents))

    assert type(amount.amount) is Decimal
    assert amount_from_text(amount_as_text(amount)) == amount


@given(_incomes())
@_PROPERTY
def test_p6_a_decision_and_its_record_keep_every_monetary_scalar_a_decimal(
    income: _Income,
) -> None:
    """§12: nothing in the decision → record path turns money into a `float`.

    The same strict type check as P1, carried through `to_record`, because the persisted aggregate
    is the last place a `float` could enter unnoticed: the stores serialize TEXT.
    """
    decision = _decide(income.context)

    if decision.posting_state is not PostingState.POSTED:
        return

    record: JournalEntryRecord = decision.to_record(recorded_at=RECORDED_AT)
    assert decision.entry is not None
    for line in decision.entry.lines:
        assert type(line.amount.amount) is Decimal
    for persisted in record.lines:
        assert type(persisted.amount.amount) is Decimal
        assert persisted.amount.amount == amount_from_text(amount_as_text(persisted.amount)).amount
    # §8a:231 — the evidence is either wholly absent or wholly present, never half-recorded.
    evidence = (record.valuation_date, record.valuation_source, record.valuation_rate)
    assert evidence == (None, None, None) or all(item is not None for item in evidence)


# --- P7: the evidence a posting records is the one its currency needs (§8a:226/231) --------


@given(_incomes(currencies=st.sampled_from([_NATIONAL, "USD", "CAD"])))
@_PROPERTY
def test_p7_a_posted_entry_records_exactly_the_valuation_its_currency_needs(
    income: _Income,
) -> None:
    """§8:193/§8a:231: `MXN` records no valuation; anything else records the one that posted it.

    This is the property that decides whether "an `MXN` posting carrying valuation evidence" is
    reachable through the production sequence: `value_entry` is asked first, exactly as the use
    case asks it, so when this holds the state is *not* reachable through `value_entry`.

    For a converted posting the pesos are re-derived from the **proposal's own amounts** through
    `MoneyPolicy` — the same policy the conversion used — rather than by repeating the rounding
    rule here, so the assertion cannot drift from the implementation it is checking.
    """
    outcome = _run(income.context)
    decision = outcome.decision

    if decision.posting_state is not PostingState.POSTED:
        return

    document = income.document
    record = decision.to_record(recorded_at=RECORDED_AT)
    assert document.fecha == FECHA, "the valuation date is the document's own Fecha (§8a:231)"

    if document.moneda.strip().upper() == _NATIONAL:
        assert decision.valuation is None
        assert (
            record.valuation_date,
            record.valuation_source,
            record.valuation_rate,
        ) == (None, None, None)
        return

    valuation = decision.valuation
    assert valuation is not None, "a foreign-currency posting states the rate it was valued at"
    assert type(valuation.rate) is Decimal
    assert valuation.rate.is_finite() and valuation.rate > 0
    assert valuation.valuation_date == document.fecha
    assert isinstance(valuation.source, FXSource)
    assert record.valuation_date == valuation.valuation_date
    assert record.valuation_source is valuation.source
    assert record.valuation_rate == valuation.rate

    assert decision.entry is not None
    proposal = outcome.entry_proposal()
    assert len(decision.entry.lines) == len(proposal.lines)
    for converted, proposed in zip(decision.entry.lines, proposal.lines, strict=True):
        expected = _MONEY.normalize(proposed.amount.amount * valuation.rate, _NATIONAL)
        assert isinstance(expected, NormalizedAmount), "MXN always has a MoneyPolicy scale"
        assert converted.line_key == proposed.line_key
        assert converted.side is proposed.side
        assert converted.account_role is proposed.account_role
        assert converted.amount.amount == expected.amount
    assert decision.entry.is_balanced()
