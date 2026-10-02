"""M3 step 4c/4d: the selector — §8's one door per document (§8:161/173).

Every document that reaches the accounting engine gets **one** answer: skip it, propose an
entry, or send it to a human. This file pins the door itself, because the three answers are not
interchangeable (§8:161):

- `SKIPPED` only through §8:161's out-of-scope table — never for a document the engine merely
  cannot handle yet, and never for a perspective nobody determined;
- a proposed entry only from a rule whose §8 row is the document's (type, perspective and
  payment method all three);
- everything else is `NEEDS_REVIEW` with a reason — and, when no rule owns the shape, with no
  entry at all (no §8 number exists to key one by).
"""

from datetime import date
from decimal import Decimal

import pytest

from sat_descarga_masiva.contabilidad.journal import AccountingAssumption, ProposedJournalEntry
from sat_descarga_masiva.contabilidad.rules.contract import PostingContext, ReviewRequest, Skip
from sat_descarga_masiva.contabilidad.rules.posting import (
    POSTING_RULES,
    SUPPORTED_RULES,
    propose,
)
from sat_descarga_masiva.domain.model.fiscal_document import (
    FiscalDocument,
    Impuestos,
    Traslado,
)
from sat_descarga_masiva.domain.model.perspective import Perspective
from sat_descarga_masiva.domain.model.review import ReviewFlagType
from sat_descarga_masiva.domain.model.value_objects import Rfc, Uuid
from sat_descarga_masiva.domain.policy.money import NormalizedAmount

CONTRIBUTOR = Rfc("AAA010101AAA")
RECEPTOR = Rfc("BBB010101BBB")
UUID = Uuid("4e80345d-917f-40bb-a98f-4a73939353c5")
SOURCE_HASH = "a" * 64
FECHA = date(2026, 1, 15)


def _money(text: str) -> NormalizedAmount:
    return NormalizedAmount(Decimal(text))


def _document(
    *,
    tipo: str = "I",
    metodo_pago: str | None = "PUE",
    fecha: date | None = FECHA,
    source_uuid: Uuid | None = UUID,
) -> FiscalDocument:
    """A document with the amounts §8's I-EMITIDO rows need, and the shape under test."""
    return FiscalDocument(
        tipo=tipo,
        version="4.0",
        moneda="MXN",
        tipo_cambio=None,
        emisor_rfc=CONTRIBUTOR,
        receptor_rfc=RECEPTOR,
        conceptos=(),
        impuestos=Impuestos(
            traslados=(Traslado("002", "Tasa", Decimal("0.16"), _money("16.00")),),
            total_traslados=_money("16.00"),
        ),
        total=_money("116.00"),
        source_hash=SOURCE_HASH,
        source_uuid=source_uuid,
        subtotal=_money("100.00"),
        fecha=fecha,
        metodo_pago=metodo_pago,
    )


def _context(
    *, perspective: Perspective = Perspective.EMITIDO, **document: object
) -> PostingContext:
    return PostingContext(
        contributor_rfc=CONTRIBUTOR,
        document=_document(**document),  # type: ignore[arg-type]
        perspective=perspective,
    )


def _refused(proposal: ProposedJournalEntry | Skip | ReviewRequest) -> ReviewRequest:
    assert isinstance(proposal, ReviewRequest), proposal
    return proposal


def _flag_types(proposal: ProposedJournalEntry | Skip | ReviewRequest) -> list[ReviewFlagType]:
    return [flag.flag_type for flag in _refused(proposal).flags]


# --- the row §8:179/180 gives an issued income comprobante ------------------------------


def test_a_pue_document_is_ruled_by_4_1() -> None:
    proposal = propose(_context(metodo_pago="PUE"))
    assert isinstance(proposal, ProposedJournalEntry)
    assert proposal.rule_id == "4.1"
    assert proposal.assumptions == (AccountingAssumption.ASSUMED_PUE,)
    assert "clearing" in {line.line_key for line in proposal.lines}


def test_a_ppd_document_is_ruled_by_4_2() -> None:
    proposal = propose(_context(metodo_pago="PPD"))
    assert isinstance(proposal, ProposedJournalEntry)
    assert proposal.rule_id == "4.2"
    assert proposal.assumptions == ()


@pytest.mark.parametrize("metodo_pago", [None, "", "XYZ", "pue"])
def test_an_undecidable_payment_method_is_reviewed_never_guessed(metodo_pago: str | None) -> None:
    """§8:171: presuming PUE would book a payment the document does not state."""
    proposal = propose(_context(metodo_pago=metodo_pago))
    assert _flag_types(proposal) == [ReviewFlagType.MISSING_SOURCE_FIELD]
    assert _refused(proposal).entry is None  # no rule owns the shape: nothing to key


# --- §8:161's skips, and nothing else ---------------------------------------------------


@pytest.mark.parametrize(
    "tipo,perspective,rule_id",
    [
        ("T", Perspective.EMITIDO, "4.12"),
        ("T", Perspective.RECIBIDO, "4.12"),
        ("N", Perspective.RECIBIDO, "4.11"),
        ("R", Perspective.RECIBIDO, "4.14"),
    ],
)
def test_the_out_of_scope_table_is_the_only_way_to_a_skip(
    tipo: str, perspective: Perspective, rule_id: str
) -> None:
    proposal = propose(_context(tipo=tipo, perspective=perspective, metodo_pago=None))
    assert isinstance(proposal, Skip)
    assert proposal.entry.rule_id == rule_id


@pytest.mark.parametrize(
    "tipo,perspective",
    [
        ("E", Perspective.EMITIDO),
        ("E", Perspective.RECIBIDO),
        ("P", Perspective.EMITIDO),
        ("P", Perspective.RECIBIDO),
        ("I", Perspective.RECIBIDO),
        ("N", Perspective.EMITIDO),
        ("R", Perspective.EMITIDO),
        ("X", Perspective.EMITIDO),
        ("I", Perspective.UNDETERMINED),
    ],
)
def test_an_unbuilt_row_is_reviewed_and_never_silently_skipped(
    tipo: str, perspective: Perspective
) -> None:
    """§8:161: "Unsupported is never silently SKIPPED" — and §8:163 for an undetermined side."""
    proposal = propose(_context(tipo=tipo, perspective=perspective, metodo_pago=None))
    assert not isinstance(proposal, Skip)
    assert _flag_types(proposal) == [ReviewFlagType.UNSUPPORTED_RULE]
    assert _refused(proposal).entry is None


# --- the registry ----------------------------------------------------------------------


def test_the_registry_holds_the_two_issued_income_rows() -> None:
    """§8:175's I-EMITIDO rows, and the wiring the validator checks a rule against (§8:159)."""
    assert {row.rule_id for row in POSTING_RULES} == {"4.1", "4.2"}
    assert {row.rule_id: row.rule_version for row in POSTING_RULES} == SUPPORTED_RULES
    assert {row.metodo_pago for row in POSTING_RULES} == {"PUE", "PPD"}


def test_the_rows_claim_disjoint_shapes() -> None:
    """Deterministic selection (§8:159): no document is two rules' at once."""
    contexts = [
        _context(metodo_pago="PUE"),
        _context(metodo_pago="PPD"),
        _context(metodo_pago=None),
        _context(tipo="E", metodo_pago=None),
        _context(tipo="T", metodo_pago=None),
        _context(perspective=Perspective.RECIBIDO),
    ]
    for context in contexts:
        assert sum(1 for row in POSTING_RULES if row.claims(context)) <= 1
