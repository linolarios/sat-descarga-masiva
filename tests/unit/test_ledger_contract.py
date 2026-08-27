"""M1: ledger repository contract — in-memory and SQLite adapters both pass (§12)."""

import sqlite3
from datetime import UTC, datetime
from typing import cast

import pytest

from sat_descarga_masiva.application.ports.persistence import (
    DownloadCursorRepository,
    DownloadJobRepository,
    SourceIdentityIndex,
)
from sat_descarga_masiva.domain.enums.catalog import Direction, ServiceType
from sat_descarga_masiva.domain.errors import SourceHashConflict
from sat_descarga_masiva.domain.model.cursor import DownloadCursor
from sat_descarga_masiva.domain.model.ledger import DownloadJob, JobStatus
from sat_descarga_masiva.domain.model.source import SourceIdentity, sha256_hex
from sat_descarga_masiva.domain.model.value_objects import RequestId, Rfc
from sat_descarga_masiva.infrastructure.persistence.memory import (
    InMemoryDownloadCursorRepository,
    InMemoryDownloadJobRepository,
    InMemorySourceIdentityIndex,
)
from sat_descarga_masiva.infrastructure.persistence.sqlite import (
    SqliteDownloadCursorRepository,
    SqliteDownloadJobRepository,
    SqliteSourceIdentityIndex,
)

RFC = Rfc("AAA010101AAA")
RID = RequestId("4e80345d-917f-40bb-a98f-4a73939353c5")
START = datetime(2026, 1, 1, tzinfo=UTC)
END = datetime(2026, 1, 31, tzinfo=UTC)


def _job() -> DownloadJob:
    return DownloadJob(
        job_id="job-1",
        client_rfc=RFC,
        service=ServiceType.CFDI,
        direction=Direction.RECIBIDOS,
        request_id=RID,
        query_start=START,
        query_end=END,
        policy_version=1,
        status=JobStatus.COMPLETED,
        created_at=START,
        completed_at=END,
    )


def _cursor() -> DownloadCursor:
    return DownloadCursor(
        client_rfc=RFC,
        service=ServiceType.CFDI,
        direction=Direction.RECIBIDOS,
        query_start=START,
        query_end=END,
        last_successful_boundary=datetime(2026, 1, 21, tzinfo=UTC),
        last_request_id=RequestId("4e80345d-917f-40bb-a98c-4a73939353c5"),
        last_completed_at=datetime(2026, 1, 21, 10, tzinfo=UTC),
    )


def _identity(sha: str) -> SourceIdentity:
    return SourceIdentity("uuid-1", sha)


def _memory_job() -> DownloadJobRepository:
    return InMemoryDownloadJobRepository()


def _sqlite_job() -> DownloadJobRepository:
    return SqliteDownloadJobRepository(sqlite3.connect(":memory:"))


def _memory_cursor() -> DownloadCursorRepository:
    return InMemoryDownloadCursorRepository()


def _sqlite_cursor() -> DownloadCursorRepository:
    return SqliteDownloadCursorRepository(sqlite3.connect(":memory:"))


def _memory_source() -> SourceIdentityIndex:
    return InMemorySourceIdentityIndex()


def _sqlite_source() -> SourceIdentityIndex:
    return SqliteSourceIdentityIndex(sqlite3.connect(":memory:"))


JOB_FACTORIES = [
    pytest.param(_memory_job, id="in-memory"),
    pytest.param(_sqlite_job, id="sqlite"),
]
CURSOR_FACTORIES = [
    pytest.param(_memory_cursor, id="in-memory"),
    pytest.param(_sqlite_cursor, id="sqlite"),
]
SOURCE_FACTORIES = [
    pytest.param(_memory_source, id="in-memory"),
    pytest.param(_sqlite_source, id="sqlite"),
]


@pytest.fixture(params=JOB_FACTORIES)
def job_repo(request: pytest.FixtureRequest) -> DownloadJobRepository:
    return cast(DownloadJobRepository, request.param())


@pytest.fixture(params=CURSOR_FACTORIES)
def cursor_repo(request: pytest.FixtureRequest) -> DownloadCursorRepository:
    return cast(DownloadCursorRepository, request.param())


@pytest.fixture(params=SOURCE_FACTORIES)
def source_index(request: pytest.FixtureRequest) -> SourceIdentityIndex:
    return cast(SourceIdentityIndex, request.param())


# --- DownloadJobRepository -----------------------------------------------------


def test_job_repo_round_trips(job_repo: DownloadJobRepository) -> None:
    job_repo.save(_job())
    assert job_repo.get("job-1") == _job()


def test_job_repo_unknown_returns_none(job_repo: DownloadJobRepository) -> None:
    assert job_repo.get("nope") is None


def test_job_repo_enum_values_round_trip(job_repo: DownloadJobRepository) -> None:
    job_repo.save(_job())
    got = job_repo.get("job-1")
    assert got is not None
    assert got.service is ServiceType.CFDI
    assert got.direction is Direction.RECIBIDOS
    assert got.status is JobStatus.COMPLETED


# --- DownloadCursorRepository --------------------------------------------------


def test_cursor_repo_round_trips(cursor_repo: DownloadCursorRepository) -> None:
    cursor_repo.save(_cursor())
    assert cursor_repo.get(RFC, ServiceType.CFDI, Direction.RECIBIDOS) == _cursor()


def test_cursor_repo_unknown_returns_none(cursor_repo: DownloadCursorRepository) -> None:
    assert cursor_repo.get(RFC, ServiceType.CFDI, Direction.RECIBIDOS) is None


def test_cursor_repo_save_is_upsert(cursor_repo: DownloadCursorRepository) -> None:
    cursor_repo.save(_cursor())
    cursor_repo.save(DownloadCursor(RFC, ServiceType.CFDI, Direction.RECIBIDOS, START, END))
    got = cursor_repo.get(RFC, ServiceType.CFDI, Direction.RECIBIDOS)
    assert got is not None
    assert got.last_successful_boundary is None


def test_cursor_repo_nullable_fields(cursor_repo: DownloadCursorRepository) -> None:
    cursor_repo.save(DownloadCursor(RFC, ServiceType.CFDI, Direction.RECIBIDOS, START, END))
    got = cursor_repo.get(RFC, ServiceType.CFDI, Direction.RECIBIDOS)
    assert got is not None
    assert got.last_successful_boundary is None
    assert got.last_request_id is None
    assert got.last_completed_at is None


# --- SourceIdentityIndex: AGENT.md §6 dedup / conflict rule -------------------


def test_source_index_new_uuid_inserts(source_index: SourceIdentityIndex) -> None:
    source_index.record(_identity(sha256_hex(b"a")))
    assert source_index.get("uuid-1") == _identity(sha256_hex(b"a"))


def test_source_index_duplicate_is_idempotent_noop(source_index: SourceIdentityIndex) -> None:
    source_index.record(_identity(sha256_hex(b"a")))
    source_index.record(_identity(sha256_hex(b"a")))  # must not raise, must not change
    assert source_index.get("uuid-1") == _identity(sha256_hex(b"a"))


def test_source_index_conflict_raises_and_preserves_original(
    source_index: SourceIdentityIndex,
) -> None:
    source_index.record(_identity(sha256_hex(b"a")))
    with pytest.raises(SourceHashConflict):
        source_index.record(_identity(sha256_hex(b"b")))
    # The original sha256 is still stored (never overwritten).
    assert source_index.get("uuid-1") == _identity(sha256_hex(b"a"))


def test_source_index_unknown_returns_none(source_index: SourceIdentityIndex) -> None:
    assert source_index.get("uuid-x") is None
