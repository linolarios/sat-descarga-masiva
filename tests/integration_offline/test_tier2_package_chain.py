"""Tier-2: the offline full chain — package ZIP -> store -> extract -> parse -> project.

Tier-1 (`tests/unit`) proves each port in isolation. This tier crosses the seams on the
evidence *on disk*: the source store wrote the package, the extractor read what the store
wrote, the parser read the file the extractor wrote, and the projection consumed that
parse. Nothing here is marked `integration`, so `make check` runs the whole chain.

No SAT, no network, no clock: package bytes, acquisition instant and zip timestamps are
all fixed, which is what makes every assertion below reproducible byte for byte.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from conftest import StoredPackage
from fixtures.package_builder import (
    FIXED_DATE_TIME,
    FIXTURES,
    artifact_name,
    build_package_bytes,
    package_members,
)
from sat_descarga_masiva.domain.model.review import ReviewFlagType
from sat_descarga_masiva.domain.model.signature import SignatureOutcome, SignatureVerdict
from sat_descarga_masiva.domain.model.source import sha256_hex
from sat_descarga_masiva.domain.model.value_objects import Uuid
from sat_descarga_masiva.fiscal.projection import ProcessedDocument, project_document
from sat_descarga_masiva.infrastructure.fiscal.satcfdi_fiscal_parser import SatcfdiFiscalParser

_TFD_UUID = "123e4567-e89b-12d3-a456-426614174000"
_RELACION_UUID = "11111111-1111-1111-1111-111111111111"

_PROJECTABLE = {"cfdi_tfd_4_0.xml"}
_REFUSED = {"cfdi_egreso_4_0.xml", "cfdi_ingreso_3_3.xml"}

# The verdict is an INPUT to projection (M2.5 owns producing it): these synthetic fixtures
# carry an empty sello, so ABSENT is the honest verdict for them, and projection must carry
# that flag forward instead of dropping it.
_VERDICT = SignatureVerdict(SignatureOutcome.ABSENT, "synthetic fixture carries no sello")

_parser = SatcfdiFiscalParser()


# --- the package itself ---------------------------------------------------


def test_package_bytes_are_reproducible() -> None:
    assert build_package_bytes() == build_package_bytes()


def test_zip_timestamps_are_fixed_and_members_are_stored() -> None:
    with zipfile.ZipFile(io.BytesIO(build_package_bytes())) as archive:
        for info in archive.infolist():
            assert info.date_time == FIXED_DATE_TIME  # the epoch, never the build clock
            assert info.compress_type == zipfile.ZIP_STORED


def test_committed_package_fixture_matches_the_builder(
    committed_package_bytes: bytes, rebuilt_package_bytes: bytes
) -> None:
    """The committed `.zip` is the builder's output — never a stale hand-edited blob."""
    assert committed_package_bytes == rebuilt_package_bytes


def test_members_are_named_after_the_uuid_the_cfdi_carries() -> None:
    members = {member.fixture: member for member in package_members()}
    assert members["cfdi_tfd_4_0.xml"].name == f"{_TFD_UUID}.xml"


def test_the_member_name_comes_from_the_tfd_not_from_a_related_document() -> None:
    """`cfdi_relacion_4_0.xml` also carries `CfdiRelacionado` UUIDs for *other* documents."""
    xml = (FIXTURES / "cfdi_relacion_4_0.xml").read_bytes()
    assert _RELACION_UUID.encode() in xml  # the trap: another document's UUID is present
    assert artifact_name("cfdi_relacion_4_0.xml", xml) == f"{_TFD_UUID}.xml"


def test_two_fixtures_that_would_share_a_member_name_are_rejected() -> None:
    """Why the default set holds one TFD-bearing fixture: a name is an identity, not a label."""
    with pytest.raises(ValueError, match="share one member name"):
        package_members(("cfdi_tfd_4_0.xml", "cfdi_relacion_4_0.xml"))


def test_members_without_a_tfd_get_a_derived_name_not_an_identity() -> None:
    """A well-formed name is only a name: the XML's missing identity stays missing."""
    members = {member.fixture: member for member in package_members()}
    for fixture in sorted(_REFUSED):
        member = members[fixture]
        Uuid(member.artifact_uuid)  # well-formed, the shape §6 packages use
        assert _parser.parse(member.data, sha256_hex(member.data)).uuid is None


# --- the chain on stored evidence ----------------------------------------


def test_stored_zip_is_still_the_evidence_that_was_downloaded(
    stored_package: StoredPackage,
) -> None:
    stored = stored_package.stored_zip_path().read_bytes()
    assert sha256_hex(stored) == stored_package.manifest.sha256  # re-read, re-hash


def test_manifest_sidecar_reads_back_as_the_manifest_that_was_written(
    stored_package: StoredPackage,
) -> None:
    assert stored_package.sidecar() == stored_package.manifest


def test_extraction_yields_one_artifact_per_xml_member(stored_package: StoredPackage) -> None:
    assert set(stored_package.extracted) == {m.artifact_uuid for m in stored_package.members}


def test_extraction_preserves_the_member_bytes(stored_package: StoredPackage) -> None:
    """Derivation, not rewriting: the extracted file *is* the member's bytes."""
    for member in stored_package.members:
        assert stored_package.extracted_path(member.fixture).read_bytes() == member.data


def test_every_extracted_xml_still_matches_its_recorded_hash(
    stored_package: StoredPackage,
) -> None:
    for member in stored_package.members:
        entry = stored_package.entry(member.fixture)
        on_disk = stored_package.extracted_path(member.fixture).read_bytes()
        assert sha256_hex(on_disk) == entry.sha256


# --- the projection seam --------------------------------------------------


def _project(stored_package: StoredPackage, fixture: str) -> ProcessedDocument:
    """Parse the extracted file and project it — the seam the M2.8 boundary will span."""
    entry = stored_package.entry(fixture)
    raw = _parser.parse(stored_package.extracted_path(fixture).read_bytes(), entry.sha256)
    return project_document(
        raw,
        _VERDICT,
        source_hash=entry.sha256,
        contributor_rfc=stored_package.manifest.client_rfc,
        artifact_uuid=stored_package.member(fixture).artifact_uuid,
    )


def test_projectable_artifact_keeps_the_uuid_its_name_carries(
    stored_package: StoredPackage,
) -> None:
    processed = _project(stored_package, "cfdi_tfd_4_0.xml")
    assert not processed.is_quarantined
    assert processed.uuid is not None
    assert processed.uuid.value == _TFD_UUID.upper()  # canonical fiscal identity (§6)


def test_projection_carries_the_verdict_flag_through_the_chain(
    stored_package: StoredPackage,
) -> None:
    """Review state is additive end to end: the signature verdict survives to the result."""
    processed = _project(stored_package, "cfdi_tfd_4_0.xml")
    assert processed.review_flags.has_open(ReviewFlagType.CFDI_SIGNATURE)


def test_artifact_without_a_tfd_uuid_is_quarantined(stored_package: StoredPackage) -> None:
    processed = _project(stored_package, "cfdi_egreso_4_0.xml")
    assert processed.is_quarantined
    assert processed.quarantine_reason == "no TFD UUID: no trustworthy document identity"
    assert processed.uuid is None


def test_unsupported_version_is_quarantined_before_identity_is_considered(
    stored_package: StoredPackage,
) -> None:
    processed = _project(stored_package, "cfdi_ingreso_3_3.xml")
    assert processed.quarantine_reason == "no fiscal projection: CFDI version 3.3 unsupported"


def test_chain_separates_projectable_from_refused_artifacts(
    stored_package: StoredPackage,
) -> None:
    """The population one real package yields: both outcomes, from disk evidence alone."""
    results = {m.fixture: _project(stored_package, m.fixture) for m in stored_package.members}
    assert {f for f, r in results.items() if not r.is_quarantined} == _PROJECTABLE
    assert {f for f, r in results.items() if r.is_quarantined} == _REFUSED
