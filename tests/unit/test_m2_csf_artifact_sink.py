"""M2.7: FilesystemCsfArtifactSink — the CSF retained immutably (§7a).

The CSF is fiscal identity and the root of the §7a audit chain
(entry -> ContributorProfile -> csf_hash -> original CSF), so it is stored as an
immutable, hash-named source artifact: never overwritten, never modified.
"""

from datetime import UTC, datetime
from pathlib import Path

from sat_descarga_masiva.application.ports.csf import CsfArtifactSink
from sat_descarga_masiva.domain.model.source import sha256_hex
from sat_descarga_masiva.infrastructure.source.csf_sink import FilesystemCsfArtifactSink

NOW = datetime(2026, 1, 1, tzinfo=UTC)
CSF = b"%PDF-1.4 constancia de situacion fiscal"
OTHER_CSF = b"%PDF-1.4 another constancia"


def _sink(root: Path) -> CsfArtifactSink:
    return FilesystemCsfArtifactSink(root, clock=lambda: NOW)


def test_store_writes_the_csf_under_its_content_hash(tmp_path: Path) -> None:
    artifact = _sink(tmp_path).store(CSF)
    assert artifact.sha256 == sha256_hex(CSF)
    assert artifact.recorded_at == NOW
    assert Path(artifact.stored_path).read_bytes() == CSF
    assert artifact.stored_path.endswith(f"{sha256_hex(CSF)}.pdf")


def test_store_keeps_distinct_csf_distinct(tmp_path: Path) -> None:
    sink = _sink(tmp_path)
    first = sink.store(CSF)
    second = sink.store(OTHER_CSF)
    assert first.stored_path != second.stored_path
    assert Path(second.stored_path).read_bytes() == OTHER_CSF


def test_storing_the_same_csf_never_overwrites_the_retained_original(tmp_path: Path) -> None:
    sink = _sink(tmp_path)
    first = sink.store(CSF)
    path = Path(first.stored_path)
    path.write_bytes(b"tampered")  # stand-in for any existing artifact content
    again = sink.store(CSF)
    assert again.sha256 == first.sha256
    assert path.read_bytes() == b"tampered"  # immutable: never written again
