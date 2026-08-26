"""M1: SafeZipExtractor — §6a #4 controls: bounds, traversal, only .xml, per-XML hash."""

import io
import zipfile
from uuid import uuid4

import pytest

from sat_descarga_masiva.application.policies.extraction import ExtractionPolicy
from sat_descarga_masiva.domain.errors import ExtractionError
from sat_descarga_masiva.domain.model.source import sha256_hex
from sat_descarga_masiva.domain.model.value_objects import PackageId
from sat_descarga_masiva.infrastructure.sat.extract.extractor import SafeZipExtractor

UUID1 = str(uuid4())
UUID2 = str(uuid4())

INGRESO = b'<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4" TipoDeComprobante="I"/>'
EGRESO = b'<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4" TipoDeComprobante="E"/>'


POLICY = ExtractionPolicy(max_total_bytes=1024 * 1024, max_entries=100)


def _zip(members: list[tuple[str, bytes]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members:
            zf.writestr(name, data)
    return buf.getvalue()


def _pid() -> PackageId:
    return PackageId(f"{UUID1}_01")


def test_extracts_xml_with_type_and_hash(tmp_path) -> None:
    content = _zip([(f"{UUID1}.xml", INGRESO), (f"{UUID2}.xml", EGRESO)])
    result = SafeZipExtractor(tmp_path, POLICY).extract(_pid(), content)

    assert {r.uuid for r in result} == {UUID1, UUID2}
    by_uuid = {r.uuid: r for r in result}
    assert by_uuid[UUID1].tipo == "ingreso"
    assert by_uuid[UUID1].sha256 == sha256_hex(INGRESO)
    assert by_uuid[UUID2].tipo == "egreso"

    assert (tmp_path / "extracted" / "ingreso" / f"{UUID1}.xml").read_bytes() == INGRESO
    assert (tmp_path / "extracted" / "egreso" / f"{UUID2}.xml").read_bytes() == EGRESO


def test_skips_non_xml_members(tmp_path) -> None:
    content = _zip([(f"{UUID1}.xml", INGRESO), ("readme.txt", b"hi"), ("logo.png", b"\x89PNG")])
    result = SafeZipExtractor(tmp_path, POLICY).extract(_pid(), content)
    assert [r.uuid for r in result] == [UUID1]


def test_rejects_too_many_entries(tmp_path) -> None:
    policy = ExtractionPolicy(max_total_bytes=1024 * 1024, max_entries=2)
    with pytest.raises(ExtractionError):
        SafeZipExtractor(tmp_path, policy).extract(_pid(), _zip([(f"{UUID1}.xml", INGRESO)] * 3))


def test_rejects_zip_bomb_total_size(tmp_path) -> None:
    chunk = b"x" * 250
    members = [(f"{i}.xml", chunk) for i in range(5)]  # each < 1024, total > 1024
    policy = ExtractionPolicy(max_total_bytes=1024, max_entries=100)
    with pytest.raises(ExtractionError):
        SafeZipExtractor(tmp_path, policy).extract(_pid(), _zip(members))


def test_rejects_path_traversal(tmp_path) -> None:
    content = _zip([(f"../{UUID1}.xml", INGRESO)])
    with pytest.raises(ExtractionError):
        SafeZipExtractor(tmp_path, POLICY).extract(_pid(), content)


def test_rejects_absolute_path(tmp_path) -> None:
    content = _zip([(f"/etc/{UUID1}.xml", INGRESO)])
    with pytest.raises(ExtractionError):
        SafeZipExtractor(tmp_path, POLICY).extract(_pid(), content)


def test_rejects_conflicting_existing_xml(tmp_path) -> None:
    dest = tmp_path / "extracted" / "ingreso" / f"{UUID1}.xml"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(EGRESO)  # different content at the same target uuid
    content = _zip([(f"{UUID1}.xml", INGRESO)])
    with pytest.raises(ExtractionError):
        SafeZipExtractor(tmp_path, POLICY).extract(_pid(), content)


def test_extraction_is_idempotent(tmp_path) -> None:
    content = _zip([(f"{UUID1}.xml", INGRESO)])
    extractor = SafeZipExtractor(tmp_path, POLICY)
    assert extractor.extract(_pid(), content) == extractor.extract(_pid(), content)
    assert len(list((tmp_path / "extracted").rglob("*.xml"))) == 1
