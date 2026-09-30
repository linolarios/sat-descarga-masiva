"""M2.8: SqliteUnitOfWork — the per-document transaction, proved behaviourally (§4/§11 M2).

The watchpoint this file answers: a unit of work is not "a connection in a special
state" but three observable outcomes — a commit a *second* connection can see, a
rollback that removes the document **and** its review facts, and one document's
failure that leaves another document's committed rows untouched. Every assertion
below is one of those outcomes; none of them inspects the connection to prove the
mechanism.

The stores are built with ``commit=False``, which is exactly how M2.8 wires them: the
unit of work owns COMMIT/ROLLBACK, so a repository must not end the unit early. That
suppression is visible here too — the peer cannot see a write until the unit commits.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sat_descarga_masiva.domain.model.documents import DocumentRecord
from sat_descarga_masiva.domain.model.perspective import Perspective
from sat_descarga_masiva.domain.model.review import (
    ReviewFlag,
    ReviewFlagType,
    ReviewSubjectKind,
)
from sat_descarga_masiva.domain.model.value_objects import Rfc, Uuid
from sat_descarga_masiva.infrastructure.persistence.sqlite import (
    SqliteDocumentRepository,
    SqliteReviewFlagStore,
    init_schema,
)
from sat_descarga_masiva.infrastructure.persistence.transactions import SqliteUnitOfWork

RFC = Rfc("AAA010101AAA")
ISSUER = Rfc("BBB010101BBB")
UUID1 = "123E4567-E89B-12D3-A456-426614174000"
UUID2 = "4E80345D-917F-40BB-A98F-4A73939353C5"
NOW = datetime(2026, 1, 1, tzinfo=UTC)
HASH = "a" * 64


class Database:
    """The app's connection and stores, plus a peer connection that only ever observes."""

    def __init__(self, path: Path) -> None:
        self.conn = sqlite3.connect(path)
        init_schema(self.conn)
        self.documents = SqliteDocumentRepository(self.conn, commit=False)
        self.flags = SqliteReviewFlagStore(self.conn, commit=False)
        self.uow = SqliteUnitOfWork(self.conn)
        self.peer = sqlite3.connect(path)

    def write_a_document(self, uuid: str = UUID1) -> None:
        self.documents.save(_record(uuid))

    def write_a_flag(self, uuid: str = UUID1) -> None:
        self.flags.open_flag(
            subject_kind=ReviewSubjectKind.DOCUMENT,
            subject_id=uuid,
            flag=_flag(),
            occurred_at=NOW,
        )


def _record(uuid: str = UUID1) -> DocumentRecord:
    return DocumentRecord(
        uuid=Uuid(uuid),
        contributor_rfc=RFC,
        perspective=Perspective.EMITIDO,
        tipo="I",
        version="4.0",
        source_hash=HASH,
        emisor_rfc=ISSUER,
        receptor_rfc=RFC,
        moneda="MXN",
        first_seen_at=NOW,
        last_seen_at=NOW,
    )


def _flag() -> ReviewFlag:
    return ReviewFlag(ReviewFlagType.CFDI_SIGNATURE, "cfdi signature absent")


def _documents(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT uuid FROM documents ORDER BY uuid").fetchall()
    return [row[0] for row in rows]


def _flag_rows(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM review_flags").fetchone()[0])


def test_the_peer_sees_nothing_until_the_unit_commits(tmp_path: Path) -> None:
    db = Database(tmp_path / "m28.sqlite3")

    with db.uow.transaction():
        db.write_a_document()
        db.write_a_flag()
        # The write is real (this connection can read it back) yet uncommitted: no
        # repository ended the unit early, and nothing has escaped to the peer.
        assert _documents(db.conn) == [UUID1]
        assert _flag_rows(db.conn) == 1
        assert _documents(db.peer) == []
        assert _flag_rows(db.peer) == 0

    assert _documents(db.peer) == [UUID1]
    assert _flag_rows(db.peer) == 1


def test_a_rollback_removes_the_document_and_its_review_facts(tmp_path: Path) -> None:
    db = Database(tmp_path / "m28.sqlite3")

    with pytest.raises(RuntimeError, match="boom"), db.uow.transaction():
        db.write_a_document()
        db.write_a_flag()
        assert _documents(db.conn) == [UUID1]  # the rows existed before the failure
        raise RuntimeError("boom")

    assert _documents(db.conn) == []
    assert _flag_rows(db.conn) == 0
    assert _documents(db.peer) == []


def test_a_failed_unit_leaves_an_earlier_committed_unit_intact(tmp_path: Path) -> None:
    db = Database(tmp_path / "m28.sqlite3")

    with db.uow.transaction():
        db.write_a_document(UUID1)

    with pytest.raises(RuntimeError, match="boom"), db.uow.transaction():
        db.write_a_document(UUID2)
        raise RuntimeError("boom")

    assert _documents(db.peer) == [UUID1]


def _open_a_nested_unit(db: Database) -> None:
    """The second unit the nested-transaction test expects to be refused."""
    with db.uow.transaction():
        db.write_a_document(UUID2)


def test_a_nested_transaction_is_rejected_instead_of_flattened(tmp_path: Path) -> None:
    """M2.8 opens one unit per document; silently flattening a second would hide a bug."""
    db = Database(tmp_path / "m28.sqlite3")

    with db.uow.transaction():
        with pytest.raises(sqlite3.OperationalError, match="within a transaction"):
            _open_a_nested_unit(db)
        db.write_a_document(UUID1)

    assert _documents(db.peer) == [UUID1]  # the outer unit still committed on its own


def test_the_unit_of_work_leaves_no_transaction_open(tmp_path: Path) -> None:
    """Behavioural, not a state inspection: a fresh explicit BEGIN must be accepted."""
    db = Database(tmp_path / "m28.sqlite3")

    with db.uow.transaction():
        db.write_a_document()

    db.conn.execute("BEGIN IMMEDIATE")
    db.conn.execute("ROLLBACK")


def test_a_second_unit_can_start_after_a_rollback(tmp_path: Path) -> None:
    db = Database(tmp_path / "m28.sqlite3")

    with pytest.raises(RuntimeError, match="boom"), db.uow.transaction():
        db.write_a_document(UUID1)
        raise RuntimeError("boom")

    with db.uow.transaction():
        db.write_a_document(UUID2)
        db.write_a_flag(UUID2)

    assert _documents(db.peer) == [UUID2]
    assert _flag_rows(db.peer) == 1
