"""M3 step 3: rules 4.11/4.12/4.14 — §8:161's exact `SKIPPED` set, and nothing more.

`SKIPPED` is a claim that **no human decision is needed**, so it is the one posting state
that must never be reached by accident. These tests pin both edges of the set:

- the inside: exactly ``T`` (any decided perspective), ``N`` **recibido**, ``R``
  **recibido** — enumerated exhaustively over every type × perspective pair, so widening
  the set costs a failing test;
- the outside: the ``N``/``R`` **emitido** drafts (§8a:205's `NEEDS_REVIEW` with zero
  lines), an ``UNDETERMINED`` perspective (§8:163), an unsupported document type
  (§8:161) and a document that cannot be dated or identified (§8:158/159) all return
  `None` — they are the validator's refusals, never a silent skip.

A skip is also an *auditable* fact (§8:169): every one of them is dated, identified and
fingerprintable, which is asserted here rather than assumed by the ledger.
"""

from datetime import date
from decimal import Decimal

import pytest

from sat_descarga_masiva.contabilidad.journal import PostingState
from sat_descarga_masiva.contabilidad.rules.contract import PostingContext, Skip
from sat_descarga_masiva.contabilidad.rules.scope import (
    OUT_OF_SCOPE_RULES,
    RULE_VERSION,
    out_of_scope,
)
from sat_descarga_masiva.domain.enums.comprobante import TipoComprobante
from sat_descarga_masiva.domain.model.fiscal_document import FiscalDocument, Impuestos
from sat_descarga_masiva.domain.model.perspective import Perspective
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
    tipo: str = "I",
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
    tipo: str = "I",
    perspective: Perspective = Perspective.EMITIDO,
    fecha: date | None = FECHA,
    source_uuid: Uuid | None = UUID,
) -> PostingContext:
    return PostingContext(
        contributor_rfc=CONTRIBUTOR,
        document=_document(tipo=tipo, fecha=fecha, source_uuid=source_uuid),
        perspective=perspective,
    )


def _skipped_documents() -> set[tuple[str, Perspective]]:
    """Every (type letter, perspective) pair this module calls out of scope."""
    return {
        (tipo.value, perspective)
        for tipo in TipoComprobante
        for perspective in _EVERY_PERSPECTIVE
        if out_of_scope(_context(tipo=tipo.value, perspective=perspective)) is not None
    }


# --- the documented set -----------------------------------------------------


@pytest.mark.parametrize("perspective", _DECIDED)
def test_a_traslado_is_skipped_as_rule_4_12_from_either_perspective(
    perspective: Perspective,
) -> None:
    """§8:161: tipo T is out of scope whichever side of it the contributor is on."""
    skip = out_of_scope(_context(tipo="T", perspective=perspective))
    assert isinstance(skip, Skip)
    assert skip.entry.rule_id == "4.12"
    assert skip.entry.lines == ()
    assert "traslado" in skip.detail


def test_a_payroll_receipt_is_skipped_as_rule_4_11() -> None:
    """§8:161: `N` *recibido* is the payroll receipt — deliberately not accounted."""
    skip = out_of_scope(_context(tipo="N", perspective=Perspective.RECIBIDO))
    assert isinstance(skip, Skip)
    assert skip.entry.rule_id == "4.11"
    assert skip.entry.lines == ()


def test_a_withholding_receipt_is_skipped_as_rule_4_14() -> None:
    """§8:161: `R` *recibido* is the acuse — it feeds DIOT, not the ledger."""
    skip = out_of_scope(_context(tipo="R", perspective=Perspective.RECIBIDO))
    assert isinstance(skip, Skip)
    assert skip.entry.rule_id == "4.14"
    assert skip.entry.lines == ()


def test_the_skipped_set_is_exactly_the_three_cases_of_the_spec() -> None:
    """§8:161 enumerates `SKIPPED` exactly; a fourth case would be a spec change."""
    assert _skipped_documents() == {
        ("T", Perspective.EMITIDO),
        ("T", Perspective.RECIBIDO),
        ("N", Perspective.RECIBIDO),
        ("R", Perspective.RECIBIDO),
    }


def test_the_skip_table_is_three_distinct_versioned_rules() -> None:
    """Rule ids are §8 numbers and are persisted, so they are ledger format, not a label."""
    assert [rule.rule_id for rule in OUT_OF_SCOPE_RULES] == ["4.11", "4.12", "4.14"]
    assert RULE_VERSION.strip()
    assert all(rule.detail.strip() for rule in OUT_OF_SCOPE_RULES)
    assert all(rule.perspectives for rule in OUT_OF_SCOPE_RULES)
    assert all(Perspective.UNDETERMINED not in rule.perspectives for rule in OUT_OF_SCOPE_RULES)


# --- the neighbours that must not be skipped --------------------------------


@pytest.mark.parametrize("tipo", ["N", "R"])
def test_the_payroll_and_withholding_drafts_are_refusals_not_skips(tipo: str) -> None:
    """§8a:205: `N`/`R` *emitido* are drafts needing a human — zero lines, NEEDS_REVIEW."""
    assert out_of_scope(_context(tipo=tipo, perspective=Perspective.EMITIDO)) is None


@pytest.mark.parametrize("tipo", ["I", "E", "P", "T", "N", "R"])
def test_an_undetermined_perspective_is_never_silently_skipped(tipo: str) -> None:
    """§8:163: an undetermined perspective is a review case, never a skip."""
    assert out_of_scope(_context(tipo=tipo, perspective=Perspective.UNDETERMINED)) is None


@pytest.mark.parametrize("tipo", ["X", "", "i", "I "])
def test_an_unsupported_document_type_is_never_silently_skipped(tipo: str) -> None:
    """§8:161: "Unsupported is never silently SKIPPED" — and no letter is normalized here."""
    assert out_of_scope(_context(tipo=tipo)) is None


@pytest.mark.parametrize("tipo", ["I", "E", "P"])
@pytest.mark.parametrize("perspective", _DECIDED)
def test_an_in_scope_type_is_left_to_the_posting_rules(tipo: str, perspective: Perspective) -> None:
    """The types the posting rules own never reach the skip door — from either side."""
    assert out_of_scope(_context(tipo=tipo, perspective=perspective)) is None


def test_a_document_that_cannot_be_dated_or_identified_is_not_skipped() -> None:
    """§8:158/159: a skip must be recordable (dated, fingerprintable) — else review it."""
    assert out_of_scope(_context(tipo="T", fecha=None)) is None
    assert out_of_scope(_context(tipo="T", source_uuid=None)) is None


# --- the skip is an auditable fact (§8:169) ---------------------------------


def test_the_skip_is_dated_on_the_documents_own_fecha() -> None:
    """§8a:209: `FiscalDocument.fecha` is what an entry is dated by — never "today"."""
    skip = out_of_scope(_context(tipo="T"))
    assert isinstance(skip, Skip)
    assert skip.entry.entry_date == FECHA


def test_the_skip_carries_the_documents_posting_identity() -> None:
    """§8:194: the persisted `SKIPPED` row is keyed by this, so it cannot be guessed later."""
    skip = out_of_scope(_context(tipo="N", perspective=Perspective.RECIBIDO))
    assert isinstance(skip, Skip)
    entry = skip.entry
    assert entry.contributor_rfc == CONTRIBUTOR
    assert entry.source_uuid == UUID
    assert entry.source_hash == SOURCE_HASH
    assert entry.rule_version == RULE_VERSION
    assert entry.posting_state is PostingState.PROPOSED  # the rule cannot say SKIPPED itself


@pytest.mark.parametrize(
    "tipo,perspective",
    [
        ("T", Perspective.EMITIDO),
        ("T", Perspective.RECIBIDO),
        ("N", Perspective.RECIBIDO),
        ("R", Perspective.RECIBIDO),
    ],
)
def test_every_skip_is_recordable(tipo: str, perspective: Perspective) -> None:
    """Zero legs still balance and the entry keys cleanly: the ledger can store the fact."""
    skip = out_of_scope(_context(tipo=tipo, perspective=perspective))
    assert isinstance(skip, Skip)
    assert skip.entry.is_balanced()
    assert skip.entry.debe_total() == Decimal("0")
    assert skip.entry.haber_total() == Decimal("0")
    assert skip.entry.fingerprint(MAPPING_VERSION).line_key == ""
    assert skip.entry.line_fingerprints(MAPPING_VERSION) == ()


# --- the document-type vocabulary the table depends on ----------------------


def test_the_document_type_vocabulary_is_the_six_spec_letters() -> None:
    """§8:177: exactly six letters; the lookup answers None instead of guessing."""
    assert {tipo.value for tipo in TipoComprobante} == {"I", "E", "T", "N", "P", "R"}
    assert TipoComprobante.of("T") is TipoComprobante.TRASLADO
    assert TipoComprobante.of("Z") is None
    assert TipoComprobante.of("") is None
