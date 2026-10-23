"""Rules 4.1–4.4 — `I` comprobantes on both sides (§8:179/180/181/182), and the one door.

§8's table gives an income comprobante four rows: two when it is *issued* (`EMITIDO`) and two
when it is *received* (`RECIBIDO` — a purchase), one per payment method. The issued two first:

- **4.1** (PUE): the SAT presumption is a single-exhibition payment, so the cash leg is booked
  to `CLEARING` and the entry says so — `ASSUMED_PUE` (§8:171). It is an *accounting
  assumption*, never payment evidence (§8a:208), and it travels as an
  `AccountingAssumption` **on the entry**, not inside a ``line_key``: the key is §8:194's
  structural identity for the leg, so an assumption that a later bank reconciliation may
  promote to `SUPPORTED_BY_BANK` cannot redefine it.
- **4.2** (PPD): nothing is presumed paid, so the receivable is booked to `CLIENTES`.

The received two are the mirror with the sides flipped — a purchase:

- **4.3** (PUE): `DR Gasto/Inv=base · DR IVA Acred. Pagado=IVA · CR Clearing=Total`, `ASSUMED_PUE`;
- **4.4** (PPD): `DR Gasto/Inv=base · DR IVA Pendiente=IVA · CR Proveedores=Total`.

Which of `Gasto`/`Inventario` the base lands on is not in the document: it is the client's
``ClaveProdServ → AccountingCategory → AccountRole`` classification (§8a:196/207), carried on the
`PostingContext` as ``classification`` and resolved by `AccountMapping.classify` before the rule
runs. A document that does not classify to one postable role — no ClaveProdServ, an unclassified
product, capital goods, or concepts that disagree — is reviewed whole, never defaulted to Gasto.

All four compute the same base — §8:175/191/181/182 under M3's `discount_policy = net` (§8a:204):
`base = SubTotal − Descuento` — and take the IVA from the document's own `002` traslados, one leg
however many rates contribute to it.

A document whose shape is not that one gets no entry rather than a partial one. §8:173's
preconditions are explicit here: an unbookable tax shape (retenciones, or a traslado of
another impuesto) is `UNSUPPORTED_RULE`; an absent `SubTotal`/`Total` or an unreadable
traslado is `MISSING_SOURCE_FIELD`; a corrupt or self-inconsistent amount is `INVALID_AMOUNT`;
a document that cannot be dated or identified is refused before anything is computed. Nothing
posts unless `Total == base + IVA` holds, which is what makes the proposed entry balance.

`propose(request)` is the engine's one door per document — skip, propose, or review
(§8:161/173) — so the pipeline asks one question and gets one of the three answers. It consults
§8:161's out-of-scope table (`scope.py`) first, then §8a:205's draft table (`drafts.py`, the `N`/`R`
**emitido** drafts) — the two "tables of decided refusals", adjacent so neither can quietly grow.
The rows it walks are this module's `I` rows plus §8's REP rows from `reposting.py` (4.5a/4.5b), in
one ordered tuple: a `P` comprobante is claimed by its own row and by nothing here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sat_descarga_masiva.contabilidad.classification import (
    ClassificationRefusal,
)
from sat_descarga_masiva.contabilidad.journal import (
    AccountingAssumption,
    JournalLine,
    LineSide,
    ProposedJournalEntry,
)
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
    ReviewRequest,
    RuleRow,
    _flag,
    _review,
)
from sat_descarga_masiva.contabilidad.rules.credit_notes import (
    CREDIT_NOTE_ROWS,
    credit_note_review,
)
from sat_descarga_masiva.contabilidad.rules.drafts import draft
from sat_descarga_masiva.contabilidad.rules.reposting import REP_ROWS
from sat_descarga_masiva.contabilidad.rules.scope import out_of_scope
from sat_descarga_masiva.domain.enums.comprobante import TipoComprobante
from sat_descarga_masiva.domain.model.perspective import Perspective
from sat_descarga_masiva.domain.model.review import ReviewFlagType

RULE_4_1 = "4.1"
RULE_4_2 = "4.2"
RULE_4_3 = "4.3"
RULE_4_4 = "4.4"

#: Bumped only when the rule's *decision* changes: §8:194 keys the posting by it, so a historical
#: entry stays explainable against the rule version that produced it.
RULE_4_1_VERSION = "1"
RULE_4_2_VERSION = "1"
RULE_4_3_VERSION = "1"
RULE_4_4_VERSION = "1"


#: What a rule (and the selector below) may answer with (§8:173). Declared in `contract.py`,
#: beside the refusal helper and the `RuleRow` protocol, because `reposting.py` answers with the
#: same three words: a rule module declares rows and computes legs, and shares the rest.
@dataclass(frozen=True)
class PostingRule:
    """One row of §8's table: the document shape it books, where the money lands, and how.

    ``money_role``/``money_key`` are the leg the money moves on — `CLEARING` for a PUE comprobante
    (§8:171/181) or `CLIENTES`/`PROVEEDORES` for a PPD one (§8:180/182) — and its *side* is the
    row's direction: a debit when the comprobante is issued (money in), a credit when it is
    received (money out). ``iva_role`` is the IVA line's role and follows the same two
    distinctions (traslado cobrado/no cobrado on the issued side, acreditable pagado/pendiente on
    the received side). ``money_key`` is structural (§8:194); ``assumptions`` is the row's
    evidence (§8:171): a PUE row states `ASSUMED_PUE` on the entry, a PPD row states nothing, and
    neither puts an assumption into a leg's key.
    """

    rule_id: str
    rule_version: str
    tipo: TipoComprobante
    perspective: Perspective
    metodo_pago: str | None
    money_role: AccountRole
    money_key: str
    iva_role: AccountRole
    propose: Callable[[PostingContext], Proposal]
    assumptions: tuple[AccountingAssumption, ...] = ()

    def claims(self, request: PostingContext) -> bool:
        """True when §8's row is the document's: its type, its perspective and its method.

        ``metodo_pago`` is compared, not normalized, because §8:179–182 choose their row by the
        document's own word (§8:171's whole reason for refusing an absent one) — but the wildcard
        ``None`` means *any* method, which is how a row for a comprobante that carries no header
        ``MetodoPago`` at all is expressed without inventing one on the document.
        """
        document = request.document
        return (
            TipoComprobante.of(document.tipo) is self.tipo
            and request.perspective is self.perspective
            and (self.metodo_pago is None or document.metodo_pago == self.metodo_pago)
        )


def rule_4_1(request: PostingContext) -> ProposedJournalEntry | ReviewRequest:
    """§8:179 — an issued PUE income comprobante: the cash leg lands on the clearing account."""
    return _issued_income(request, _RULE_4_1)


def rule_4_2(request: PostingContext) -> ProposedJournalEntry | ReviewRequest:
    """§8:180 — an issued PPD income comprobante: nothing is presumed paid, the client owes."""
    return _issued_income(request, _RULE_4_2)


def rule_4_3(request: PostingContext) -> ProposedJournalEntry | ReviewRequest:
    """§8:181 — a received PUE purchase: the classified base and creditable IVA are debits."""
    return _received_purchase(request, _RULE_4_3)


def rule_4_4(request: PostingContext) -> ProposedJournalEntry | ReviewRequest:
    """§8:182 — a received PPD purchase: nothing is presumed paid, the vendor is owed."""
    return _received_purchase(request, _RULE_4_4)


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
    amounts = base_amounts(request, row)
    if isinstance(amounts, ReviewRequest):
        return amounts
    return _issued_entry(request, row, amounts)


def _received_purchase(
    request: PostingContext, row: PostingRule
) -> ProposedJournalEntry | ReviewRequest:
    """§8:181/182's calculation: the shape, then the classification, then the preconditions.

    The row decides the money and IVA roles; the *classification* (§8a:196/207) decides the base
    role, and a document that does not classify to one postable role is reviewed whole. The
    classification is checked before the amounts so the refusal names the purchase's own
    precondition first — a received comprobante whose products are unclassified is not a purchase
    this engine can book, whatever its arithmetic says.
    """
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
    classification = request.classification
    if classification is None:
        return _review(
            request,
            row,
            flags=(
                _flag(
                    ReviewFlagType.MISSING_SOURCE_FIELD,
                    "no classification was resolved for this received purchase: §8a:207's"
                    " ClaveProdServ → AccountingCategory → AccountRole answer is a precondition",
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
    amounts = base_amounts(request, row)
    if isinstance(amounts, ReviewRequest):
        return amounts
    return _purchase_entry(request, row, amounts, classification)


def _issued_entry(
    request: PostingContext, row: PostingRule, amounts: BaseAmounts
) -> ProposedJournalEntry:
    """§8:179/180's legs: the money lands on the row's account, base and IVA are the credits.

    The entry balances by construction: `Total == base + IVA` was proven before this point, so
    the debit total equals the credit total — and §8:159 checks it again before posting. The
    row's ``assumptions`` travel on the entry (§8:171): what was presumed, recorded beside the
    legs whose keys stay structural (§8:194).
    """
    lines = [
        JournalLine(
            account_role=row.money_role,
            line_key=row.money_key,
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


def _purchase_entry(
    request: PostingContext, row: PostingRule, amounts: BaseAmounts, base_role: AccountRole
) -> ProposedJournalEntry:
    """§8:181/182's legs: the classified base and the creditable IVA debit; the money credits.

    The mirror of :func:`_issued_entry`, balancing by the same proof: `Total == base + IVA` was
    checked in `base_amounts`, so the debits equal the money credit. ``base_role`` is the
    client's classification of the document's products (§8a:207) — `Gasto` or `Inventario`, never
    guessed — and the row's ``assumptions`` travel on the entry (§8:171).
    """
    lines = [
        JournalLine(
            account_role=base_role,
            line_key=BASE_KEY,
            side=LineSide.DEBE,
            amount=amounts.base,
        )
    ]
    if amounts.iva.amount > 0:
        lines.append(
            JournalLine(
                account_role=row.iva_role,
                line_key=IVA_KEY,
                side=LineSide.DEBE,
                amount=amounts.iva,
            )
        )
    lines.append(
        JournalLine(
            account_role=row.money_role,
            line_key=row.money_key,
            side=LineSide.HABER,
            amount=amounts.total,
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


#: §8's rows this module builds, one per (type, perspective, method). The rows are disjoint in
#: what they claim, so selection is deterministic, and the tuple *is* the engine's registry of
#: postable rules — the validator is wired with it (§8:159), so a rule id or version that is not
#: here cannot post. Each rule function books its own row and nothing else.
#:
#: §8's REP rows (4.5a/4.5b) are built in `reposting.py` beside the calculation they share — one
#: entry per comprobante, one set of legs per related document — and appended here so there is
#: exactly one ordered registry and therefore exactly one selection order.
_RULE_4_1 = PostingRule(
    rule_id=RULE_4_1,
    rule_version=RULE_4_1_VERSION,
    tipo=TipoComprobante.INGRESO,
    perspective=Perspective.EMITIDO,
    metodo_pago=METODO_PUE,
    money_role=AccountRole.CLEARING,
    money_key=CLEARING_KEY,
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
    money_role=AccountRole.CLIENTES,
    money_key=TOTAL_KEY,
    iva_role=AccountRole.IVA_TRASLADADO_NO_COBRADO,
    propose=rule_4_2,
)

_RULE_4_3 = PostingRule(
    rule_id=RULE_4_3,
    rule_version=RULE_4_3_VERSION,
    tipo=TipoComprobante.INGRESO,
    perspective=Perspective.RECIBIDO,
    metodo_pago=METODO_PUE,
    money_role=AccountRole.CLEARING,
    money_key=CLEARING_KEY,
    iva_role=AccountRole.IVA_ACRED_PAGADO,
    propose=rule_4_3,
    assumptions=(AccountingAssumption.ASSUMED_PUE,),
)

_RULE_4_4 = PostingRule(
    rule_id=RULE_4_4,
    rule_version=RULE_4_4_VERSION,
    tipo=TipoComprobante.INGRESO,
    perspective=Perspective.RECIBIDO,
    metodo_pago=METODO_PPD,
    money_role=AccountRole.PROVEEDORES,
    money_key=TOTAL_KEY,
    iva_role=AccountRole.IVA_ACRED_PENDIENTE,
    propose=rule_4_4,
)

POSTING_RULES: tuple[RuleRow, ...] = (
    _RULE_4_1,
    _RULE_4_2,
    _RULE_4_3,
    _RULE_4_4,
    *REP_ROWS,
    *CREDIT_NOTE_ROWS,
)

#: The engine's registry as the validator is wired with it: ``rule_id`` → current version.
SUPPORTED_RULES: dict[str, str] = {row.rule_id: row.rule_version for row in POSTING_RULES}


def propose(request: PostingContext) -> Proposal:
    """The engine's one door per document (§8:161/173): skip it, propose it, or review it.

    ``SKIPPED`` is reachable only through §8:161's out-of-scope table; §8a:205's drafts
    (`drafts.py`, the ``N``/``R`` **emitido** cases) come next, and they are keyed zero-line
    `ReviewRequest`s. A document no rule claims and no draft table holds is then a
    *document-level* `ReviewRequest` — never a silent skip, and never a guess: no rule owns its
    shape, so there is no §8 number to key an entry by and the review fact belongs to the
    document itself (§8:167).
    """
    skip = out_of_scope(request)
    if skip is not None:
        return skip
    drafted = draft(request)
    if drafted is not None:
        return drafted

    for row in POSTING_RULES:
        if row.claims(request):
            return row.propose(request)
    document = request.document
    if TipoComprobante.of(document.tipo) is TipoComprobante.EGRESO and request.perspective in (
        Perspective.EMITIDO,
        Perspective.RECIBIDO,
    ):
        return credit_note_review(request)

    if TipoComprobante.of(document.tipo) is TipoComprobante.INGRESO and request.perspective in (
        Perspective.EMITIDO,
        Perspective.RECIBIDO,
    ):
        pair = (
            "4.1 (PUE) and 4.2 (PPD)"
            if request.perspective is Perspective.EMITIDO
            else "4.3 (PUE) and 4.4 (PPD)"
        )
        return ReviewRequest(
            flags=(
                _flag(
                    ReviewFlagType.MISSING_SOURCE_FIELD,
                    f"MetodoPago {document.metodo_pago!r}: §8:179–182 choose between {pair} by"
                    " it, and presuming PUE would book a payment the document does not state"
                    " (§8:171)",
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
