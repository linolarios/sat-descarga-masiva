"""SQLite-specific persistence details: row_factory, stored enum values, datetime."""

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from sat_descarga_masiva.domain.enums.catalog import Direction, ServiceType
from sat_descarga_masiva.domain.errors import SourceHashConflict
from sat_descarga_masiva.domain.model.ledger import DownloadJob, JobStatus
from sat_descarga_masiva.domain.model.source import SourceIdentity, sha256_hex
from sat_descarga_masiva.domain.model.value_objects import RequestId, Rfc
from sat_descarga_masiva.infrastructure.persistence.sqlite import (
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
