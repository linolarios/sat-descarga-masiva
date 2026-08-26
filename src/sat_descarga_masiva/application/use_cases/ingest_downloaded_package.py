"""Ingest a downloaded package: store the raw artifact, then extract (M1).

Kept as a SEPARATE use case from ExecuteDownloadUseCase, which stays a pure
SAT round-trip. Order matters: the immutable raw store is written FIRST (the
authoritative evidence). If extraction then fails, the raw store is left intact
and extraction is independently re-runnable (AGENT.md §6a #4 / boundary).
"""

from __future__ import annotations

from sat_descarga_masiva.application.ports.source import PackageExtractor, SourceArtifactSink
from sat_descarga_masiva.domain.model.manifest import Manifest
from sat_descarga_masiva.domain.model.results import Package
from sat_descarga_masiva.domain.model.source import ExtractedXml


class IngestDownloadedPackageUseCase:
    """Store the raw package immutably, then safely extract it."""

    def __init__(self, *, sink: SourceArtifactSink, extractor: PackageExtractor) -> None:
        self._sink = sink
        self._extractor = extractor

    def ingest(self, package: Package, manifest: Manifest) -> tuple[ExtractedXml, ...]:
        self._sink.store(manifest, package.content)
        return self._extractor.extract(package.package_id, package.content)
