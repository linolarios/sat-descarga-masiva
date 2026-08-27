"""Ports for resumability and package storage."""

from __future__ import annotations

from typing import Protocol

from sat_descarga_masiva.domain.enums.catalog import Direction, ServiceType
from sat_descarga_masiva.domain.model.cursor import DownloadCursor
from sat_descarga_masiva.domain.model.ledger import DownloadJob
from sat_descarga_masiva.domain.model.results import Package
from sat_descarga_masiva.domain.model.source import SourceIdentity
from sat_descarga_masiva.domain.model.token import AccessToken
from sat_descarga_masiva.domain.model.value_objects import PackageId, RequestId, Rfc


class TokenStore(Protocol):
    def get(self, key: str) -> AccessToken | None: ...
    def save(self, key: str, token: AccessToken) -> None: ...


class RequestRepository(Protocol):
    def save(self, request_id: RequestId) -> None: ...
    def get(self, request_id: RequestId) -> RequestId | None: ...


class PackageSink(Protocol):
    def store(self, package: Package) -> PackageId: ...


class DownloadJobRepository(Protocol):
    """M1-owned `download_jobs` table: one row per download run (resumable)."""

    def save(self, job: DownloadJob) -> None: ...
    def get(self, job_id: str) -> DownloadJob | None: ...


class DownloadCursorRepository(Protocol):
    """M1-owned `download_cursors` table: resume marker per (rfc, service, direction)."""

    def get(
        self, client_rfc: Rfc, service: ServiceType, direction: Direction
    ) -> DownloadCursor | None: ...
    def save(self, cursor: DownloadCursor) -> None: ...


class SourceIdentityIndex(Protocol):
    """M1 UUID -> extracted-XML SHA-256 dedup index (§6, NOT the manifest sidecar).

    Recording an identity whose uuid already exists with a DIFFERENT sha256
    raises ``SourceHashConflict`` (caller routes to NEEDS_REVIEW); the stored
    identity is never overwritten. Re-recording the same (uuid, sha256) is a
    silent no-op; a new uuid is inserted.
    """

    def record(self, identity: SourceIdentity) -> None: ...
    def get(self, uuid: str) -> SourceIdentity | None: ...
