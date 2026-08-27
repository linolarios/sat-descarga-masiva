"""In-memory adapters for TokenStore, RequestRepository and the M1 ledger repos."""

from __future__ import annotations

from sat_descarga_masiva.domain.enums.catalog import Direction, ServiceType
from sat_descarga_masiva.domain.errors import SourceHashConflict
from sat_descarga_masiva.domain.model.cursor import DownloadCursor
from sat_descarga_masiva.domain.model.ledger import DownloadJob
from sat_descarga_masiva.domain.model.source import SourceIdentity
from sat_descarga_masiva.domain.model.token import AccessToken
from sat_descarga_masiva.domain.model.value_objects import RequestId, Rfc


class InMemoryTokenStore:
    """TokenStore port backed by an in-memory dict (nothing persists)."""

    def __init__(self) -> None:
        self._tokens: dict[str, AccessToken] = {}

    def get(self, key: str) -> AccessToken | None:
        return self._tokens.get(key)

    def save(self, key: str, token: AccessToken) -> None:
        self._tokens[key] = token


class InMemoryRequestRepository:
    """RequestRepository port: tracks request ids in memory for resume."""

    def __init__(self) -> None:
        self._request_ids: set[RequestId] = set()

    def save(self, request_id: RequestId) -> None:
        self._request_ids.add(request_id)

    def get(self, request_id: RequestId) -> RequestId | None:
        return request_id if request_id in self._request_ids else None


class InMemoryDownloadJobRepository:
    """DownloadJobRepository backed by a dict keyed by job_id."""

    def __init__(self) -> None:
        self._jobs: dict[str, DownloadJob] = {}

    def save(self, job: DownloadJob) -> None:
        self._jobs[job.job_id] = job

    def get(self, job_id: str) -> DownloadJob | None:
        return self._jobs.get(job_id)


class InMemoryDownloadCursorRepository:
    """DownloadCursorRepository backed by a dict keyed by (rfc, service, direction)."""

    def __init__(self) -> None:
        self._cursors: dict[tuple[str, str, str], DownloadCursor] = {}

    def get(
        self, client_rfc: Rfc, service: ServiceType, direction: Direction
    ) -> DownloadCursor | None:
        return self._cursors.get((client_rfc.value, service.value, direction.value))

    def save(self, cursor: DownloadCursor) -> None:
        self._cursors[(cursor.client_rfc.value, cursor.service.value, cursor.direction.value)] = (
            cursor
        )


class InMemorySourceIdentityIndex:
    """SourceIdentityIndex backed by a dict keyed by uuid."""

    def __init__(self) -> None:
        self._records: dict[str, SourceIdentity] = {}

    def record(self, identity: SourceIdentity) -> None:
        existing = self._records.get(identity.uuid)
        if existing is not None:
            if existing.sha256 == identity.sha256:
                return  # duplicate artifact: idempotent no-op
            raise SourceHashConflict(
                f"source uuid {identity.uuid} already recorded with a different SHA-256"
            )
        self._records[identity.uuid] = identity

    def get(self, uuid: str) -> SourceIdentity | None:
        return self._records.get(uuid)
