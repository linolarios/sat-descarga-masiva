"""M3 step 3: rules 4.10/4.13 — the payroll/withholding *drafts* (§8a:205), and nothing more.

`N` (payroll) and `R` (withholding) are **not** out of scope when the contributor *issued* them:
they are documents M3 can neither parse nor account yet, so they are `NEEDS_REVIEW` with **zero
lines** and a reason — a human completes the draft (§8a:205). The received side is different and
lives in `scope.py`: an `N`/`R` **recibido** is a deliberate `SKIPPED` (4.11/4.14). These tests pin
both edges of that pair:

- the inside: exactly the ``N``/``R`` **emitido** pairs are drafts, enumerated over every type ×
  perspective, so widening the table costs a failing test;
- the outside: the ``N``/``R`` **recibido** skips, an ``UNDETERMINED`` side, the ``I``/``E``/``P``
  rows and any unsupported type are **not** drafts — they belong to `scope` or the posting rules.

A draft is still an *auditable* fact (§8:169) whenever it can be one: it carries the posting
identity and is keyed as a zero-line entry, exactly like a skip. When the document cannot be keyed
(no `Fecha`, no TFD UUID) the flag lands on the document instead — a draft is never a silent drop,
and never a crash.
"""

from datetime import date
from decimal import Decimal

import pytest

from sat_descarga_masiva.contabilidad.journal import PostingState
from sat_descarga_masiva.contabilidad.rules.contract import PostingContext, ReviewRequest, Skip
from sat_descarga_masiva.contabilidad.rules.drafts import (
    DRAFT_RULES,
    RULE_4_10,
    RULE_4_13,
    RULE_VERSION,
    draft,
)
from sat_descarga_masiva.contabilidad.rules.posting import propose
from sat_descarga_masiva.contabilidad.rules.scope import out_of_scope
from sat_descarga_masiva.domain.enums.comprobante import TipoComprobante
from sat_descarga_masiva.domain.model.fiscal_document import FiscalDocument, Impuestos
from sat_descarga_masiva.domain.model.perspective import Perspective
from sat_descarga_masiva.domain.model.review import ReviewFlagType
from sat_descarga_masiva.domain.model.value_objects import Rfc, Uuid

CONTRIBUTOR = Rfc("AAA010101AAA")
RECEPTOR = Rfc("BBB010101BBB")
UUID = Uuid("4e80345d-917f-40bb-a98f-4a73939353c5")
SOURCE_HASH = "a" * 64
FECHA = date(2026, 1, 15)
MAPPING_VERSION = "2026.01"

_DECIDED = (Perspective.EMITIDO, Perspective.RECIBIDO)
_EVERY_PERSPECTIVE = (*_DECIDED, Perspective.UNDETERMINED)


def _document(
    *,
    tipo: str = "N",
    fecha: date | None = FECHA,
    source_uuid: Uuid | None = UUID,
) -> FiscalDocument:
    return FiscalDocument(
        tipo=tipo,
        version="4.0",
        moneda="MXN",
        tipo_cambio=None,
        emisor_rfc=CONTRIBUTOR,
        receptor_rfc=RECEPTOR,
        conceptos=(),
        impuestos=Impuestos(),
        total=None,
        source_hash=SOURCE_HASH,
        source_uuid=source_uuid,
        fecha=fecha,
    )


def _context(
    *,
    tipo: str = "N",
    perspective: Perspective = Perspective.EMITIDO,
    fecha: date | None = FECHA,
    source_uuid: Uuid | None = UUID,
) -> PostingContext:
    return PostingContext(
        contributor_rfc=CONTRIBUTOR,
        document=_document(tipo=tipo, fecha=fecha, source_uuid=source_uuid),
        perspective=perspective,
    )


def _drafted_documents() -> set[tuple[str, Perspective]]:
    """Every (type letter, perspective) pair this module calls a draft."""
    return {
        (tipo.value, perspective)
        for tipo in TipoComprobante
        for perspective in _EVERY_PERSPECTIVE
        if draft(_context(tipo=tipo.value, perspective=perspective)) is not None
    }


# --- the documented set -----------------------------------------------------


def test_the_drafts_are_exactly_the_emitido_payroll_and_withholding_pairs() -> None:
    """§8a:205: `N`/`R` **emitido** are the only drafts — nothing else is claimed."""
    assert _drafted_documents() == {
        ("N", Perspective.EMITIDO),
        ("R", Perspective.EMITIDO),
    }


def test_the_draft_table_names_rules_4_10_and_4_13() -> None:
    """§8:188/190: the two draft rows carry their §8 numbers, once each."""
    assert {rule.rule_id for rule in DRAFT_RULES} == {RULE_4_10, RULE_4_13}
    assert {rule.rule_id: rule.rule_version for rule in DRAFT_RULES} == {
        RULE_4_10: RULE_VERSION,
        RULE_4_13: RULE_VERSION,
    }


@pytest.mark.parametrize(
    "tipo,flag_type,rule_id",
    [
        ("N", ReviewFlagType.PAYROLL_DRAFT_UNSUPPORTED, RULE_4_10),
        ("R", ReviewFlagType.RETENCION_DRAFT_UNSUPPORTED, RULE_4_13),
    ],
)
def test_an_emitido_draft_is_reviewed_with_its_own_flag(
    tipo: str, flag_type: ReviewFlagType, rule_id: str
) -> None:
    """§8a:205: the draft is `NEEDS_REVIEW` with a reason — never a skip, never a guess."""
    proposal = draft(_context(tipo=tipo, perspective=Perspective.EMITIDO))
    assert isinstance(proposal, ReviewRequest)
    assert [flag.flag_type for flag in proposal.flags] == [flag_type]
    assert proposal.entry is not None
    assert proposal.entry.rule_id == rule_id


# --- the outside of the set -------------------------------------------------


@pytest.mark.parametrize("tipo", ["N", "R"])
def test_the_received_side_is_a_skip_not_a_draft(tipo: str) -> None:
    """§8:161/§8a:205: `N`/`R` **recibido** are deliberate skips (4.11/4.14), not drafts."""
    context = _context(tipo=tipo, perspective=Perspective.RECIBIDO)
    assert draft(context) is None
    assert isinstance(out_of_scope(context), Skip)


@pytest.mark.parametrize("tipo", ["N", "R"])
def test_an_undetermined_side_is_never_a_draft(tipo: str) -> None:
    """§8:163: an undetermined perspective is a review case, not a rule-declared draft."""
    assert draft(_context(tipo=tipo, perspective=Perspective.UNDETERMINED)) is None


@pytest.mark.parametrize("tipo", ["I", "E", "P", "T"])
@pytest.mark.parametrize("perspective", _DECIDED)
def test_a_type_owned_elsewhere_is_not_a_draft(tipo: str, perspective: Perspective) -> None:
    """The posting rows and the out-of-scope table own these; the draft table never claims them."""
    assert draft(_context(tipo=tipo, perspective=perspective)) is None


@pytest.mark.parametrize("tipo", ["X", "", "n", "N "])
def test_an_unsupported_document_type_is_never_a_draft(tipo: str) -> None:
    """§8:161: an unsupported type goes to review as-is — and no letter is normalized here."""
    assert draft(_context(tipo=tipo, perspective=Perspective.EMITIDO)) is None


# --- the draft is an auditable fact (§8:169) --------------------------------


@pytest.mark.parametrize("tipo", ["N", "R"])
def test_the_draft_is_dated_on_the_documents_own_fecha(tipo: str) -> None:
    """§8a:209: `FiscalDocument.fecha` is what an entry is dated by — never "today"."""
    proposal = draft(_context(tipo=tipo, perspective=Perspective.EMITIDO))
    assert isinstance(proposal, ReviewRequest)
    assert proposal.entry is not None
    assert proposal.entry.entry_date == FECHA


@pytest.mark.parametrize("tipo", ["N", "R"])
def test_the_draft_carries_the_documents_posting_identity(tipo: str) -> None:
    """§8:194: the persisted draft row is keyed by this, so it cannot be guessed later."""
    proposal = draft(_context(tipo=tipo, perspective=Perspective.EMITIDO))
    assert isinstance(proposal, ReviewRequest)
    entry = proposal.entry
    assert entry is not None
    assert entry.contributor_rfc == CONTRIBUTOR
    assert entry.source_uuid == UUID
    assert entry.source_hash == SOURCE_HASH
    assert entry.rule_version == RULE_VERSION
    assert entry.posting_state is PostingState.PROPOSED  # the rule cannot say NEEDS_REVIEW itself


@pytest.mark.parametrize("tipo", ["N", "R"])
def test_every_draft_carries_zero_lines_and_balances(tipo: str) -> None:
    """§8a:205: zero lines — the draft is a placeholder a human fills, not a partial entry."""
    proposal = draft(_context(tipo=tipo, perspective=Perspective.EMITIDO))
    assert isinstance(proposal, ReviewRequest)
    entry = proposal.entry
    assert entry is not None
    assert entry.lines == ()
    assert entry.is_balanced()
    assert entry.debe_total() == Decimal("0")
    assert entry.haber_total() == Decimal("0")
    assert entry.fingerprint(MAPPING_VERSION).line_key == ""
    assert entry.line_fingerprints(MAPPING_VERSION) == ()


@pytest.mark.parametrize("tipo", ["N", "R"])
def test_an_unrecordable_draft_flags_the_document_rather_than_the_entry(tipo: str) -> None:
    """§8:158/159: an undatable/unidentified draft is still a draft — flagged, never dropped."""
    for document in (
        _context(tipo=tipo, perspective=Perspective.EMITIDO, fecha=None),
        _context(tipo=tipo, perspective=Perspective.EMITIDO, source_uuid=None),
    ):
        proposal = draft(document)
        assert isinstance(proposal, ReviewRequest)
        assert proposal.entry is None
        assert proposal.flags  # a draft is never silent


# --- the selector routes the drafts (§8:161's one door) ---------------------


@pytest.mark.parametrize(
    "tipo,flag_type,rule_id",
    [
        ("N", ReviewFlagType.PAYROLL_DRAFT_UNSUPPORTED, RULE_4_10),
        ("R", ReviewFlagType.RETENCION_DRAFT_UNSUPPORTED, RULE_4_13),
    ],
)
def test_propose_routes_an_emitido_draft_to_its_rule(
    tipo: str, flag_type: ReviewFlagType, rule_id: str
) -> None:
    """§8:161: the selector reaches the draft table — not the generic unsupported refusal."""
    proposal = propose(_context(tipo=tipo, perspective=Perspective.EMITIDO))
    assert not isinstance(proposal, Skip)
    assert isinstance(proposal, ReviewRequest)
    assert [flag.flag_type for flag in proposal.flags] == [flag_type]
    assert proposal.entry is not None
    assert proposal.entry.rule_id == rule_id
