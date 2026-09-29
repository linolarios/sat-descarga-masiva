"""M2.6: pure per-document composition into ProcessedDocument (§6, §11 M2).

`project_document` composes the existing producers (parse, perspective, signature) into one
immutable result and decides *whether the document may be projected at all* from its identity
facts. It performs no I/O, holds no repository and writes no fiscal event: identity is the TFD
UUID (§6), and a document whose identity cannot be trusted is quarantined rather than keyed by
some other identifier.
"""

import ast
from dataclasses import FrozenInstanceError
from decimal import Decimal
from pathlib import Path

import pytest

from sat_descarga_masiva.domain.model.fiscal_document import ParseOutcome
from sat_descarga_masiva.domain.model.perspective import Perspective
from sat_descarga_masiva.domain.model.raw_cfd import (
    RawCfd,
    RawConcepto,
    RawImpuestos,
    RawTraslado,
)
from sat_descarga_masiva.domain.model.review import ReviewFlags, ReviewFlagType
from sat_descarga_masiva.domain.model.signature import SignatureOutcome, SignatureVerdict
from sat_descarga_masiva.domain.model.value_objects import Rfc, Uuid
from sat_descarga_masiva.domain.policy.money import NormalizedAmount
from sat_descarga_masiva.fiscal import projection
from sat_descarga_masiva.fiscal.projection import ProcessedDocument, project_document

CONTRIBUTOR = Rfc("AAA010101AAA")
OTHER_RFC = Rfc("CCC010101CCC")
TFD_UUID = "11111111-1111-1111-1111-111111111111"
OTHER_UUID = "22222222-2222-2222-2222-222222222222"
SOURCE_HASH = "sha256:abc"

# Dependency boundary of the module under test (§16): money/crypto/XML/network/file libraries
# are unreachable from fiscal/projection.py (application/infrastructure are asserted separately).
_FORBIDDEN_ROOTS = (
    "cryptography",
    "hashlib",
    "io",
    "lxml",
    "openpyxl",
    "os",
    "pathlib",
    "requests",
    "satcfdi",
    "shutil",
    "socket",
    "sqlite3",
    "subprocess",
    "tempfile",
)
# Layers above the pure rings: fiscal/ may never import them (§16).
_FORBIDDEN_PACKAGES = (
    "sat_descarga_masiva.application",
    "sat_descarga_masiva.infrastructure",
)


def _raw(**overrides: object) -> RawCfd:
    base = dict(
        tipo="I",
        version="4.0",
        moneda="MXN",
        tipo_cambio=None,
        emisor_rfc=CONTRIBUTOR.value,  # the contributor is the emisor -> EMITIDO
        receptor_rfc="BBB010101BBB",
        conceptos=(RawConcepto("01010101", "1", "100.00", "100.00", None),),
        impuestos=RawImpuestos(
            traslados=(RawTraslado("002", "Tasa", "0.16", "16.00"),),
            retenciones=(),
            total_traslados="16.00",
            total_retenciones=None,
        ),
        total="116.00",
        uuid=TFD_UUID,
    )
    base.update(overrides)
    return RawCfd(**base)  # type: ignore[arg-type]


def _project(
    raw: RawCfd | None = None,
    verdict: SignatureVerdict | None = None,
    *,
    contributor: Rfc = CONTRIBUTOR,
    artifact_uuid: str = TFD_UUID,
) -> ProcessedDocument:
    return project_document(
        raw if raw is not None else _raw(),
        verdict if verdict is not None else SignatureVerdict(SignatureOutcome.VALID),
        source_hash=SOURCE_HASH,
        contributor_rfc=contributor,
        artifact_uuid=artifact_uuid,
    )


def test_parsed_document_is_projected_with_its_facts() -> None:
    result = _project()
    assert result.outcome is ParseOutcome.PARSED
    assert result.is_quarantined is False
    assert result.quarantine_reason is None
    assert result.perspective is Perspective.EMITIDO
    assert result.review_flags == ReviewFlags()
    document = result.document
    assert document is not None
    assert document.source_hash == SOURCE_HASH
    assert document.tipo == "I"


def test_projected_document_exposes_the_tfd_uuid() -> None:
    result = _project(_raw(uuid=TFD_UUID.lower()))
    document = result.document
    assert document is not None
    # The TFD UUID is the identity, canonicalized through the domain value object — never the
    # artifact name and never the source hash.
    assert result.uuid == Uuid(TFD_UUID)
    assert result.uuid == document.source_uuid
    assert result.uuid.value == TFD_UUID


def test_partial_remains_projectable() -> None:
    result = _project(_raw(moneda="EUR", total="10.00"))
    assert result.outcome is ParseOutcome.PARTIAL
    assert result.is_quarantined is False
    assert result.quarantine_reason is None


def test_signature_and_perspective_flags_coexist() -> None:
    result = _project(
        _raw(),
        SignatureVerdict(SignatureOutcome.INVALID, "checksum"),
        contributor=OTHER_RFC,
    )
    assert result.perspective is Perspective.UNDETERMINED
    assert result.review_flags.count(ReviewFlagType.CFDI_SIGNATURE) == 1
    assert result.review_flags.count(ReviewFlagType.PERSPECTIVE_MISMATCH) == 1
    assert result.is_quarantined is False


def test_parser_and_signature_flags_coexist() -> None:
    result = _project(_raw(moneda="EUR"), SignatureVerdict(SignatureOutcome.ABSENT))
    assert result.review_flags.count(ReviewFlagType.UNSUPPORTED_CURRENCY) == 1
    assert result.review_flags.count(ReviewFlagType.CFDI_SIGNATURE) == 1
    assert result.is_quarantined is False


def test_undetermined_perspective_projects_with_its_flag() -> None:
    result = _project(_raw(emisor_rfc="XAXX010101000"), contributor=Rfc("XAXX010101000"))
    assert result.perspective is Perspective.UNDETERMINED
    assert result.review_flags.count(ReviewFlagType.PERSPECTIVE_UNDETERMINED) == 1
    assert result.is_quarantined is False
    assert result.uuid == Uuid(TFD_UUID)


def test_missing_tfd_uuid_quarantines() -> None:
    result = _project(_raw(uuid=None))
    assert result.quarantine_reason == "no TFD UUID: no trustworthy document identity"
    assert result.is_quarantined is True
    assert result.uuid is None  # a quarantined document is not a projected document
    assert result.document is not None  # the parsed facts are kept, just not projected


def test_tfd_uuid_differing_from_the_artifact_uuid_quarantines() -> None:
    result = _project(_raw(uuid=OTHER_UUID.lower()), artifact_uuid=TFD_UUID)
    assert result.quarantine_reason == (
        f"TFD UUID {OTHER_UUID} differs from the artifact UUID {TFD_UUID}"
    )
    assert result.is_quarantined is True
    assert result.uuid is None


def test_invalid_artifact_uuid_quarantines() -> None:
    result = _project(artifact_uuid="not-a-uuid")
    assert result.quarantine_reason == "artifact name not-a-uuid is not a CFDI UUID"
    assert result.is_quarantined is True
    assert result.uuid is None


def test_unsupported_version_quarantines_without_a_new_flag_type() -> None:
    result = _project(_raw(version="3.3", uuid=None), artifact_uuid="not-a-uuid")
    assert result.outcome is ParseOutcome.FAILED
    assert result.document is None
    assert result.quarantine_reason == "no fiscal projection: CFDI version 3.3 unsupported"
    assert result.is_quarantined is True
    assert result.uuid is None
    # An unsupported version opens no flag: quarantine is not review vocabulary (§8 gap).
    assert result.review_flags == ReviewFlags()


def test_same_inputs_project_the_same_result_and_the_result_is_frozen() -> None:
    raw = _raw()
    verdict = SignatureVerdict(SignatureOutcome.VALID)
    first = project_document(
        raw,
        verdict,
        source_hash=SOURCE_HASH,
        contributor_rfc=CONTRIBUTOR,
        artifact_uuid=TFD_UUID,
    )
    second = project_document(
        raw,
        verdict,
        source_hash=SOURCE_HASH,
        contributor_rfc=CONTRIBUTOR,
        artifact_uuid=TFD_UUID,
    )
    assert first == second
    assert raw.uuid == TFD_UUID  # inputs are read, never rewritten
    with pytest.raises(FrozenInstanceError):
        first.quarantine_reason = "mutated"  # type: ignore[misc]


def test_projected_money_stays_decimal_normalized() -> None:
    result = _project()
    document = result.document
    assert document is not None
    assert isinstance(document.total, NormalizedAmount)
    assert document.total == NormalizedAmount(Decimal("116.00"))
    assert isinstance(document.total.amount, Decimal)
    assert not isinstance(document.total.amount, float)


def test_projection_module_depends_only_on_domain_and_stdlib() -> None:
    source = Path(projection.__file__).read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)

    project_imports = {name for name in imported if name.split(".")[0] == "sat_descarga_masiva"}
    assert project_imports  # the module really composes domain values and sibling producers
    # Pure rings only: domain values, plus the fiscal producers it composes (never the layers
    # above: application/infrastructure are asserted absent below).
    assert all(
        name.startswith(("sat_descarga_masiva.domain.", "sat_descarga_masiva.fiscal."))
        for name in project_imports
    )
    forbidden = sorted(
        name
        for name in imported
        if name.split(".")[0] in _FORBIDDEN_ROOTS or name.startswith(_FORBIDDEN_PACKAGES)
    )
    assert forbidden == []
