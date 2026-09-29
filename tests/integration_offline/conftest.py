"""Tier-2 harness (AGENT.md §12 (2)): the offline full chain, executed in CI.

`make test` / `make check` select `-m "not integration"`; the `integration` marker is
reserved for the opt-in live-SAT tier (§12 (3)). Nothing in this directory carries that
marker, so this chain runs on every check — only the live tier is opt-in.

The chain is exercised on the *evidence on disk*, not on in-memory values: the source
store writes the package, the extractor reads what the store wrote, and the parser reads
the file the extractor wrote. That is the seam tier-1 unit tests cannot cross.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest

from fixtures.package_builder import (
    FIXTURES,
    PACKAGE_NAME,
    PackageMember,
    build_package_bytes,
    package_members,
)
from sat_descarga_masiva.application.policies.extraction import ExtractionPolicy
from sat_descarga_masiva.domain.enums.catalog import (
    Direction,
    DocumentStatus,
    RequestType,
    ServiceType,
)
from sat_descarga_masiva.domain.model.manifest import Manifest, build_manifest
from sat_descarga_masiva.domain.model.query import DownloadQuery
from sat_descarga_masiva.domain.model.source import ExtractedXml, sha256_hex
from sat_descarga_masiva.domain.model.value_objects import (
    DateRange,
    PackageId,
    RequestId,
    Rfc,
)
from sat_descarga_masiva.infrastructure.sat.extract.extractor import SafeZipExtractor
from sat_descarga_masiva.infrastructure.source.codec import manifest_from_dict
from sat_descarga_masiva.infrastructure.source.filesystem_sink import (
    FilesystemSourceArtifactSink,
)

RFC = Rfc("AAA010101AAA")
RID = RequestId("4e80345d-917f-40bb-a98f-4a73939353c5")
PID = PackageId("4e80345d-917f-40bb-a98f-4a73939353c5_01")

#: The fixed acquisition instant: tier-2 evidence is deterministic, never "now".
DOWNLOADED_AT = datetime(2026, 1, 31, 12)

EXTRACTION_POLICY = ExtractionPolicy(max_total_bytes=1024 * 1024, max_entries=100)

QUERY = DownloadQuery(
    service=ServiceType.CFDI,
    direction=Direction.RECIBIDOS,
    request_type=RequestType.CFDI,
    date_range=DateRange(datetime(2026, 1, 1), datetime(2026, 1, 31)),
    rfc_solicitante=RFC,
    document_status=DocumentStatus.VIGENTE,
)


@dataclass(frozen=True)
class StoredPackage:
    """The chain's evidence, every path rooted on disk instead of in memory."""

    root: Path
    manifest: Manifest
    members: tuple[PackageMember, ...]
    extracted: dict[str, ExtractedXml]  # by artifact uuid

    def member(self, fixture: str) -> PackageMember:
        for member in self.members:
            if member.fixture == fixture:
                return member
        raise AssertionError(f"{fixture} is not part of the stored package")

    def entry(self, fixture: str) -> ExtractedXml:
        return self.extracted[self.member(fixture).artifact_uuid]

    def extracted_path(self, fixture: str) -> Path:
        entry = self.entry(fixture)
        return self.root / "extracted" / entry.tipo / f"{entry.uuid}.xml"

    def stored_zip_path(self) -> Path:
        return self.root / "raw" / f"{self.manifest.sha256}.zip"

    def sidecar_path(self) -> Path:
        return self.root / "raw" / f"{self.manifest.sha256}.manifest.json"

    def sidecar(self) -> Manifest:
        """The manifest read back from `manifest.json` on disk (§6 resume path)."""
        return manifest_from_dict(json.loads(self.sidecar_path().read_text()))


def build_manifest_for(sha256: str) -> Manifest:
    """A complete, fixed manifest for the package whose digest is `sha256`."""
    return build_manifest(
        sha256=sha256,
        client_rfc=RFC,
        service=ServiceType.CFDI,
        direction=Direction.RECIBIDOS,
        request_id=RID,
        package_id=PID,
        downloaded_at=DOWNLOADED_AT,
        satcfdi_version="26.8.0",
        application_version="0.1.0",
        query=QUERY,
        policy_version=1,
    )


@pytest.fixture()
def committed_package_bytes() -> bytes:
    """The committed package fixture: the evidence under test."""
    return (FIXTURES / PACKAGE_NAME).read_bytes()


@pytest.fixture()
def rebuilt_package_bytes() -> bytes:
    """The package the builder produces right now (byte-comparable, no clock)."""
    return build_package_bytes()


@pytest.fixture()
def stored_package(tmp_path: Path, committed_package_bytes: bytes) -> StoredPackage:
    """Store the package, then extract it — the full offline chain, all on disk."""
    root = tmp_path / "source"
    manifest = build_manifest_for(sha256_hex(committed_package_bytes))
    FilesystemSourceArtifactSink(root).store(manifest, committed_package_bytes)
    results = SafeZipExtractor(root, EXTRACTION_POLICY).extract(
        manifest.package_id, committed_package_bytes
    )
    return StoredPackage(
        root=root,
        manifest=manifest,
        members=package_members(),
        extracted={entry.uuid: entry for entry in results},
    )
