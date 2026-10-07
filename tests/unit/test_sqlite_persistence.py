"""SQLite-specific persistence details: row_factory, stored enum values, datetime."""

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from sat_descarga_masiva.domain.enums.catalog import Direction, ServiceType
from sat_descarga_masiva.domain.errors import SourceHashConflict
from sat_descarga_masiva.domain.model.cursor import DownloadCursor
from sat_descarga_masiva.domain.model.ledger import DownloadJob, JobStatus
from sat_descarga_masiva.domain.model.source import SourceIdentity, sha256_hex
from sat_descarga_masiva.domain.model.value_objects import RequestId, Rfc
from sat_descarga_masiva.infrastructure.persistence.sqlite import (
    SqliteDownloadCursorRepository,
    SqliteDownloadJobRepository,
    SqliteSourceIdentityIndex,
)

RFC = Rfc("AAA010101AAA")
RID = RequestId("4e80345d-917f-40bb-a98f-4a73939353c5")
START = datetime(2026, 1, 1, tzinfo=UTC)
END = datetime(2026, 1, 31, tzinfo=UTC)


def _conn() -> sqlite3.Connection:
    return sqlite3.connect(":memory:")


def _job(**overrides: object) -> DownloadJob:
    base = dict(
        job_id="job-1",
        client_rfc=RFC,
        service=ServiceType.CFDI,
        direction=Direction.RECIBIDOS,
        request_id=RID,
        query_start=START,
        query_end=END,
        policy_version=1,
        status=JobStatus.RUNNING,
        created_at=START,
        completed_at=None,
    )
    base.update(overrides)
    return DownloadJob(**base)  # type: ignore[arg-type]


def _cursor(**overrides: object) -> DownloadCursor:
    base = dict(
        client_rfc=RFC,
        service=ServiceType.CFDI,
        direction=Direction.RECIBIDOS,
        query_start=START,
        query_end=END,
        last_successful_boundary=START,
        last_request_id=RID,
        last_completed_at=START,
    )
    base.update(overrides)
    return DownloadCursor(**base)  # type: ignore[arg-type]


def _rowid(conn: sqlite3.Connection, table: str, where: str, params: tuple[object, ...]) -> int:
    row = conn.execute(f"SELECT rowid FROM {table} WHERE {where}", params).fetchone()
    assert row is not None
    return int(row["rowid"])


def _count(conn: sqlite3.Connection, table: str) -> int:
    row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
    assert row is not None
    return int(row["n"])


def test_connection_uses_row_factory() -> None:
    conn = _conn()
    SqliteDownloadJobRepository(conn)
    assert conn.row_factory is sqlite3.Row


def test_enum_values_stored_as_string_value_not_name() -> None:
    conn = _conn()
    SqliteDownloadJobRepository(conn).save(_job(status=JobStatus.COMPLETED))
    row = conn.execute(
        "SELECT service, direction, status FROM download_jobs WHERE job_id = ?", ("job-1",)
    ).fetchone()
    assert row is not None
    assert row["service"] == "cfdi"  # ServiceType.CFDI.value, not the enum name
    assert row["direction"] == "recibidos"  # Direction.RECIBIDOS.value
    assert row["status"] == "completed"  # JobStatus.COMPLETED.value


def test_datetime_round_trip_is_utc_aware() -> None:
    conn = _conn()
    repo = SqliteDownloadJobRepository(conn)
    precise = datetime(2026, 1, 1, 12, 30, 45, 123456, tzinfo=UTC)
    repo.save(_job(query_start=precise, created_at=precise, status=JobStatus.RUNNING))
    got = repo.get("job-1")
    assert got is not None
    assert got == _job(query_start=precise, created_at=precise, status=JobStatus.RUNNING)
    assert got.query_start.tzinfo is not None
    assert got.query_start.utcoffset() == timedelta(0)


def test_naive_input_is_normalized_to_utc_on_write() -> None:
    conn = _conn()
    repo = SqliteDownloadJobRepository(conn)
    naive = datetime(2026, 1, 1, 12, 30)
    repo.save(_job(query_start=naive, created_at=naive))
    got = repo.get("job-1")
    assert got is not None
    assert got.query_start == datetime(2026, 1, 1, 12, 30, tzinfo=UTC)
    assert got.query_start.utcoffset() == timedelta(0)


def test_source_conflict_keeps_original_row_in_db() -> None:
    conn = _conn()
    index = SqliteSourceIdentityIndex(conn)
    index.record(SourceIdentity("u1", sha256_hex(b"a")))
    with pytest.raises(SourceHashConflict):
        index.record(SourceIdentity("u1", sha256_hex(b"b")))
    row = conn.execute("SELECT uuid, sha256 FROM source_records WHERE uuid = ?", ("u1",)).fetchone()
    assert row is not None
    assert row["sha256"] == sha256_hex(b"a")  # original preserved at the DB level


# A re-saved job updates its row in place -- never delete+reinsert (AGENT.md 11 M3).
def test_job_re_save_updates_in_place_without_delete_reinsert() -> None:
    conn = _conn()
    repo = SqliteDownloadJobRepository(conn)
    repo.save(_job(job_id="job-1", status=JobStatus.RUNNING))
    repo.save(_job(job_id="job-2", status=JobStatus.RUNNING))  # keep job-1 off the rowid frontier

    before = _rowid(conn, "download_jobs", "job_id = ?", ("job-1",))
    repo.save(_job(job_id="job-1", status=JobStatus.COMPLETED, completed_at=END))

    # D1.1 -- the mutable operational state moved
    got = repo.get("job-1")
    assert got is not None
    assert got.status is JobStatus.COMPLETED
    assert got.completed_at == END
    # D1.2 -- the same physical row, not a fresh one
    assert _rowid(conn, "download_jobs", "job_id = ?", ("job-1",)) == before
    # D1.3 -- no duplicate key row
    assert _count(conn, "download_jobs") == 2
    # D1.4 -- the conflict key survives the update
    key = conn.execute("SELECT job_id FROM download_jobs WHERE rowid = ?", (before,)).fetchone()
    assert key is not None
    assert key["job_id"] == "job-1"


# A re-saved cursor advances its row in place -- never delete+reinsert.
def test_cursor_re_save_updates_in_place_without_delete_reinsert() -> None:
    conn = _conn()
    repo = SqliteDownloadCursorRepository(conn)
    repo.save(_cursor(direction=Direction.RECIBIDOS))
    repo.save(_cursor(direction=Direction.EMITIDOS))

    where = "client_rfc = ? AND service = ? AND direction = ?"
    target = (RFC.value, ServiceType.CFDI.value, Direction.RECIBIDOS.value)
    before = _rowid(conn, "download_cursors", where, target)
    repo.save(_cursor(direction=Direction.RECIBIDOS, last_successful_boundary=END))

    # D1.1 -- the mutable resume state moved
    got = repo.get(RFC, ServiceType.CFDI, Direction.RECIBIDOS)
    assert got is not None
    assert got.last_successful_boundary == END
    # D1.2 -- the same physical row, not a fresh one
    assert _rowid(conn, "download_cursors", where, target) == before
    # D1.3 -- no duplicate key row
    assert _count(conn, "download_cursors") == 2
    # D1.4 -- the conflict key survives the update
    key = conn.execute(
        "SELECT client_rfc, service, direction FROM download_cursors WHERE rowid = ?", (before,)
    ).fetchone()
    assert key is not None
    assert (key["client_rfc"], key["service"], key["direction"]) == target
