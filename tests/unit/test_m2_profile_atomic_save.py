"""M2-E: the profile save is one unit — the profile and its obligations land together.

`save()` writes the parent row and then one child row per obligation. That is the first
self-committing multi-statement save in the persistence package, so this file proves the
outcome the application depends on: either the whole fact is stored or none of it is.
The failure is injected by breaking the child insert's statement constant, which means
the parent row is written *before* the failure — an empty database can only be explained
by a rollback, never by nothing having been attempted.

Two transaction owners meet here and the difference is behavioural, not stylistic:
``commit=True`` (the default) means the repository owns the unit and must roll its own
failure back, while ``commit=False`` is how M2.8 wires stores inside
``SqliteUnitOfWork`` — there the unit owns commit/rollback and the repository must not
end it early (see test_m2_sqlite_transactions.py).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sat_descarga_masiva.domain.model.contributor import (
    ContributorProfile,
    ContributorProfileRecord,
    ObligacionFiscal,
    PersonaTipo,
    RegimenFiscal,
    SituacionFiscal,
)
from sat_descarga_masiva.domain.model.value_objects import Rfc
from sat_descarga_masiva.infrastructure.persistence import sqlite as sqlite_module
from sat_descarga_masiva.infrastructure.persistence.sqlite import (
    SqliteContributorProfileRepository,
    init_schema,
)
from sat_descarga_masiva.infrastructure.persistence.transactions import SqliteUnitOfWork

RFC = Rfc("AAA010101AAA")
T0 = datetime(2026, 1, 1, tzinfo=UTC)
T1 = datetime(2026, 1, 2, tzinfo=UTC)
HASH = "a" * 64
OBLIGACIONES = (
    ObligacionFiscal("3", "Declarar anualmente el ISR"),
    ObligacionFiscal("9", "Declarar mensualmente el IVA."),
)


def _record() -> ContributorProfileRecord:
    return ContributorProfileRecord(
        profile=ContributorProfile(
            rfc=RFC,
            nombre="ACME SA DE CV",
            persona_tipo=PersonaTipo.MORAL,
            regimen_fiscal=RegimenFiscal("601", "General de Ley Personas Morales"),
            situacion_fiscal=SituacionFiscal.ACTIVO,
            obligaciones=OBLIGACIONES,
            codigo_postal="97000",
        ),
        csf_hash=HASH,
        csf_obtained_at=T0,
        profile_version=1,
        recorded_at=T1,
    )


def _rows(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


class Database:
    """A writing connection plus a peer connection that only ever observes commits."""

    def __init__(self, path: Path, *, commit: bool = True) -> None:
        self.conn = sqlite3.connect(path)
        init_schema(self.conn)
        self.profiles = SqliteContributorProfileRepository(self.conn, commit=commit)
        self.peer = sqlite3.connect(path)


def test_the_whole_fact_lands_together_when_nothing_fails(tmp_path: Path) -> None:
    """The control: this is the save the atomicity test breaks, and it stores both tables."""
    db = Database(tmp_path / "profiles.sqlite3")

    db.profiles.save(_record())

    assert _rows(db.conn, "contributor_profiles") == 1
    assert _rows(db.conn, "profile_obligaciones") == len(OBLIGACIONES)
    assert _rows(db.peer, "contributor_profiles") == 1
    assert _rows(db.peer, "profile_obligaciones") == len(OBLIGACIONES)


def test_a_failed_obligation_write_leaves_no_profile_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The parent row is written first, so `{COUNT} == 0` can only mean it was rolled back."""
    db = Database(tmp_path / "profiles.sqlite3")
    # `? || NULL` folds one *bound* value to NULL, so the child insert still binds every
    # parameter and fails on the column's NOT NULL constraint — after the parent row.
    monkeypatch.setattr(
        sqlite_module,
        "_INSERT_OBLIGACION",
        "INSERT INTO profile_obligaciones "
        "(client_rfc, profile_version, ordinal, code, description) VALUES (?, ?, ?, ?, ? || NULL)",
    )

    with pytest.raises(sqlite3.IntegrityError, match="NOT NULL"):
        db.profiles.save(_record())

    assert _rows(db.conn, "contributor_profiles") == 0
    assert _rows(db.conn, "profile_obligaciones") == 0
    assert _rows(db.peer, "contributor_profiles") == 0
    assert _rows(db.peer, "profile_obligaciones") == 0

    monkeypatch.undo()
    db.profiles.save(_record())  # the rollback left the store usable
    assert _rows(db.peer, "contributor_profiles") == 1


def test_with_commit_false_the_peer_sees_nothing_until_the_unit_commits(tmp_path: Path) -> None:
    """Inside M2.8's unit the repository must not commit: the unit owns the boundary."""
    db = Database(tmp_path / "profiles.sqlite3", commit=False)
    unit = SqliteUnitOfWork(db.conn)

    with unit.transaction():
        db.profiles.save(_record())
        assert _rows(db.conn, "contributor_profiles") == 1
        assert _rows(db.conn, "profile_obligaciones") == len(OBLIGACIONES)
        assert _rows(db.peer, "contributor_profiles") == 0
        assert _rows(db.peer, "profile_obligaciones") == 0

    assert _rows(db.peer, "contributor_profiles") == 1
    assert _rows(db.peer, "profile_obligaciones") == len(OBLIGACIONES)


def test_a_failed_unit_rolls_back_the_profile_and_its_obligations(tmp_path: Path) -> None:
    """`commit=False` means the repository must not roll back a unit it does not own."""
    db = Database(tmp_path / "profiles.sqlite3", commit=False)
    unit = SqliteUnitOfWork(db.conn)

    with pytest.raises(RuntimeError, match="boom"), unit.transaction():
        db.profiles.save(_record())
        assert _rows(db.conn, "contributor_profiles") == 1  # it really was written
        raise RuntimeError("boom")

    assert _rows(db.conn, "contributor_profiles") == 0
    assert _rows(db.conn, "profile_obligaciones") == 0
    assert _rows(db.peer, "contributor_profiles") == 0
    assert _rows(db.peer, "profile_obligaciones") == 0
