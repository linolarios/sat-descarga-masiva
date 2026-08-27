"""Thin public facade for SAT ingestion (AGENT.md §4 #9). Orchestration only.

The facade is the public seam: it runs the download use case, builds one
Manifest per downloaded package, and hands each to the ingest use case. It
contains NO SAT/accounting/fiscal/persistence/retry logic — those belong to
the application use cases and infrastructure adapters.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sat_descarga_masiva.application.ports.services import Clock
from sat_descarga_masiva.domain.model.manifest import Manifest, build_manifest
from sat_descarga_masiva.domain.model.query import DownloadQuery
from sat_descarga_masiva.domain.model.results import DownloadOutcome, Package
from sat_descarga_masiva.domain.model.source import ExtractedXml, sha256_hex


@dataclass(frozen=True)
class VersionInfo:
    """Adaptor/application versions stamped onto each §6 manifest."""

    satcfdi_version: str
    application_version: str


class _Executor(Protocol):
    def execute(self, query: DownloadQuery) -> DownloadOutcome: ...


class _Ingester(Protocol):
    def ingest(self, package: Package, manifest: Manifest) -> tuple[ExtractedXml, ...]: ...


class SatDownloadClient:
    """Chain ExecuteDownloadUseCase -> Manifest build -> IngestDownloadedPackageUseCase."""

    def __init__(
        self,
        *,
        executor: _Executor,
        ingester: _Ingester,
        clock: Clock,
        versions: VersionInfo,
        policy_version: int = 1,
    ) -> None:
        self._executor = executor
        self._ingester = ingester
        self._clock = clock
        self._versions = versions
        self._policy_version = policy_version

    def download_and_ingest(self, query: DownloadQuery) -> tuple[ExtractedXml, ...]:
        outcome = self._executor.execute(query)
        results: list[ExtractedXml] = []
        for package in outcome.packages:
            manifest = build_manifest(
                sha256=sha256_hex(package.content),
                client_rfc=query.rfc_solicitante,
                service=query.service,
                direction=query.direction,
                request_id=outcome.request_id,
                package_id=package.package_id,
                downloaded_at=self._clock.now(),
                satcfdi_version=self._versions.satcfdi_version,
                application_version=self._versions.application_version,
                query=query,
                policy_version=self._policy_version,
            )
            results.extend(self._ingester.ingest(package, manifest))
        return tuple(results)
