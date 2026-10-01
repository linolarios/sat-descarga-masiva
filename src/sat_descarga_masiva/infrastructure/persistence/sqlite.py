"""SQLite adapters for the ledger tables (AGENT.md §4 persistence rule).

Connections are configured with ``row_factory = sqlite3.Row`` so rows are read
by column name (never positional) — keeps mypy --strict honest about the schema.

Schema: M2 introduces the authoritative ``init_schema(conn)`` (§11 M2 adds
tables). Each repository still executes its own defensive
``CREATE TABLE IF NOT EXISTS`` on construction (the M1 convention, which keeps a
bare ``sqlite3.connect(":memory:")`` usable in tests); ``init_schema`` is the
entry point that migrates an existing database — pre-M2 databases carry
``PRAGMA user_version = 0`` because M1 never versioned the schema. Migration only
adds: it never drops, recreates or rewrites existing rows.

Datetimes are normalized to tz-aware UTC on write so cursor-resume math never
mixes naive and aware timestamps. Money is never stored here as REAL/FLOAT; the
canonical Decimal <-> TEXT codec is
``domain.policy.money.amount_as_text``/``amount_from_text``.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import UTC, date, datetime

from sat_descarga_masiva.domain.enums.catalog import Direction, ServiceType
from sat_descarga_masiva.domain.errors import (
    ImmutableRecordConflict,
    ReviewFlagNotFound,
    SourceHashConflict,
)
from sat_descarga_masiva.domain.model.contributor import (
    ContributorProfile,
    ContributorProfileRecord,
    ObligacionFiscal,
    PersonaTipo,
    RegimenFiscal,
    SituacionFiscal,
)
from sat_descarga_masiva.domain.model.csf import CsfArtifact
from sat_descarga_masiva.domain.model.cursor import DownloadCursor
from sat_descarga_masiva.domain.model.documents import DocumentRecord
from sat_descarga_masiva.domain.model.fiscal_event import FiscalEvent
from sat_descarga_masiva.domain.model.ledger import DownloadJob, JobStatus
from sat_descarga_masiva.domain.model.perspective import Perspective
from sat_descarga_masiva.domain.model.pipeline_run import (
    PipelineFlow,
    PipelineRun,
    StageReport,
)
from sat_descarga_masiva.domain.model.review import (
    ReviewFlag,
    ReviewFlagRecord,
    ReviewFlagState,
    ReviewFlagType,
    ReviewSubjectKind,
)
from sat_descarga_masiva.domain.model.source import SourceIdentity
from sat_descarga_masiva.domain.model.value_objects import RequestId, Rfc, Uuid

SCHEMA_VERSION = 3
"""SQLite schema version written to ``PRAGMA user_version`` by ``init_schema``.

1 = M1 ledger tables (download_jobs, download_cursors, source_records)
2 = M2 tables (documents, fiscal_events, contributor_profiles, csf_artifacts,
    review_flags, pipeline_runs) + download_jobs.pipeline_run_id
3 = M2-E identity completeness (profile_obligaciones) + a nullable
    contributor_profiles.codigo_postal
"""

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
    completed_at TEXT,
    pipeline_run_id TEXT
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
    policy_version, status, created_at, completed_at, pipeline_run_id
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_SELECT_JOB = """
SELECT
    job_id, client_rfc, service, direction, request_id, query_start, query_end,
    policy_version, status, created_at, completed_at, pipeline_run_id
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

_CREATE_DOCUMENTS = """
CREATE TABLE IF NOT EXISTS documents (
    uuid TEXT PRIMARY KEY,
    contributor_rfc TEXT NOT NULL,
    perspective TEXT NOT NULL,
    tipo TEXT NOT NULL,
    version TEXT NOT NULL,
    moneda TEXT,
    emisor_rfc TEXT NOT NULL,
    receptor_rfc TEXT,
    source_hash TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    last_run_id TEXT
)
"""

_CREATE_FISCAL_EVENTS = """
CREATE TABLE IF NOT EXISTS fiscal_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT NOT NULL,
    contributor_rfc TEXT NOT NULL,
    kind TEXT NOT NULL,
    effective_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    detail_json TEXT,
    UNIQUE (uuid, contributor_rfc, kind, effective_at, source_hash)
)
"""

_CREATE_CONTRIBUTOR_PROFILES = """
CREATE TABLE IF NOT EXISTS contributor_profiles (
    client_rfc TEXT NOT NULL,
    profile_version INTEGER NOT NULL,
    nombre TEXT NOT NULL,
    persona_tipo TEXT NOT NULL,
    regimen_fiscal_code TEXT NOT NULL,
    regimen_fiscal_description TEXT NOT NULL,
    situacion_fiscal TEXT NOT NULL,
    fecha_inicio_operaciones TEXT,
    codigo_postal TEXT,
    csf_hash TEXT NOT NULL,
    csf_obtained_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    PRIMARY KEY (client_rfc, profile_version)
)
"""

#: The obligation list is a child table of a profile version, not a JSON/TEXT blob:
#: §7a's profile is versioned and immutable, so its items must be readable as rows and
#: must keep their source order — `ordinal` is that order, and (client_rfc,
#: profile_version, ordinal) is the key. The parent link is a recorded key verified by
#: the test suite; SQLite foreign keys are not enforced anywhere in this repository.
_CREATE_PROFILE_OBLIGACIONES = """
CREATE TABLE IF NOT EXISTS profile_obligaciones (
    client_rfc TEXT NOT NULL,
    profile_version INTEGER NOT NULL,
    ordinal INTEGER NOT NULL,
    code TEXT NOT NULL,
    description TEXT NOT NULL,
    PRIMARY KEY (client_rfc, profile_version, ordinal)
)
"""

_CREATE_CSF_ARTIFACTS = """
CREATE TABLE IF NOT EXISTS csf_artifacts (
    sha256 TEXT PRIMARY KEY,
    stored_path TEXT NOT NULL,
    recorded_at TEXT NOT NULL
)
"""

_CREATE_REVIEW_FLAGS = """
CREATE TABLE IF NOT EXISTS review_flags (
    flag_id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_kind TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    flag_type TEXT NOT NULL,
    reason TEXT NOT NULL,
    state TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    closes_flag_id INTEGER REFERENCES review_flags (flag_id),
    UNIQUE (closes_flag_id)
)
"""

_CREATE_PIPELINE_RUNS = """
CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id TEXT PRIMARY KEY,
    flow TEXT NOT NULL,
    client_rfc TEXT NOT NULL,
    period TEXT,
    status TEXT NOT NULL,
    failed_stage TEXT,
    per_stage_json TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT
)
"""

_MIGRATIONS: dict[int, tuple[str, ...]] = {
    1: (_CREATE_DOWNLOAD_JOBS, _CREATE_DOWNLOAD_CURSORS, _CREATE_SOURCE_RECORDS),
    2: (
        _CREATE_DOCUMENTS,
        _CREATE_FISCAL_EVENTS,
        _CREATE_CONTRIBUTOR_PROFILES,
        _CREATE_CSF_ARTIFACTS,
        _CREATE_REVIEW_FLAGS,
        _CREATE_PIPELINE_RUNS,
    ),
    3: (_CREATE_PROFILE_OBLIGACIONES,),
}

_DOCUMENT_COLUMNS = """
    uuid, contributor_rfc, perspective, tipo, version, moneda, emisor_rfc,
    receptor_rfc, source_hash, first_seen_at, last_seen_at, last_run_id
"""

_INSERT_DOCUMENT = f"""
INSERT INTO documents ({_DOCUMENT_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_SELECT_DOCUMENT = f"""
SELECT {_DOCUMENT_COLUMNS} FROM documents WHERE uuid = ?
"""

_UPDATE_DOCUMENT_PROJECTION = """
UPDATE documents SET last_seen_at = ?, last_run_id = ? WHERE uuid = ?
"""

_EVENT_COLUMNS = """
    uuid, contributor_rfc, kind, effective_at, recorded_at, source_hash, detail_json
"""

_INSERT_EVENT = f"""
INSERT INTO fiscal_events ({_EVENT_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?)
ON CONFLICT DO NOTHING
"""

_SELECT_EVENTS = f"""
SELECT {_EVENT_COLUMNS} FROM fiscal_events WHERE uuid = ? ORDER BY event_id
"""

_PROFILE_COLUMNS = """
    client_rfc, profile_version, nombre, persona_tipo, regimen_fiscal_code,
    regimen_fiscal_description, situacion_fiscal, fecha_inicio_operaciones,
    codigo_postal, csf_hash, csf_obtained_at, recorded_at
"""

_INSERT_PROFILE = f"""
INSERT INTO contributor_profiles ({_PROFILE_COLUMNS})
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_SELECT_PROFILE = f"""
SELECT {_PROFILE_COLUMNS} FROM contributor_profiles
WHERE client_rfc = ? AND profile_version = ?
"""

_SELECT_LATEST_PROFILE = f"""
SELECT {_PROFILE_COLUMNS} FROM contributor_profiles
WHERE client_rfc = ? ORDER BY profile_version DESC LIMIT 1
"""

_INSERT_OBLIGACION = """
INSERT INTO profile_obligaciones (client_rfc, profile_version, ordinal, code, description)
VALUES (?, ?, ?, ?, ?)
"""

_SELECT_OBLIGACIONES = """
SELECT code, description FROM profile_obligaciones
WHERE client_rfc = ? AND profile_version = ? ORDER BY ordinal
"""

_INSERT_CSF_ARTIFACT = """
INSERT INTO csf_artifacts (sha256, stored_path, recorded_at) VALUES (?, ?, ?)
"""

_SELECT_CSF_ARTIFACT = """
SELECT sha256, stored_path, recorded_at FROM csf_artifacts WHERE sha256 = ?
"""

_FLAG_COLUMNS = """
    flag_id, subject_kind, subject_id, flag_type, reason, state, occurred_at,
    closes_flag_id
"""

_INSERT_FLAG = """
INSERT INTO review_flags (
    subject_kind, subject_id, flag_type, reason, state, occurred_at, closes_flag_id
) VALUES (?, ?, ?, ?, ?, ?, ?)
"""

_SELECT_FLAG = f"""
SELECT {_FLAG_COLUMNS} FROM review_flags WHERE flag_id = ?
"""

_SELECT_FLAGS = f"""
SELECT {_FLAG_COLUMNS} FROM review_flags
WHERE subject_kind = ? AND subject_id = ? ORDER BY flag_id
"""

_SELECT_FLAG_CLOSURE = f"""
SELECT {_FLAG_COLUMNS} FROM review_flags WHERE closes_flag_id = ?
"""

_SELECT_OPEN_FLAGS = f"""
SELECT {_FLAG_COLUMNS} FROM review_flags
WHERE subject_kind = ? AND subject_id = ? AND closes_flag_id IS NULL
  AND flag_id NOT IN (SELECT closes_flag_id FROM review_flags WHERE closes_flag_id IS NOT NULL)
ORDER BY flag_id
"""

_RUN_COLUMNS = """
    run_id, flow, client_rfc, period, status, failed_stage, per_stage_json,
    started_at, finished_at
"""

_UPSERT_RUN = f"""
INSERT INTO pipeline_runs ({_RUN_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (run_id) DO UPDATE SET
    flow = excluded.flow,
    client_rfc = excluded.client_rfc,
    period = excluded.period,
    status = excluded.status,
    failed_stage = excluded.failed_stage,
    per_stage_json = excluded.per_stage_json,
    started_at = excluded.started_at,
    finished_at = excluded.finished_at
"""

_SELECT_RUN = f"""
SELECT {_RUN_COLUMNS} FROM pipeline_runs WHERE run_id = ?
"""

_SELECT_RUNS_FOR_CLIENT = f"""
SELECT {_RUN_COLUMNS} FROM pipeline_runs WHERE client_rfc = ? ORDER BY started_at, run_id
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


def _utc(value: datetime) -> datetime:
    """Normalize to tz-aware UTC (naive treated as UTC) — the same rule as _iso."""
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _user_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def _ensure_run_link(conn: sqlite3.Connection) -> None:
    """Add `download_jobs.pipeline_run_id` to a database created before M2 (§7).

    M1 databases have no ``user_version``, so a plain CREATE TABLE would leave an
    existing `download_jobs` without the run link; this is the one additive ALTER
    of the ladder. Guarded by a column check (and skipped when the table is not
    there yet), so it is idempotent.
    """
    columns = _columns(conn, "download_jobs")
    if columns and "pipeline_run_id" not in columns:
        conn.execute("ALTER TABLE download_jobs ADD COLUMN pipeline_run_id TEXT")


def _ensure_profile_postal_code(conn: sqlite3.Connection) -> None:
    """Add `contributor_profiles.codigo_postal` to a schema-2 database (M2-E).

    The v3 step is the child table; the postal code is a new column on a table that
    already exists, so it is added by the same guarded, additive ALTER shape M2 used
    for the run link. Existing rows read as ``NULL``, which is exactly "the constancia
    did not state a postal code" — nothing is backfilled or invented.
    """
    columns = _columns(conn, "contributor_profiles")
    if columns and "codigo_postal" not in columns:
        conn.execute("ALTER TABLE contributor_profiles ADD COLUMN codigo_postal TEXT")


def init_schema(conn: sqlite3.Connection) -> None:
    """Create/migrate the SQLite schema up to :data:`SCHEMA_VERSION` (§11 M2).

    Authoritative entry point for the application: applies every missing
    migration step in order (``CREATE TABLE IF NOT EXISTS`` only), then advances
    ``PRAGMA user_version``. Idempotent, and never drops, recreates or rewrites
    existing rows — including rows written by a pre-M2 database. A database
    already stamped with a *newer* version is left at that version (never lowered).
    """
    conn.row_factory = sqlite3.Row
    version = _user_version(conn)
    for step in range(version + 1, SCHEMA_VERSION + 1):
        for statement in _MIGRATIONS[step]:
            conn.execute(statement)
    _ensure_run_link(conn)
    _ensure_profile_postal_code(conn)
    if version < SCHEMA_VERSION:
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()


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
        pipeline_run_id=row["pipeline_run_id"],
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
        _ensure_run_link(self._conn)  # legacy M1 database: add the M2 run link

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
                job.pipeline_run_id,
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


def _document_from_row(row: sqlite3.Row) -> DocumentRecord:
    return DocumentRecord(
        uuid=Uuid(row["uuid"]),
        contributor_rfc=Rfc(row["contributor_rfc"]),
        perspective=Perspective(row["perspective"]),
        tipo=row["tipo"],
        version=row["version"],
        moneda=row["moneda"],
        emisor_rfc=Rfc(row["emisor_rfc"]),
        receptor_rfc=Rfc(row["receptor_rfc"]) if row["receptor_rfc"] is not None else None,
        source_hash=row["source_hash"],
        first_seen_at=_from_iso(row["first_seen_at"]),
        last_seen_at=_from_iso(row["last_seen_at"]),
        last_run_id=row["last_run_id"],
    )


def _event_from_row(row: sqlite3.Row) -> FiscalEvent:
    return FiscalEvent(
        uuid=Uuid(row["uuid"]),
        contributor_rfc=Rfc(row["contributor_rfc"]),
        kind=row["kind"],
        effective_at=_from_iso(row["effective_at"]),
        recorded_at=_from_iso(row["recorded_at"]),
        source_hash=row["source_hash"],
        detail_json=row["detail_json"],
    )


def _profile_from_row(
    row: sqlite3.Row, obligaciones: tuple[ObligacionFiscal, ...] = ()
) -> ContributorProfileRecord:
    """Rebuild a profile version from its own columns plus its obligation rows.

    `obligaciones` is passed in rather than read here, so this stays a pure row mapper:
    the child rows are keyed by the same ``(client_rfc, profile_version)`` the caller
    already has in hand. Nothing defaults an absent postal code to a value — ``NULL``
    (the v2 shape, and any constancia that states none) reads back as ``None``.
    """
    raw_date: str | None = row["fecha_inicio_operaciones"]
    return ContributorProfileRecord(
        profile=ContributorProfile(
            rfc=Rfc(row["client_rfc"]),
            nombre=row["nombre"],
            persona_tipo=PersonaTipo(row["persona_tipo"]),
            regimen_fiscal=RegimenFiscal(
                code=row["regimen_fiscal_code"],
                description=row["regimen_fiscal_description"],
            ),
            situacion_fiscal=SituacionFiscal(row["situacion_fiscal"]),
            fecha_inicio_operaciones=date.fromisoformat(raw_date) if raw_date else None,
            obligaciones=obligaciones,
            codigo_postal=row["codigo_postal"],
        ),
        csf_hash=row["csf_hash"],
        csf_obtained_at=_from_iso(row["csf_obtained_at"]),
        profile_version=row["profile_version"],
        recorded_at=_from_iso(row["recorded_at"]),
    )


def _artifact_from_row(row: sqlite3.Row) -> CsfArtifact:
    return CsfArtifact(
        sha256=row["sha256"],
        stored_path=row["stored_path"],
        recorded_at=_from_iso(row["recorded_at"]),
    )


def _flag_from_row(row: sqlite3.Row) -> ReviewFlagRecord:
    return ReviewFlagRecord(
        flag_id=row["flag_id"],
        subject_kind=ReviewSubjectKind(row["subject_kind"]),
        subject_id=row["subject_id"],
        flag=ReviewFlag(
            flag_type=ReviewFlagType(row["flag_type"]),
            reason=row["reason"],
            state=ReviewFlagState(row["state"]),
        ),
        occurred_at=_from_iso(row["occurred_at"]),
        closes_flag_id=row["closes_flag_id"],
    )


def _per_stage_to_json(per_stage: tuple[StageReport, ...]) -> str:
    return json.dumps(
        [
            {"stage": report.stage, "message": report.message, "counts": report.counts}
            for report in per_stage
        ]
    )


def _per_stage_from_json(text: str) -> tuple[StageReport, ...]:
    payload = json.loads(text)
    return tuple(
        StageReport(
            stage=str(entry["stage"]),
            message=entry["message"],
            counts=tuple((str(key), int(count)) for key, count in entry["counts"]),
        )
        for entry in payload
    )


def _run_from_row(row: sqlite3.Row) -> PipelineRun:
    return PipelineRun(
        run_id=row["run_id"],
        flow=PipelineFlow(row["flow"]),
        client_rfc=Rfc(row["client_rfc"]),
        status=JobStatus(row["status"]),
        started_at=_from_iso(row["started_at"]),
        period=row["period"],
        failed_stage=row["failed_stage"],
        per_stage=_per_stage_from_json(row["per_stage_json"]),
        finished_at=_from_iso(row["finished_at"]) if row["finished_at"] is not None else None,
    )


class SqliteDocumentRepository:
    """DocumentRepository over the `documents` projection (§4).

    A re-save of the same ``(uuid, source_hash)`` identity runs a targeted UPDATE
    of the projection fields only — never ``INSERT OR REPLACE``, which would
    delete and reinsert the row (and later cascade an FK delete) — and the
    identity fields stay write-once. A same-UUID/different-hash save is an
    integrity conflict (§6) and the stored row is left untouched.

    ``commit=False`` makes the write durable only when an explicit unit of work
    (M2.8's per-document transaction) says so: this repository's own COMMIT would
    otherwise end that unit early.
    """

    def __init__(self, conn: sqlite3.Connection, *, commit: bool = True) -> None:
        conn.row_factory = sqlite3.Row
        self._conn = conn
        self._commit_writes = commit
        self._conn.execute(_CREATE_DOCUMENTS)

    def save(self, record: DocumentRecord) -> None:
        incoming = replace(
            record,
            first_seen_at=_utc(record.first_seen_at),
            last_seen_at=_utc(record.last_seen_at),
        )
        existing = self._conn.execute(_SELECT_DOCUMENT, (incoming.uuid.value,)).fetchone()
        if existing is None:
            self._conn.execute(
                _INSERT_DOCUMENT,
                (
                    incoming.uuid.value,
                    incoming.contributor_rfc.value,
                    incoming.perspective.value,
                    incoming.tipo,
                    incoming.version,
                    incoming.moneda,
                    incoming.emisor_rfc.value,
                    incoming.receptor_rfc.value if incoming.receptor_rfc is not None else None,
                    incoming.source_hash,
                    _iso(incoming.first_seen_at),
                    _iso(incoming.last_seen_at),
                    incoming.last_run_id,
                ),
            )
            self._commit_write()
            return
        if existing["source_hash"] != incoming.source_hash:
            raise SourceHashConflict(
                f"document {incoming.uuid.value} already recorded with a different source hash"
            )
        self._conn.execute(
            _UPDATE_DOCUMENT_PROJECTION,
            (
                _iso(max(_from_iso(existing["last_seen_at"]), incoming.last_seen_at)),
                incoming.last_run_id,
                incoming.uuid.value,
            ),
        )
        self._commit_write()

    def get(self, uuid: Uuid) -> DocumentRecord | None:
        row = self._conn.execute(_SELECT_DOCUMENT, (uuid.value,)).fetchone()
        return None if row is None else _document_from_row(row)

    def _commit_write(self) -> None:
        if self._commit_writes:
            self._conn.commit()


class SqliteFiscalEventStore:
    """FiscalEventStore over the append-only `fiscal_events` table (§4).

    ``ON CONFLICT DO NOTHING`` (never ``INSERT OR REPLACE``) makes the identical
    fact an idempotent no-op while any differently-shaped fact inserts: the only
    constraint it can swallow is the row's own UNIQUE identity.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        conn.row_factory = sqlite3.Row
        self._conn = conn
        self._conn.execute(_CREATE_FISCAL_EVENTS)

    def append(self, event: FiscalEvent) -> None:
        incoming = replace(
            event,
            effective_at=_utc(event.effective_at),
            recorded_at=_utc(event.recorded_at),
        )
        self._conn.execute(
            _INSERT_EVENT,
            (
                incoming.uuid.value,
                incoming.contributor_rfc.value,
                incoming.kind,
                _iso(incoming.effective_at),
                _iso(incoming.recorded_at),
                incoming.source_hash,
                incoming.detail_json,
            ),
        )
        self._conn.commit()

    def for_uuid(self, uuid: Uuid) -> tuple[FiscalEvent, ...]:
        rows = self._conn.execute(_SELECT_EVENTS, (uuid.value,)).fetchall()
        return tuple(_event_from_row(row) for row in rows)


class SqliteContributorProfileRepository:
    """ContributorProfileRepository over `contributor_profiles` (§7a).

    ``(client_rfc, profile_version)`` is the primary key: an existing version is
    never updated in place — identical content is a no-op, different content is an
    ``ImmutableRecordConflict`` (a correction is a new version).

    A profile version is written as one unit: the parent row plus one
    `profile_obligaciones` row per obligation. Nothing may land without the other, so a
    failure while writing the obligations rolls the parent row back too. That rollback
    only happens when this repository owns the transaction (``commit=True``, the
    default): M2.8 wires stores with ``commit=False`` inside ``SqliteUnitOfWork``, where
    the unit owns COMMIT *and* ROLLBACK — rolling back there would end the unit's
    transaction behind its back and break its all-or-nothing guarantee.
    """

    def __init__(self, conn: sqlite3.Connection, *, commit: bool = True) -> None:
        conn.row_factory = sqlite3.Row
        self._conn = conn
        self._commit_writes = commit
        self._conn.execute(_CREATE_CONTRIBUTOR_PROFILES)
        self._conn.execute(_CREATE_PROFILE_OBLIGACIONES)

    def save(self, record: ContributorProfileRecord) -> None:
        incoming = replace(
            record,
            csf_obtained_at=_utc(record.csf_obtained_at),
            recorded_at=_utc(record.recorded_at),
        )
        existing = self._conn.execute(
            _SELECT_PROFILE, (incoming.profile.rfc.value, incoming.profile_version)
        ).fetchone()
        if existing is not None:
            if self._record_from_row(existing) != incoming:
                raise ImmutableRecordConflict(
                    f"profile {incoming.profile.rfc.value} version "
                    f"{incoming.profile_version} already recorded with different content"
                )
            return  # identical re-save: idempotent no-op
        start = incoming.profile.fecha_inicio_operaciones
        try:
            self._conn.execute(
                _INSERT_PROFILE,
                (
                    incoming.profile.rfc.value,
                    incoming.profile_version,
                    incoming.profile.nombre,
                    incoming.profile.persona_tipo.value,
                    incoming.profile.regimen_fiscal.code,
                    incoming.profile.regimen_fiscal.description,
                    incoming.profile.situacion_fiscal.value,
                    start.isoformat() if start is not None else None,
                    incoming.profile.codigo_postal,
                    incoming.csf_hash,
                    _iso(incoming.csf_obtained_at),
                    _iso(incoming.recorded_at),
                ),
            )
            self._conn.executemany(
                _INSERT_OBLIGACION,
                [
                    (
                        incoming.profile.rfc.value,
                        incoming.profile_version,
                        ordinal,
                        obligacion.code,
                        obligacion.description,
                    )
                    for ordinal, obligacion in enumerate(incoming.profile.obligaciones)
                ],
            )
        except BaseException:
            if self._commit_writes:
                self._conn.rollback()
            raise
        self._commit_write()

    def get(self, client_rfc: Rfc, profile_version: int) -> ContributorProfileRecord | None:
        row = self._conn.execute(_SELECT_PROFILE, (client_rfc.value, profile_version)).fetchone()
        return None if row is None else self._record_from_row(row)

    def latest(self, client_rfc: Rfc) -> ContributorProfileRecord | None:
        row = self._conn.execute(_SELECT_LATEST_PROFILE, (client_rfc.value,)).fetchone()
        return None if row is None else self._record_from_row(row)

    def _record_from_row(self, row: sqlite3.Row) -> ContributorProfileRecord:
        return _profile_from_row(
            row,
            self._obligaciones(row["client_rfc"], row["profile_version"]),
        )

    def _obligaciones(self, client_rfc: str, profile_version: int) -> tuple[ObligacionFiscal, ...]:
        rows = self._conn.execute(_SELECT_OBLIGACIONES, (client_rfc, profile_version)).fetchall()
        return tuple(ObligacionFiscal(row["code"], row["description"]) for row in rows)

    def _commit_write(self) -> None:
        if self._commit_writes:
            self._conn.commit()


class SqliteCsfArtifactRepository:
    """CsfArtifactRepository over `csf_artifacts`, keyed by the content hash (§7a)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        conn.row_factory = sqlite3.Row
        self._conn = conn
        self._conn.execute(_CREATE_CSF_ARTIFACTS)

    def record(self, artifact: CsfArtifact) -> None:
        incoming = replace(artifact, recorded_at=_utc(artifact.recorded_at))
        existing = self._conn.execute(_SELECT_CSF_ARTIFACT, (incoming.sha256,)).fetchone()
        if existing is not None:
            if _artifact_from_row(existing) != incoming:
                raise ImmutableRecordConflict(
                    f"CSF artifact {incoming.sha256} already recorded with different content"
                )
            return  # identical re-record: idempotent no-op
        self._conn.execute(
            _INSERT_CSF_ARTIFACT,
            (incoming.sha256, incoming.stored_path, _iso(incoming.recorded_at)),
        )
        self._conn.commit()

    def get(self, sha256: str) -> CsfArtifact | None:
        row = self._conn.execute(_SELECT_CSF_ARTIFACT, (sha256,)).fetchone()
        return None if row is None else _artifact_from_row(row)


class SqliteReviewFlagStore:
    """ReviewFlagStore over `review_flags`: additive open/close facts (§8).

    The opening row is never updated: closing inserts a second row whose
    ``closes_flag_id`` (UNIQUE) points at it, so an accidental double closure
    cannot duplicate the fact.

    ``commit=False`` makes the fact durable only when an explicit unit of work
    (M2.8's per-document transaction) says so, so a flag and the document it
    belongs to can never survive one another.
    """

    def __init__(self, conn: sqlite3.Connection, *, commit: bool = True) -> None:
        conn.row_factory = sqlite3.Row
        self._conn = conn
        self._commit_writes = commit
        self._conn.execute(_CREATE_REVIEW_FLAGS)

    def open_flag(
        self,
        *,
        subject_kind: ReviewSubjectKind,
        subject_id: str,
        flag: ReviewFlag,
        occurred_at: datetime,
    ) -> ReviewFlagRecord:
        cursor = self._conn.execute(
            _INSERT_FLAG,
            (
                subject_kind.value,
                subject_id,
                flag.flag_type.value,
                flag.reason,
                ReviewFlagState.OPEN.value,  # an opening fact is always OPEN
                _iso(_utc(occurred_at)),
                None,
            ),
        )
        self._commit_write()
        return self._row(cursor.lastrowid)

    def close_flag(self, *, flag_id: int, occurred_at: datetime) -> ReviewFlagRecord:
        opening = self._conn.execute(_SELECT_FLAG, (flag_id,)).fetchone()
        if opening is None or opening["closes_flag_id"] is not None:
            raise ReviewFlagNotFound(f"no open review flag with flag_id {flag_id}")
        existing = self._conn.execute(_SELECT_FLAG_CLOSURE, (flag_id,)).fetchone()
        if existing is not None:
            return _flag_from_row(existing)  # the first closure is the fact
        opened = _flag_from_row(opening)
        cursor = self._conn.execute(
            _INSERT_FLAG,
            (
                opened.subject_kind.value,
                opened.subject_id,
                opened.flag.flag_type.value,
                opened.flag.reason,
                ReviewFlagState.CLOSED.value,
                _iso(_utc(occurred_at)),
                flag_id,
            ),
        )
        self._commit_write()
        return self._row(cursor.lastrowid)

    def open_flags(
        self, subject_kind: ReviewSubjectKind, subject_id: str
    ) -> tuple[ReviewFlagRecord, ...]:
        rows = self._conn.execute(_SELECT_OPEN_FLAGS, (subject_kind.value, subject_id)).fetchall()
        return tuple(_flag_from_row(row) for row in rows)

    def history(
        self, subject_kind: ReviewSubjectKind, subject_id: str
    ) -> tuple[ReviewFlagRecord, ...]:
        rows = self._conn.execute(_SELECT_FLAGS, (subject_kind.value, subject_id)).fetchall()
        return tuple(_flag_from_row(row) for row in rows)

    def _row(self, flag_id: int | None) -> ReviewFlagRecord:
        if flag_id is None:  # pragma: no cover - sqlite reports it after INSERT
            raise ReviewFlagNotFound("sqlite did not report the new review flag id")
        row = self._conn.execute(_SELECT_FLAG, (flag_id,)).fetchone()
        if row is None:  # pragma: no cover - the row was just inserted
            raise ReviewFlagNotFound(f"no review flag with flag_id {flag_id}")
        return _flag_from_row(row)

    def _commit_write(self) -> None:
        if self._commit_writes:
            self._conn.commit()


class SqlitePipelineRunRepository:
    """PipelineRunRepository over `pipeline_runs` (mutable processing state, §7)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        conn.row_factory = sqlite3.Row
        self._conn = conn
        self._conn.execute(_CREATE_PIPELINE_RUNS)

    def save(self, run: PipelineRun) -> None:
        self._conn.execute(
            _UPSERT_RUN,
            (
                run.run_id,
                run.flow.value,
                run.client_rfc.value,
                run.period,
                run.status.value,
                run.failed_stage,
                _per_stage_to_json(run.per_stage),
                _iso(run.started_at),
                _iso(run.finished_at) if run.finished_at is not None else None,
            ),
        )
        self._conn.commit()

    def get(self, run_id: str) -> PipelineRun | None:
        row = self._conn.execute(_SELECT_RUN, (run_id,)).fetchone()
        return None if row is None else _run_from_row(row)

    def for_client(self, client_rfc: Rfc) -> tuple[PipelineRun, ...]:
        rows = self._conn.execute(_SELECT_RUNS_FOR_CLIENT, (client_rfc.value,)).fetchall()
        return tuple(_run_from_row(row) for row in rows)
