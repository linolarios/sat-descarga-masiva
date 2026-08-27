"""SQLite adapters for M1-owned ledger tables (AGENT.md §4 persistence rule).

Connections are configured with ``row_factory = sqlite3.Row`` so rows are read
by column name (never positional) — keeps mypy --strict honest about the schema.
Schema creation currently lives here (per repository) for M1; a central
``init_schema(conn)`` is deferred until M2 adds tables (AGENT.md §11).

Datetimes are normalized to tz-aware UTC on write so cursor-resume math never
mixes naive and aware timestamps.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from sat_descarga_masiva.domain.enums.catalog import Direction, ServiceType
from sat_descarga_masiva.domain.errors import SourceHashConflict
from sat_descarga_masiva.domain.model.cursor import DownloadCursor
from sat_descarga_masiva.domain.model.ledger import DownloadJob, JobStatus
from sat_descarga_masiva.domain.model.source import SourceIdentity
from sat_descarga_masiva.domain.model.value_objects import RequestId, Rfc

_CREATE_DOWNLOAD_JOBS = """
CREATE TABLE IF NOT EXISTS download_jobs (
    job_id TEXT PRIMARY KEY,
    client_rfc TEXT NOT NULL,
    service TEXT NOT NULL,
    direction TEXT NOT NULL,
    request_id TEXT NOT NULL,
    query_start TEXT NOT NULL,
    query_end TEXT NOT NULL,
    policy_version INTEGER NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    completed_at TEXT
)
"""

_CREATE_DOWNLOAD_CURSORS = """
CREATE TABLE IF NOT EXISTS download_cursors (
    client_rfc TEXT NOT NULL,
    service TEXT NOT NULL,
    direction TEXT NOT NULL,
    query_start TEXT NOT NULL,
    query_end TEXT NOT NULL,
    last_successful_boundary TEXT,
    last_request_id TEXT,
    last_completed_at TEXT,
    PRIMARY KEY (client_rfc, service, direction)
)
"""

_CREATE_SOURCE_RECORDS = """
CREATE TABLE IF NOT EXISTS source_records (
    uuid TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL
)
"""

_INSERT_JOB = """
INSERT OR REPLACE INTO download_jobs (
    job_id, client_rfc, service, direction, request_id, query_start, query_end,
    policy_version, status, created_at, completed_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_SELECT_JOB = """
SELECT
    job_id, client_rfc, service, direction, request_id, query_start, query_end,
    policy_version, status, created_at, completed_at
FROM download_jobs
WHERE job_id = ?
"""

_UPSERT_CURSOR = """
INSERT OR REPLACE INTO download_cursors (
    client_rfc, service, direction, query_start, query_end,
    last_successful_boundary, last_request_id, last_completed_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""

_SELECT_CURSOR = """
SELECT
    client_rfc, service, direction, query_start, query_end,
    last_successful_boundary, last_request_id, last_completed_at
FROM download_cursors
WHERE client_rfc = ? AND service = ? AND direction = ?
"""

_INSERT_SOURCE = """
INSERT INTO source_records (uuid, sha256) VALUES (?, ?)
"""

_SELECT_SOURCE = """
SELECT uuid, sha256 FROM source_records WHERE uuid = ?
"""


def _iso(value: datetime) -> str:
    """Serialize as tz-aware UTC (naive treated as UTC; aware converted to UTC)."""
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=UTC)
    else:
        value = value.astimezone(UTC)
    return value.isoformat()


def _from_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _job_from_row(row: sqlite3.Row) -> DownloadJob:
    return DownloadJob(
        job_id=row["job_id"],
        client_rfc=Rfc(row["client_rfc"]),
        service=ServiceType(row["service"]),
        direction=Direction(row["direction"]),
        request_id=RequestId(row["request_id"]),
        query_start=_from_iso(row["query_start"]),
        query_end=_from_iso(row["query_end"]),
        policy_version=row["policy_version"],
        status=JobStatus(row["status"]),
        created_at=_from_iso(row["created_at"]),
        completed_at=_from_iso(row["completed_at"]) if row["completed_at"] is not None else None,
    )


def _cursor_from_row(row: sqlite3.Row) -> DownloadCursor:
    return DownloadCursor(
        client_rfc=Rfc(row["client_rfc"]),
        service=ServiceType(row["service"]),
        direction=Direction(row["direction"]),
        query_start=_from_iso(row["query_start"]),
        query_end=_from_iso(row["query_end"]),
        last_successful_boundary=_from_iso(row["last_successful_boundary"])
        if row["last_successful_boundary"] is not None
        else None,
        last_request_id=RequestId(row["last_request_id"])
        if row["last_request_id"] is not None
        else None,
        last_completed_at=_from_iso(row["last_completed_at"])
        if row["last_completed_at"] is not None
        else None,
    )


class SqliteDownloadJobRepository:
    """DownloadJobRepository over the `download_jobs` table."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        conn.row_factory = sqlite3.Row
        self._conn = conn
        self._conn.execute(_CREATE_DOWNLOAD_JOBS)

    def save(self, job: DownloadJob) -> None:
        self._conn.execute(
            _INSERT_JOB,
            (
                job.job_id,
                job.client_rfc.value,
                job.service.value,
                job.direction.value,
                job.request_id.value,
                _iso(job.query_start),
                _iso(job.query_end),
                job.policy_version,
                job.status.value,
                _iso(job.created_at),
                _iso(job.completed_at) if job.completed_at is not None else None,
            ),
        )
        self._conn.commit()

    def get(self, job_id: str) -> DownloadJob | None:
        row = self._conn.execute(_SELECT_JOB, (job_id,)).fetchone()
        return None if row is None else _job_from_row(row)


class SqliteDownloadCursorRepository:
    """DownloadCursorRepository over the `download_cursors` table."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        conn.row_factory = sqlite3.Row
        self._conn = conn
        self._conn.execute(_CREATE_DOWNLOAD_CURSORS)

    def get(
        self, client_rfc: Rfc, service: ServiceType, direction: Direction
    ) -> DownloadCursor | None:
        row = self._conn.execute(
            _SELECT_CURSOR, (client_rfc.value, service.value, direction.value)
        ).fetchone()
        return None if row is None else _cursor_from_row(row)

    def save(self, cursor: DownloadCursor) -> None:
        self._conn.execute(
            _UPSERT_CURSOR,
            (
                cursor.client_rfc.value,
                cursor.service.value,
                cursor.direction.value,
                _iso(cursor.query_start),
                _iso(cursor.query_end),
                _iso(cursor.last_successful_boundary)
                if cursor.last_successful_boundary is not None
                else None,
                cursor.last_request_id.value if cursor.last_request_id is not None else None,
                _iso(cursor.last_completed_at) if cursor.last_completed_at is not None else None,
            ),
        )
        self._conn.commit()


class SqliteSourceIdentityIndex:
    """UUID -> extracted-XML SHA-256 dedup index (AGENT.md §6).

    This is a *dedup index*, NOT the per-package manifest (that lives in the
    ``source/raw/*.manifest.json`` sidecar, commit 9). The sha256 recorded here
    is the **extracted-XML** digest (the future ``posting_snapshot.source_hash``,
    §5/§11 M3) — distinct from the ZIP hash stored on the manifest sidecar.

    Uniqueness is authoritative in the DB. A UUID with a DIFFERENT sha256 is an
    integrity conflict and raises ``SourceHashConflict`` (caller routes to
    NEEDS_REVIEW); the existing record is NEVER overwritten.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        conn.row_factory = sqlite3.Row
        self._conn = conn
        self._conn.execute(_CREATE_SOURCE_RECORDS)

    def record(self, identity: SourceIdentity) -> None:
        existing = self._conn.execute(_SELECT_SOURCE, (identity.uuid,)).fetchone()
        if existing is not None:
            if existing["sha256"] == identity.sha256:
                return  # duplicate artifact: idempotent no-op
            raise SourceHashConflict(
                f"source uuid {identity.uuid} already recorded with a different SHA-256"
            )
        self._conn.execute(_INSERT_SOURCE, (identity.uuid, identity.sha256))
        self._conn.commit()

    def get(self, uuid: str) -> SourceIdentity | None:
        row = self._conn.execute(_SELECT_SOURCE, (uuid,)).fetchone()
        return None if row is None else SourceIdentity(uuid=row["uuid"], sha256=row["sha256"])
