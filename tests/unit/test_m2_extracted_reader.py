"""M2.8: re-reading one extracted artifact reproducibly, by identity (§6a.2).

The reader is the only collaborator that touches the filesystem on the process
path: M2.8 hands it an `ExtractedXml` record and receives the EXACT bytes that
record was derived from — or an `ExtractionError`, never a guess. These tests pin
the four things that make that true: the addressed path (`extracted/<tipo>/
<uuid>.xml`), the exact bytes, the recorded hash still matching, and the refusal
to follow a name component out of the extraction root.
"""

from pathlib import Path

import pytest

from sat_descarga_masiva.domain.errors import ExtractionError
from sat_descarga_masiva.domain.model.source import ExtractedXml, sha256_hex
from sat_descarga_masiva.infrastructure.source.extracted_reader import (
    FilesystemExtractedXmlReader,
)

UUID1 = "123e4567-e89b-12d3-a456-426614174000"
UUID2 = "4e80345d-917f-40bb-a98f-4a73939353c5"
XML = b"<cfdi:Comprobante TipoDeComprobante='I'/>"
OTHER_XML = b"<cfdi:Comprobante TipoDeComprobante='E'/>"


def _store(root: Path, tipo: str, name: str, data: bytes) -> ExtractedXml:
    """Write an extracted artifact where the extractor puts it, and record it."""
    path = root / "extracted" / tipo / f"{name}.xml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return ExtractedXml(uuid=name, tipo=tipo, sha256=sha256_hex(data))


def test_reads_the_exact_bytes_recorded_for_the_artifact(tmp_path: Path) -> None:
    artifact = _store(tmp_path, "ingreso", UUID1, XML)
    assert FilesystemExtractedXmlReader(tmp_path).read(artifact) == XML


def test_reads_from_the_tipo_directory_the_artifact_names(tmp_path: Path) -> None:
    """`tipo` is part of the address: the same UUID in another dir is another file."""
    _store(tmp_path, "ingreso", UUID1, XML)
    egreso = _store(tmp_path, "egreso", UUID1, OTHER_XML)
    assert FilesystemExtractedXmlReader(tmp_path).read(egreso) == OTHER_XML


def test_missing_artifact_raises_extraction_error(tmp_path: Path) -> None:
    missing = ExtractedXml(uuid=UUID2, tipo="ingreso", sha256=sha256_hex(XML))
    with pytest.raises(ExtractionError, match="missing"):
        FilesystemExtractedXmlReader(tmp_path).read(missing)


def test_hash_mismatch_raises_extraction_error_and_returns_no_bytes(tmp_path: Path) -> None:
    """Store integrity (§6a.2): bytes that changed underneath us never reach a parse."""
    artifact = _store(tmp_path, "ingreso", UUID1, XML)
    tampered = ExtractedXml(uuid=UUID1, tipo="ingreso", sha256=sha256_hex(OTHER_XML))
    with pytest.raises(ExtractionError, match="hash mismatch"):
        FilesystemExtractedXmlReader(tmp_path).read(tampered)
    assert artifact.sha256 == sha256_hex(XML)  # the record itself is untouched


@pytest.mark.parametrize(
    "name", ["../../etc/passwd", "/etc/passwd", "a/b", "..\\..\\x", "C:evil", "~"]
)
def test_rejects_an_unsafe_uuid_component(tmp_path: Path, name: str) -> None:
    artifact = ExtractedXml(uuid=name, tipo="ingreso", sha256=sha256_hex(XML))
    with pytest.raises(ExtractionError):
        FilesystemExtractedXmlReader(tmp_path).read(artifact)


@pytest.mark.parametrize("tipo", ["../ingreso", "", "/ingreso", "..", "in/geso"])
def test_rejects_an_unsafe_tipo_component(tmp_path: Path, tipo: str) -> None:
    artifact = ExtractedXml(uuid=UUID1, tipo=tipo, sha256=sha256_hex(XML))
    with pytest.raises(ExtractionError):
        FilesystemExtractedXmlReader(tmp_path).read(artifact)


def test_traversal_is_refused_even_when_the_target_exists_with_a_matching_hash(
    tmp_path: Path,
) -> None:
    """The strongest form: the escape is refused by the guard, not by a hash check."""
    secret = tmp_path / "secret" / f"{UUID1}.xml"
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_bytes(XML)
    escaping = ExtractedXml(uuid=f"../secret/{UUID1}", tipo="ingreso", sha256=sha256_hex(XML))
    with pytest.raises(ExtractionError, match="traversal"):
        FilesystemExtractedXmlReader(tmp_path).read(escaping)


def test_guard_admits_a_plain_artifact_name(tmp_path: Path) -> None:
    """Positive control for the guard: an ordinary artifact name is not touched by it."""
    artifact = _store(tmp_path, "ingreso", UUID1, XML)
    assert FilesystemExtractedXmlReader(tmp_path).read(artifact) == XML
