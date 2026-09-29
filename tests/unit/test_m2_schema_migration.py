"""M2.7: SQLite schema creation + migration ladder (AGENT.md §11, §4).

`init_schema` is the authoritative entry point introduced with M2 (§11 M2 adds
tables). It must be idempotent, must never drop/recreate or lose data, and must
advance `PRAGMA user_version`; pre-M2 databases carry `user_version = 0` (M1
created tables per repository without ever setting a version).
"""

import sqlite3
from datetime import UTC, datetime

from sat_descarga_masiva.domain.enums.catalog import Direction, ServiceType
from sat_descarga_masiva.domain.model.ledger import DownloadJob, JobStatus
from sat_descarga_masiva.domain.model.pipeline_run import PipelineFlow, PipelineRun
from sat_descarga_masiva.domain.model.source import SourceIdentity, sha256_hex
from sat_descarga_masiva.domain.model.value_objects import RequestId, Rfc
from sat_descarga_masiva.infrastructure.persistence.sqlite import (
    SCHEMA_VERSION,
    SqliteDownloadJobRepository,
    SqlitePipelineRunRepository,
    SqliteSourceIdentityIndex,
    init_schema,
)

RFC = Rfc("AAA010101AAA")
RID = RequestId("4e80345d-917f-40bb-a98f-4a73939353c5")
UID = "4e80345d-917f-40bb-a98f-4a73939353c5"
T0 = datetime(2026, 1, 1, tzinfo=UTC)
T1 = datetime(2026, 1, 31, tzinfo=UTC)

M1_TABLES = {"download_jobs", "download_cursors", "source_records"}
M2_TABLES = {
    "documents",
    "fiscal_events",
    "contributor_profiles",
    "csf_artifacts",
    "review_flags",
    "pipeline_runs",
}

_PRE_M2_DOWNLOAD_JOBS = """
CREATE TABLE download_jobs (
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

_PRE_M2_JOB_ROW = """
INSERT INTO download_jobs (
    job_id, client_rfc, service, direction, request_id, query_start, query_end,
    policy_version, status, created_at, completed_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def _conn() -> sqlite3.Connection:
    return sqlite3.connect(":memory:")


def _job(**overrides: object) -> DownloadJob:
    base: dict[str, object] = dict(
        job_id="job-1",
        client_rfc=RFC,
        service=ServiceType.CFDI,
        direction=Direction.RECIBIDOS,
        request_id=RID,
        query_start=T0,
        query_end=T1,
        policy_version=1,
        status=JobStatus.COMPLETED,
        created_at=T0,
        completed_at=T1,
    )
    base.update(overrides)
    return DownloadJob(**base)  # type: ignore[arg-type]


def _run() -> PipelineRun:
    return PipelineRun(
        run_id="run-1",
        flow=PipelineFlow.DOWNLOAD,
        client_rfc=RFC,
        status=JobStatus.COMPLETED,
        started_at=T0,
        finished_at=T1,
    )


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {row[0] for row in rows}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _user_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def test_init_schema_sets_the_schema_version() -> None:
    conn = _conn()
    assert _user_version(conn) == 0  # M1 never versioned the schema
    init_schema(conn)
    assert _user_version(conn) == SCHEMA_VERSION


def test_init_schema_creates_all_m1_and_m2_tables() -> None:
    conn = _conn()
    init_schema(conn)
    names = _table_names(conn)
    assert names >= M1_TABLES
    assert names >= M2_TABLES


def test_init_schema_is_idempotent() -> None:
    conn = _conn()
    init_schema(conn)
    before = _table_names(conn)
    init_schema(conn)
    assert _table_names(conn) == before
    assert _user_version(conn) == SCHEMA_VERSION


def test_documents_is_a_projection_without_a_status_column() -> None:
    conn = _conn()
    init_schema(conn)
    columns = _columns(conn, "documents")
    assert "status" not in columns  # §4: current projection, not a mutable status
    assert {"uuid", "source_hash", "first_seen_at", "last_seen_at"} <= columns


def test_no_fiscal_table_stores_real_numbers() -> None:
    conn = _conn()
    init_schema(conn)
    for table in ("documents", "fiscal_events", "contributor_profiles"):
        types = {row[2].upper() for row in conn.execute(f"PRAGMA table_info({table})")}
        assert "REAL" not in types  # no float in fiscal storage (§4 MoneyPolicy)


def _pre_m2_database() -> sqlite3.Connection:
    """A pre-M2 database: M1 wrote its own table, never a `user_version`."""
    conn = _conn()
    conn.execute(_PRE_M2_DOWNLOAD_JOBS)
    conn.execute(
        _PRE_M2_JOB_ROW,
        (
            "job-1",
            RFC.value,
            ServiceType.CFDI.value,
            Direction.RECIBIDOS.value,
            RID.value,
            T0.isoformat(),
            T1.isoformat(),
            1,
            JobStatus.COMPLETED.value,
            T0.isoformat(),
            T1.isoformat(),
        ),
    )
    conn.commit()
    return conn


def test_migration_preserves_existing_m1_data_and_adds_the_run_link() -> None:
    conn = _pre_m2_database()
    assert _user_version(conn) == 0
    assert "pipeline_run_id" not in _columns(conn, "download_jobs")

    init_schema(conn)

    assert _user_version(conn) == SCHEMA_VERSION
    assert "pipeline_run_id" in _columns(conn, "download_jobs")
    preserved = SqliteDownloadJobRepository(conn).get("job-1")
    assert preserved is not None
    assert preserved.pipeline_run_id is None
    assert preserved == _job()  # all pre-M2 data intact


def test_pre_m2_download_jobs_table_gains_the_column_on_repository_use() -> None:
    conn = _conn()
    conn.execute(_PRE_M2_DOWNLOAD_JOBS)
    repo = SqliteDownloadJobRepository(conn)  # guarded ALTER for a legacy database
    repo.save(_job(pipeline_run_id="run-1"))
    got = repo.get("job-1")
    assert got is not None
    assert got.pipeline_run_id == "run-1"


def test_init_schema_never_drops_unrelated_tables_or_rows() -> None:
    conn = _conn()
    conn.execute("CREATE TABLE future_table (id INTEGER PRIMARY KEY, note TEXT NOT NULL)")
    conn.execute("INSERT INTO future_table (note) VALUES ('keep me')")
    init_schema(conn)
    rows = conn.execute("SELECT note FROM future_table").fetchall()
    assert [row[0] for row in rows] == ["keep me"]


def test_init_schema_is_idempotent_on_a_v0_database_with_m1_tables() -> None:
    conn = _pre_m2_database()
    init_schema(conn)
    init_schema(conn)
    assert _user_version(conn) == SCHEMA_VERSION
    assert SqliteDownloadJobRepository(conn).get("job-1") == _job()


def test_init_schema_never_lowers_a_newer_schema_version() -> None:
    conn = _conn()
    conn.execute("PRAGMA user_version = 99")  # written by a newer build
    init_schema(conn)
    assert _user_version(conn) == 99


def test_correlation_chain_pipeline_run_to_download_job_to_source_identity() -> None:
    conn = _conn()
    init_schema(conn)
    SqlitePipelineRunRepository(conn).save(_run())
    SqliteDownloadJobRepository(conn).save(_job(pipeline_run_id="run-1"))
    SqliteSourceIdentityIndex(conn).record(SourceIdentity(UID, sha256_hex(b"xml")))

    job = SqliteDownloadJobRepository(conn).get("job-1")
    assert job is not None
    assert job.pipeline_run_id == "run-1"
    # The package -> uuid link is NOT a DB relation: source artifacts are the
    # authoritative evidence (§4); `source_records` only maps uuid -> sha256.
    assert _columns(conn, "source_records") == {"uuid", "sha256"}
    assert _columns(conn, "download_jobs").isdisjoint({"uuid", "sha256"})
