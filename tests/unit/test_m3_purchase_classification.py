"""M3 step A: the purchase-classification seam — ``ClaveProdServ → category → role`` (§8a:196/207).

A *received* income comprobante (rules 4.3/4.4) has no role in the document: §8's row names the
base leg ``Gasto/Inv``, and which one it is comes from the client's versioned chart
(``ClaveProdServ → AccountingCategory``) plus the engine's fixed ``category → role`` vocabulary.
These tests pin the properties the receiving rules rely on:

- **fixed vocabulary**: the categories are the engine's, and only ``activo_fijo`` posts nothing —
  §8a:196's capital goods go to a fixed-asset review, never a guessed posting;
- **total or refused**: a document's concepts reduce to exactly one role, or to a typed
  `ClassificationRefusal` (missing ClaveProdServ, an unclassified product, capital goods, or
  concepts that disagree). Never first-wins, never summed;
- **versioned with the mapping**: the ``ClaveProdServ → category`` table rides the same YAML and
  the same ``mapping_version``, so there is no second version scheme (§8a:207).
"""

from decimal import Decimal
from pathlib import Path

import pytest

from sat_descarga_masiva.application.ports.accounting import AccountMapping
from sat_descarga_masiva.contabilidad.classification import (
    AccountingCategory,
    ClassificationRefusal,
    ClassificationRefusalKind,
    classify_document,
    role_for,
)
from sat_descarga_masiva.contabilidad.roles import AccountRole
from sat_descarga_masiva.contabilidad.rules.contract import PostingContext
from sat_descarga_masiva.domain.errors import InvalidMapping
from sat_descarga_masiva.domain.model.fiscal_document import Concepto, FiscalDocument, Impuestos
from sat_descarga_masiva.domain.model.perspective import Perspective
from sat_descarga_masiva.domain.model.value_objects import Rfc
from sat_descarga_masiva.domain.policy.money import NormalizedAmount
from sat_descarga_masiva.infrastructure.mapping.yaml_mapping import YamlMappingProvider

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "mappings"
_CLIENT = Rfc("AAA010101AAA")
_CONTRIBUTOR = Rfc("AAA010101AAA")
_EMISOR = Rfc("BBB010101BBB")
_VERSION = "2026.01"

#: Fixture ClaveProdServ codes, keyed to the categories the fixture YAML assigns them (§8a:207).
_GASTO = "84111506"
_INVENTARIO = "53131600"
_ACTIVO_FIJO = "43211500"

_TABLE = {
    _GASTO: AccountingCategory.GASTO,
    _INVENTARIO: AccountingCategory.INVENTARIO,
    _ACTIVO_FIJO: AccountingCategory.ACTIVO_FIJO,
}


def _money(text: str) -> NormalizedAmount:
    return NormalizedAmount(Decimal(text))


def _document(*claves: str) -> FiscalDocument:
    """A received income comprobante whose concepts carry the given ClaveProdServ codes."""
    return FiscalDocument(
        tipo="I",
        version="4.0",
        moneda="MXN",
        tipo_cambio=None,
        emisor_rfc=_EMISOR,
        receptor_rfc=_CONTRIBUTOR,
        conceptos=tuple(
            Concepto(clave, Decimal("1"), _money("100.00"), _money("100.00")) for clave in claves
        ),
        impuestos=Impuestos(),
        total=_money("100.00"),
        source_hash="a" * 64,
    )


def _refusal(result: object) -> ClassificationRefusal:
    assert isinstance(result, ClassificationRefusal), result
    return result


def _provider(root: Path | str = _FIXTURES) -> YamlMappingProvider:
    return YamlMappingProvider(root)


# --- the fixed vocabulary (§8a:196) -----------------------------------------------------


def test_each_category_names_the_role_it_books_to() -> None:
    """The engine's ``category → role`` edge: Gasto and Inventario book; capital goods do not."""
    assert role_for(AccountingCategory.GASTO) is AccountRole.GASTO
    assert role_for(AccountingCategory.INVENTARIO) is AccountRole.INVENTARIO
    assert role_for(AccountingCategory.ACTIVO_FIJO) is None


def test_the_vocabulary_is_the_three_purchase_categories() -> None:
    """M3's purchase semantics, verbatim: a role the engine cannot name cannot be posted to."""
    assert {category.value for category in AccountingCategory} == {
        "gasto",
        "inventario",
        "activo_fijo",
    }


def test_a_refusal_always_says_why() -> None:
    """§8:167: a refusal without a reason is indistinguishable from nobody having looked."""
    with pytest.raises(ValueError, match="must say why"):
        ClassificationRefusal(ClassificationRefusalKind.CAPITAL_GOODS, "   ")


# --- classifying a document's concepts (§8a:207) ----------------------------------------


def test_concepts_that_agree_classify_to_their_role() -> None:
    """One role for the whole document is the only thing a single base leg can express."""
    assert classify_document(_document(_GASTO, _GASTO), _TABLE) is AccountRole.GASTO
    assert classify_document(_document(_INVENTARIO), _TABLE) is AccountRole.INVENTARIO


def test_a_concept_without_a_clave_prod_serv_is_refused() -> None:
    """No product code means no classification: the rule reviews instead of defaulting (§8a:196)."""
    refusal = _refusal(classify_document(_document(""), _TABLE))
    assert refusal.kind is ClassificationRefusalKind.MISSING_CLAVE_PROD_SERV


def test_a_document_without_concepts_is_refused() -> None:
    """A purchase with nothing to classify is not a classification of nothing."""
    refusal = _refusal(classify_document(_document(), _TABLE))
    assert refusal.kind is ClassificationRefusalKind.MISSING_CLAVE_PROD_SERV


def test_an_unclassified_product_is_refused() -> None:
    """A ClaveProdServ the client's mapping does not name is a missing mapping, never Gasto."""
    refusal = _refusal(classify_document(_document("99999999"), _TABLE))
    assert refusal.kind is ClassificationRefusalKind.UNCLASSIFIED_PRODUCT
    assert "99999999" in refusal.detail


def test_a_capital_good_is_refused_for_fixed_asset_review() -> None:
    """§8a:196: capital goods are reviewed — the engine never invents a fixed-asset posting."""
    refusal = _refusal(classify_document(_document(_ACTIVO_FIJO), _TABLE))
    assert refusal.kind is ClassificationRefusalKind.CAPITAL_GOODS


def test_concepts_that_disagree_are_refused_never_first_wins_never_summed() -> None:
    """A mixed document is reviewed whole: taking one role or summing them misstates it (§8:173)."""
    refusal = _refusal(classify_document(_document(_GASTO, _INVENTARIO), _TABLE))
    assert refusal.kind is ClassificationRefusalKind.MIXED_CATEGORIES
    assert "gasto" in refusal.detail and "inventario" in refusal.detail


# --- the YAML adapter: the table is the client's, and it is versioned (§8a:207) ----------


def test_the_fixture_classifies_its_products() -> None:
    """The client's YAML is the versioned artifact §8a:207 asks for, classification included."""
    mapping = _provider().mapping_for(_CLIENT)
    assert mapping.mapping_version == _VERSION
    assert mapping.category_for(_GASTO) is AccountingCategory.GASTO
    assert mapping.category_for(_INVENTARIO) is AccountingCategory.INVENTARIO
    assert mapping.category_for(_ACTIVO_FIJO) is AccountingCategory.ACTIVO_FIJO
    assert mapping.category_for("00000000") is None


def test_a_client_can_classify_its_own_document_end_to_end() -> None:
    """The whole seam: the client's mapping reduces a received purchase to a role — or a refusal."""
    mapping = _provider().mapping_for(_CLIENT)
    assert mapping.classify(_document(_GASTO, _GASTO)) is AccountRole.GASTO
    assert mapping.classify(_document(_ACTIVO_FIJO)) == ClassificationRefusal(
        ClassificationRefusalKind.CAPITAL_GOODS,
        "ClaveProdServ '43211500' is classified activo_fijo: §8a:196 routes capital goods to a"
        " fixed-asset review rather than a guessed posting",
    )


def test_a_mapping_without_a_classification_block_classifies_nothing(tmp_path: Path) -> None:
    """The block is optional; absent means unclassified, which the receiving rules review."""
    (tmp_path / f"{_CLIENT.value}.yaml").write_text(
        "mapping_version: '2026.01'\naccounts:\n  gasto: '601-001'\n", encoding="utf-8"
    )
    mapping = _provider(tmp_path).mapping_for(_CLIENT)
    assert mapping.classification == {}
    assert mapping.category_for(_GASTO) is None
    assert isinstance(mapping.classify(_document(_GASTO)), ClassificationRefusal)


def test_a_mistyped_category_is_refused(tmp_path: Path) -> None:
    """A category outside the vocabulary must not silently leave a product unclassified."""
    (tmp_path / f"{_CLIENT.value}.yaml").write_text(
        "mapping_version: '2026.01'\naccounts: {}\nclassification:\n  '84111506': gastto\n",
        encoding="utf-8",
    )
    with pytest.raises(InvalidMapping, match="not an AccountingCategory"):
        _provider(tmp_path).mapping_for(_CLIENT)


def test_a_classification_block_that_is_not_a_mapping_is_refused(tmp_path: Path) -> None:
    """A broken config is a human's to fix, never a partial classification."""
    (tmp_path / f"{_CLIENT.value}.yaml").write_text(
        "mapping_version: '2026.01'\naccounts: {}\nclassification: [gasto]\n", encoding="utf-8"
    )
    with pytest.raises(InvalidMapping, match="'classification' must map"):
        _provider(tmp_path).mapping_for(_CLIENT)


def test_a_classification_entry_must_be_a_code_and_a_name(tmp_path: Path) -> None:
    """An entry that is not a code → name pair is a configuration fault, not a silent skip."""
    (tmp_path / f"{_CLIENT.value}.yaml").write_text(
        "mapping_version: '2026.01'\naccounts: {}\nclassification:\n  '84111506': [gasto]\n",
        encoding="utf-8",
    )
    with pytest.raises(InvalidMapping, match="ClaveProdServ code and a category name"):
        _provider(tmp_path).mapping_for(_CLIENT)


# --- the carrier the receiving rules read (§8:173) --------------------------------------


def test_a_mapping_without_a_classification_table_still_exists() -> None:
    """Backward compatible: the emitting rules' two-argument mapping keeps working."""
    mapping = AccountMapping(_VERSION, {AccountRole.INGRESOS: "401-001"})
    assert mapping.classification == {}


def test_a_posting_context_carries_the_classification_or_none() -> None:
    """A receiving rule reads the resolved role off the context; an emitting rule needs none."""
    document = _document(_GASTO)
    default = PostingContext(_CONTRIBUTOR, document, Perspective.RECIBIDO)
    assert default.classification is None

    classified = PostingContext(
        _CONTRIBUTOR, document, Perspective.RECIBIDO, classification=AccountRole.GASTO
    )
    assert classified.classification is AccountRole.GASTO
