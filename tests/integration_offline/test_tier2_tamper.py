"""Tier-2: store integrity (§6a.2) — evidence that changed underneath us is detected.

Detection, not tamper-proofing: rewriting *both* an artifact and the sidecar that records
its digest is a different threat model (the immutability of the store itself, §4(a)) and is
out of scope here. What is asserted is that the recorded digest is re-checked against bytes
re-read from disk, so corruption or an edit cannot slide silently into a downstream parse.
"""

from __future__ import annotations

import pytest

from conftest import StoredPackage
from sat_descarga_masiva.domain.errors import SourceIntegrityError
from sat_descarga_masiva.domain.model.source import verify_source_integrity


def test_tampered_extracted_xml_is_detected(stored_package: StoredPackage) -> None:
    path = stored_package.extracted_path("cfdi_tfd_4_0.xml")
    entry = stored_package.entry("cfdi_tfd_4_0.xml")
    path.write_bytes(path.read_bytes().replace(b"AAA010101AAA", b"XXX010101XXX"))

    with pytest.raises(SourceIntegrityError):
        verify_source_integrity(path.read_bytes(), entry.sha256)


def test_tampered_stored_zip_is_detected(stored_package: StoredPackage) -> None:
    path = stored_package.stored_zip_path()
    path.write_bytes(path.read_bytes() + b"trailing-junk")

    with pytest.raises(SourceIntegrityError):
        verify_source_integrity(path.read_bytes(), stored_package.manifest.sha256)


def test_sidecar_digest_that_the_artifact_does_not_match_is_detected(
    stored_package: StoredPackage,
) -> None:
    """The sidecar is the claim, the artifact is the evidence: a wrong claim fails."""
    stored_package.sidecar_path().write_text(
        stored_package.sidecar_path().read_text().replace(stored_package.manifest.sha256, "0" * 64)
    )

    with pytest.raises(SourceIntegrityError):
        verify_source_integrity(
            stored_package.stored_zip_path().read_bytes(), stored_package.sidecar().sha256
        )


def test_tampering_with_one_artifact_does_not_hide_another(stored_package: StoredPackage) -> None:
    """One artifact's damage never masks or spreads to another's integrity (§6, per-artifact)."""
    tampered = stored_package.extracted_path("cfdi_tfd_4_0.xml")
    tampered.write_bytes(tampered.read_bytes() + b"\n<!-- edited -->")

    untouched = stored_package.extracted_path("cfdi_ingreso_3_3.xml")
    entry = stored_package.entry("cfdi_ingreso_3_3.xml")
    verify_source_integrity(untouched.read_bytes(), entry.sha256)  # still passes
    assert untouched.read_bytes() == stored_package.member("cfdi_ingreso_3_3.xml").data
