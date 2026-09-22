"""M2.4a: promote raw CFDI identity and header facts into FiscalDocument.

The typed layer records source facts (TFD identity, SubTotal/Descuento, FormaPago,
MetodoPago, RegimenFiscalReceptor, CfdiRelacionados) without deriving any
accounting meaning: no accounting base, no PUE/PPD branch, no relation semantics.
"""

from dataclasses import MISSING, fields
from decimal import Decimal
from pathlib import Path

import pytest

from sat_descarga_masiva.domain.model.fiscal_document import (
    CfdiRelacionados,
    FiscalDocument,
    Impuestos,
    ParseOutcome,
)
from sat_descarga_masiva.domain.model.raw_cfd import (
    RawCfd,
    RawCfdiRelacionados,
    RawConcepto,
    RawImpuestos,
)
from sat_descarga_masiva.domain.model.review import ReviewFlagType
from sat_descarga_masiva.domain.model.value_objects import Rfc, Uuid
from sat_descarga_masiva.domain.policy.money import NormalizedAmount
from sat_descarga_masiva.fiscal.parse import build_fiscal_document
from sat_descarga_masiva.infrastructure.fiscal.satcfdi_fiscal_parser import SatcfdiFiscalParser

_FIXTURES = Path(__file__).parent.parent / "fixtures"
_SOURCE_HASH = "a" * 64
_TFD_UUID = "123E4567-E89B-12D3-A456-426614174000"
_RELACION_UUID_1 = "11111111-1111-1111-1111-111111111111"
_RELACION_UUID_2 = "22222222-2222-2222-2222-222222222222"

_NEW_FIELDS = (
    "source_uuid",
    "subtotal",
    "descuento",
    "forma_pago",
    "metodo_pago",
    "regimen_fiscal_receptor",
    "cfdi_relacionados",
)

_parser = SatcfdiFiscalParser()


def _build(name: str):
    raw = _parser.parse((_FIXTURES / name).read_bytes(), _SOURCE_HASH)
    return raw, build_fiscal_document(raw, _SOURCE_HASH)


def _doc(name: str) -> FiscalDocument:
    _, result = _build(name)
    assert result.document is not None
    return result.document


def _raw(**overrides: object) -> RawCfd:
    base = dict(
        tipo="I",
        version="4.0",
        moneda="MXN",
        tipo_cambio=None,
        emisor_rfc="AAA010101AAA",
        receptor_rfc="BBB010101BBB",
        conceptos=(RawConcepto("01010101", "1", "100.00", "100.00", None),),
        impuestos=RawImpuestos(),
        total="116.00",
    )
    base.update(overrides)
    return RawCfd(**base)  # type: ignore[arg-type]


def _built(**overrides: object) -> FiscalDocument:
    result = build_fiscal_document(_raw(**overrides), _SOURCE_HASH)
    assert result.document is not None
    return result.document


# --- identity ---------------------------------------------------------------


def test_tfd_uuid_is_promoted_to_source_uuid() -> None:
    assert _doc("cfdi_tfd_4_0.xml").source_uuid == Uuid(_TFD_UUID)


def test_source_uuid_is_canonical_uppercase_and_hyphenated() -> None:
    source_uuid = _doc("cfdi_tfd_4_0.xml").source_uuid
    assert source_uuid is not None
    assert source_uuid.value == _TFD_UUID


def test_lowercase_uuid_is_canonicalized() -> None:
    assert Uuid("123e4567-e89b-12d3-a456-426614174000").value == _TFD_UUID


def test_non_canonical_uuid_representation_is_canonicalized() -> None:
    assert Uuid("{123e4567-e89b-12d3-a456-426614174000}").value == _TFD_UUID
    assert Uuid("123e4567e89b12d3a456426614174000").value == _TFD_UUID


def test_canonical_uuid_equality_holds_across_representation_case() -> None:
    assert Uuid("123e4567-e89b-12d3-a456-426614174000") == Uuid(_TFD_UUID)


def test_absent_uuid_stays_none() -> None:
    assert _doc("cfdi_ingreso_4_0.xml").source_uuid is None


def test_source_hash_can_never_become_source_uuid() -> None:
    """Absent TFD means absent identity: the hash is provenance, not a UUID."""
    doc = _doc("cfdi_ingreso_4_0.xml")
    assert doc.source_uuid is None
    assert doc.source_hash == _SOURCE_HASH
    assert doc.source_uuid != doc.source_hash


def test_malformed_uuid_raises_value_error() -> None:
    with pytest.raises(ValueError):
        build_fiscal_document(_raw(uuid="not-a-uuid"), _SOURCE_HASH)


# --- header facts -----------------------------------------------------------


def test_subtotal_is_promoted_as_normalized_amount() -> None:
    assert _doc("cfdi_ingreso_4_0.xml").subtotal == NormalizedAmount(Decimal("100.00"))


def test_descuento_is_promoted_as_normalized_amount() -> None:
    assert _doc("cfdi_egreso_4_0.xml").descuento == NormalizedAmount(Decimal("10.00"))


def test_absent_descuento_stays_none() -> None:
    assert _doc("cfdi_ingreso_4_0.xml").descuento is None


def test_forma_pago_and_metodo_pago_are_promoted() -> None:
    doc = _doc("cfdi_ingreso_4_0.xml")
    assert doc.forma_pago == "03"
    assert doc.metodo_pago == "PUE"


def test_regimen_fiscal_receptor_is_promoted() -> None:
    assert _doc("cfdi_ingreso_4_0.xml").regimen_fiscal_receptor == "601"


def test_absent_regimen_fiscal_receptor_stays_none() -> None:
    assert _built().regimen_fiscal_receptor is None


# --- type P -----------------------------------------------------------------


def test_type_p_header_payment_fields_stay_absent() -> None:
    """A P document carries no header FormaPago/MetodoPago."""
    raw, result = _build("cfdi_pago_4_0.xml")
    assert raw.tipo == "P"
    assert raw.forma_pago is None
    assert raw.metodo_pago is None
    doc = result.document
    assert doc is not None
    assert doc.forma_pago is None
    assert doc.metodo_pago is None


def test_type_p_uuid_is_still_promoted() -> None:
    assert _doc("cfdi_pago_detallado_4_0.xml").source_uuid == Uuid(_TFD_UUID)


# --- relations --------------------------------------------------------------


def test_no_relations_promote_to_empty_tuple() -> None:
    assert _doc("cfdi_ingreso_4_0.xml").cfdi_relacionados == ()


def test_relation_group_is_promoted() -> None:
    relations = _doc("cfdi_relacion_4_0.xml").cfdi_relacionados
    assert len(relations) == 1
    assert isinstance(relations[0], CfdiRelacionados)


def test_tipo_relacion_is_preserved_as_source_code() -> None:
    assert _doc("cfdi_relacion_4_0.xml").cfdi_relacionados[0].tipo_relacion == "01"


def test_multiple_uuids_within_a_group_preserve_order() -> None:
    assert _doc("cfdi_relacion_4_0.xml").cfdi_relacionados[0].uuids == (
        Uuid(_RELACION_UUID_1),
        Uuid(_RELACION_UUID_2),
    )


def test_related_uuids_are_normalized_through_uuid() -> None:
    uuids = _doc("cfdi_relacion_4_0.xml").cfdi_relacionados[0].uuids
    assert all(isinstance(value, Uuid) for value in uuids)


def test_multiple_relation_groups_keep_order_and_are_not_flattened() -> None:
    doc = _built(
        cfdi_relacionados=(
            RawCfdiRelacionados("04", ("33333333-3333-3333-3333-333333333333",)),
            RawCfdiRelacionados("01", (_RELACION_UUID_1, _RELACION_UUID_2)),
        )
    )
    assert [group.tipo_relacion for group in doc.cfdi_relacionados] == ["04", "01"]
    assert len(doc.cfdi_relacionados[1].uuids) == 2


# --- accounting boundary ----------------------------------------------------


def test_no_accounting_base_is_derived() -> None:
    """SubTotal and Descuento stay separate facts; Total is the source header value."""
    doc = _doc("cfdi_egreso_4_0.xml")
    assert doc.subtotal == NormalizedAmount(Decimal("100.00"))
    assert doc.descuento == NormalizedAmount(Decimal("10.00"))
    assert doc.total == NormalizedAmount(Decimal("90.00"))


def test_fiscal_document_exposes_no_derived_base_field() -> None:
    names = {f.name for f in fields(FiscalDocument)}
    assert not {name for name in names if "base" in name or "net" in name}


# --- currency ---------------------------------------------------------------


def test_supported_currency_normalizes_subtotal_and_descuento() -> None:
    doc = _built(subtotal="100.005", descuento="10.005")
    assert doc.subtotal == NormalizedAmount(Decimal("100.01"))
    assert doc.descuento == NormalizedAmount(Decimal("10.01"))


def test_unsupported_currency_nulls_monetary_facts_and_keeps_flag() -> None:
    result = build_fiscal_document(
        _raw(moneda="EUR", subtotal="100.00", descuento="10.00"), _SOURCE_HASH
    )
    assert result.outcome is ParseOutcome.PARTIAL
    doc = result.document
    assert doc is not None
    assert doc.total is None
    assert doc.subtotal is None
    assert doc.descuento is None
    assert doc.review_flags.has_open(ReviewFlagType.UNSUPPORTED_CURRENCY) is True


def test_type_p_xxx_header_is_not_an_unsupported_currency() -> None:
    """M2.4b: XXX on a P comprobante is the CFDI-mandated "no monetary total" code."""
    raw, result = _build("cfdi_pago_4_0.xml")
    assert raw.tipo == "P"
    assert raw.moneda == "XXX"
    assert result.outcome is ParseOutcome.PARSED
    doc = result.document
    assert doc is not None
    assert doc.total is None
    assert doc.subtotal is None
    assert doc.descuento is None
    assert doc.review_flags.has_open(ReviewFlagType.UNSUPPORTED_CURRENCY) is False


def test_non_monetary_facts_survive_unsupported_currency() -> None:
    doc = _built(
        moneda="EUR",
        uuid="123e4567-e89b-12d3-a456-426614174000",
        forma_pago="03",
        metodo_pago="PUE",
        regimen_fiscal_receptor="601",
        cfdi_relacionados=(RawCfdiRelacionados("01", (_RELACION_UUID_1,)),),
    )
    assert doc.source_uuid == Uuid(_TFD_UUID)
    assert doc.forma_pago == "03"
    assert doc.metodo_pago == "PUE"
    assert doc.regimen_fiscal_receptor == "601"
    assert len(doc.cfdi_relacionados) == 1


# --- backward compatibility -------------------------------------------------


def test_existing_construction_without_new_fields_remains_valid() -> None:
    doc = FiscalDocument(
        tipo="I",
        version="4.0",
        moneda="MXN",
        tipo_cambio=None,
        emisor_rfc=Rfc("AAA010101AAA"),
        receptor_rfc=None,
        conceptos=(),
        impuestos=Impuestos(),
        total=None,
        source_hash="abc",
    )
    assert doc.source_uuid is None
    assert doc.subtotal is None
    assert doc.descuento is None
    assert doc.forma_pago is None
    assert doc.metodo_pago is None
    assert doc.regimen_fiscal_receptor is None
    assert doc.cfdi_relacionados == ()


def test_all_new_fields_are_defaulted() -> None:
    by_name = {f.name: f for f in fields(FiscalDocument)}
    assert set(_NEW_FIELDS) <= set(by_name)
    for name in _NEW_FIELDS:
        assert by_name[name].default is not MISSING


def test_existing_field_order_is_unchanged() -> None:
    names = [f.name for f in fields(FiscalDocument)]
    assert names[:10] == [
        "tipo",
        "version",
        "moneda",
        "tipo_cambio",
        "emisor_rfc",
        "receptor_rfc",
        "conceptos",
        "impuestos",
        "total",
        "source_hash",
    ]
    assert names[10:12] == ["status", "review_flags"]
    assert names[12:19] == list(_NEW_FIELDS)
    # M2.4b appends `pagos` last so every earlier position is untouched.
    assert names[19:] == ["pagos"]


def test_no_second_uuid_field_was_introduced() -> None:
    names = {f.name for f in fields(FiscalDocument)}
    assert "source_uuid" in names
    assert "uuid" not in names
