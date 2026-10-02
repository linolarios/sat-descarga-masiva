"""M3 step 2: the accounting tables — facts are never rewritten (§8, §11).

M3 adds four tables and all four are facts: an entry's posting state is assigned once
(§8:166), a leg is never edited, a metadata observation is historical (§6), and the
posting snapshot is the evidence recorded at posting time (§11 M3). The schema says so
itself — every one of them refuses UPDATE and DELETE — and the ladder only adds, so a
v3 database keeps every row it had. Schema v5 adds one column on top of those tables:
`journal_entries.assumptions`, the §8:171 evidence a rule presumed — `[]` when it
presumed nothing.
"""

import sqlite3
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from sat_descarga_masiva.contabilidad.journal import (
    AccountingAssumption,
    JournalLine,
    LineSide,
    PostingState,
    ProposedJournalEntry,
)
from sat_descarga_masiva.contabilidad.roles import AccountRole
from sat_descarga_masiva.domain.model.value_objects import Rfc, Uuid
from sat_descarga_masiva.domain.policy.money import NormalizedAmount
from sat_descarga_masiva.infrastructure.persistence.sqlite import SCHEMA_VERSION, init_schema

M3_TABLES = ("journal_entries", "journal_lines", "metadata_snapshots", "posting_snapshot")

RFC_TEXT = "AAA010101AAA"
OTHER_RFC_TEXT = "BBB010101BBB"
UUID_TEXT = "4e80345d-917f-40bb-a98f-4a73939353c5"
SUBSTITUTION_UUID = "9f1a4c8b-1f8e-4a63-9c47-5d0ee93a2e11"
HASH = "a" * 64
OTHER_HASH = "b" * 64
WHEN = datetime(2026, 1, 31, 12, 0, tzinfo=UTC).isoformat()
LATER = datetime(2026, 2, 1, 9, 30, tzinfo=UTC).isoformat()
MAPPING_VERSION = "2026.01"

_INSERT_METADATA = (
    "INSERT INTO metadata_snapshots (uuid, contributor_rfc, status, retrieved_at, source_hash)"
    " VALUES (?, ?, ?, ?, ?)"
)
_INSERT_ENTRY = (
    "INSERT INTO journal_entries (entry_key, contributor_rfc, source_uuid, source_hash, rule_id,"
    " rule_version, mapping_version, posting_state, entry_date, recorded_at)"
    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
_INSERT_LINE = (
    "INSERT INTO journal_lines (entry_key, ordinal, line_key, account_role, side, amount)"
    " VALUES (?, ?, ?, ?, ?, ?)"
)
_INSERT_SNAPSHOT = (
    "INSERT INTO posting_snapshot (entry_key, source_hash, rule_version, policy_version,"
    " mapping_version, posted_at, valuation_date, valuation_source, valuation_rate)"
    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
)

#: `journal_entries` exactly as ladder step 4 left it (schema 4): every column of the v4
#: table and none of v5, plus the two append-only guards that step also created — so the
#: upgrade under test is a *guarded* v4 table gaining a column, which is what a real v4
#: database is. Verbatim from `sqlite.py`'s step-4 DDL and `_append_only_guards`.
_PRE_V5_JOURNAL_ENTRIES = """
CREATE TABLE journal_entries (
    entry_key TEXT PRIMARY KEY,
    contributor_rfc TEXT NOT NULL,
    source_uuid TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    rule_version TEXT NOT NULL,
    mapping_version TEXT NOT NULL,
    posting_state TEXT NOT NULL,
    entry_date TEXT NOT NULL,
    recorded_at TEXT NOT NULL
)
"""
_PRE_V5_GUARDS = (
    """
CREATE TRIGGER journal_entries_no_update
BEFORE UPDATE ON journal_entries
BEGIN
    SELECT RAISE(ABORT, 'journal_entries is append-only: a fact is written once (§8)');
END
""",
    """
CREATE TRIGGER journal_entries_no_delete
BEFORE DELETE ON journal_entries
BEGIN
    SELECT RAISE(ABORT, 'journal_entries is append-only: a fact is never erased (§8)');
END
""",
)

#: One seed per table, plus the in-place edit that table must refuse. The edit is the
#: smallest write that would make the row a lie: a state rewritten (§8:166), a leg
#: silently re-amounted, an observation overwritten (§6), evidence replaced.
_CASES = {
    "journal_entries": (
        lambda conn: _seed_entry(conn, "entry-1"),
        "UPDATE journal_entries SET posting_state = 'posted'",
    ),
    "journal_lines": (
        lambda conn: (_seed_entry(conn, "entry-1"), _seed_line(conn, "entry-1")),
        "UPDATE journal_lines SET amount = '999.00'",
    ),
    "metadata_snapshots": (
        lambda conn: conn.execute(_INSERT_METADATA, (UUID_TEXT, RFC_TEXT, "Vigente", WHEN, HASH)),
        "UPDATE metadata_snapshots SET status = 'Cancelado'",
    ),
    "posting_snapshot": (
        lambda conn: _seed_snapshot(conn, "entry-1"),
        "UPDATE posting_snapshot SET mapping_version = '2026.02'",
    ),
}


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    return conn


def _seed_entry(conn: sqlite3.Connection, entry_key: str) -> None:
    conn.execute(
        _INSERT_ENTRY,
        (
            entry_key,
            RFC_TEXT,
            UUID_TEXT,
            HASH,
            "4.1",
            "1",
            MAPPING_VERSION,
            PostingState.POSTED.value,
            "2026-01-31",
            WHEN,
        ),
    )


def _seed_line(conn: sqlite3.Connection, entry_key: str) -> None:
    conn.execute(_INSERT_LINE, (entry_key, 0, "total", "clientes", "debe", "116.00"))


def _seed_snapshot(conn: sqlite3.Connection, entry_key: str) -> None:
    conn.execute(
        _INSERT_SNAPSHOT,
        (entry_key, HASH, "1", "7", MAPPING_VERSION, WHEN, None, None, None),
    )


def _v4_database() -> sqlite3.Connection:
    """A database as M3 (schema 4) left it: one entry row, no `assumptions` column."""
    conn = sqlite3.connect(":memory:")
    conn.execute(_PRE_V5_JOURNAL_ENTRIES)
    for guard in _PRE_V5_GUARDS:
        conn.execute(guard)
    _seed_entry(conn, "entry-1")
    conn.execute("PRAGMA user_version = 4")
    conn.commit()
    return conn


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _trigger_names(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}


def _columns(conn: sqlite3.Connection, table: str) -> dict[str, str]:
    return {row[1]: row[2].upper() for row in conn.execute(f"PRAGMA table_info({table})")}


def _column(conn: sqlite3.Connection, table: str, column: str) -> dict[str, object]:
    """One `PRAGMA table_info` row, so the DDL *contract* is assertable, not just the type."""
    for row in conn.execute(f"PRAGMA table_info({table})"):
        if row[1] == column:
            return {"type": row[2].upper(), "notnull": row[3], "default": row[4], "pk": row[5]}
    raise AssertionError(f"{table} has no {column} column")


def _user_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _foreign_keys(conn: sqlite3.Connection, table: str) -> list[tuple[object, ...]]:
    return list(conn.execute(f"PRAGMA foreign_key_list({table})"))


def _proposed_entry() -> ProposedJournalEntry:
    return ProposedJournalEntry(
        rule_id="4.1",
        rule_version="1",
        contributor_rfc=Rfc(RFC_TEXT),
        source_uuid=Uuid(UUID_TEXT),
        source_hash=HASH,
        entry_date=date(2026, 1, 31),
        lines=(
            JournalLine(
                account_role=AccountRole.CLIENTES,
                line_key="total",
                side=LineSide.DEBE,
                amount=NormalizedAmount(Decimal("116.00")),
            ),
        ),
    )


# --- the ladder ---------------------------------------------------------------------


def test_the_ladder_adds_the_four_m3_tables() -> None:
    conn = _conn()
    assert _user_version(conn) == SCHEMA_VERSION == 5
    assert _table_names(conn) >= set(M3_TABLES)


def test_the_ladder_adds_the_assumption_column_as_the_v5_step() -> None:
    """§8:171: what a rule presumed is stored beside the entry — one column, one step.

    A `CREATE TABLE IF NOT EXISTS` cannot add a column to the table step 4 already
    created, so v5 is the ladder's second guarded additive ALTER. What it has to
    produce is a column that cannot be absent, cannot be NULL, and cannot be read
    ambiguously: `[]` and only `[]` is the value meaning "presumed nothing".
    """
    conn = _conn()
    assert _column(conn, "journal_entries", "assumptions") == {
        "type": "TEXT",
        "notnull": 1,
        "default": "'[]'",
        "pk": 0,
    }


def test_the_ladder_is_idempotent_including_its_guards() -> None:
    conn = _conn()
    tables, triggers, version = _table_names(conn), _trigger_names(conn), _user_version(conn)
    init_schema(conn)
    assert _table_names(conn) == tables
    assert _trigger_names(conn) == triggers
    assert _user_version(conn) == version


def test_a_v3_database_upgrades_to_the_current_version_and_keeps_its_rows() -> None:
    """The ladder only adds: a v3 row survives, and the M3 facts appear beside it."""
    conn = _conn()
    conn.execute("INSERT INTO source_records (uuid, sha256) VALUES (?, ?)", (UUID_TEXT, HASH))
    for table in M3_TABLES:
        conn.execute(f"DROP TABLE {table}")
    conn.execute("PRAGMA user_version = 3")
    conn.commit()
    assert _user_version(conn) == 3
    assert not (_table_names(conn) & set(M3_TABLES))

    init_schema(conn)

    assert _user_version(conn) == SCHEMA_VERSION == 5
    assert _table_names(conn) >= set(M3_TABLES)
    row = conn.execute("SELECT sha256 FROM source_records WHERE uuid = ?", (UUID_TEXT,)).fetchone()
    assert row[0] == HASH
    init_schema(conn)  # idempotent on an already-migrated v5 database
    assert _user_version(conn) == 5


def test_a_v4_database_upgrades_and_keeps_its_rows() -> None:
    """The v5 step adds a column to a guarded v4 table: the row survives, reading `[]`.

    `[]` is not a placeholder for "unknown". No application journal writer existed
    before v5 — `journal_entries` has no repository and no port — so no stored row can
    have lost an assumption, and `[]` is the domain's own value for "presumed
    nothing". Nothing is backfilled; the guard also leaves the v4 guards alone.
    """
    conn = _v4_database()
    assert _user_version(conn) == 4
    assert "assumptions" not in _columns(conn, "journal_entries")

    init_schema(conn)

    assert _user_version(conn) == SCHEMA_VERSION == 5
    assert _column(conn, "journal_entries", "assumptions")["default"] == "'[]'"
    assert _trigger_names(conn) == {"journal_entries_no_update", "journal_entries_no_delete"}
    row = conn.execute(
        "SELECT entry_key, posting_state, assumptions FROM journal_entries"
    ).fetchone()
    assert tuple(row) == ("entry-1", PostingState.POSTED.value, "[]")
    init_schema(conn)  # idempotent on an already-migrated v5 database
    assert _user_version(conn) == 5


# --- immutability -------------------------------------------------------------------


@pytest.mark.parametrize("table", sorted(_CASES))
def test_every_m3_table_refuses_an_update_and_a_delete(table: str) -> None:
    """A fact is written once: the guards make that true, not merely intended (§8:166)."""
    seed, update = _CASES[table]
    conn = _conn()
    seed(conn)
    conn.commit()
    assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 1  # seeded, not vacuous

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(update)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(f"DELETE FROM {table}")
    assert (
        conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 1
    )  # still there, unchanged


def test_only_the_accounting_facts_are_frozen() -> None:
    """The guards are scoped to M3's facts — M2's projections still learn (§6)."""
    conn = _conn()
    assert _trigger_names(conn) == {
        f"{table}_{verb}" for table in M3_TABLES for verb in ("no_update", "no_delete")
    }
    conn.execute("INSERT INTO source_records (uuid, sha256) VALUES (?, ?)", (UUID_TEXT, HASH))
    conn.execute("UPDATE source_records SET sha256 = ? WHERE uuid = ?", (OTHER_HASH, UUID_TEXT))
    conn.commit()
    row = conn.execute("SELECT sha256 FROM source_records").fetchone()
    assert row[0] == OTHER_HASH


def test_an_entry_refuses_to_rewrite_its_assumptions() -> None:
    """§8:171/§8:166: promoting `ASSUMED_PUE` is a new record — the column is never edited."""
    conn = _conn()
    _seed_entry(conn, "entry-1")
    conn.commit()
    assert conn.execute("SELECT assumptions FROM journal_entries").fetchone()[0] == "[]"

    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE journal_entries SET assumptions = '[\"assumed_pue\"]'")

    row = conn.execute("SELECT assumptions FROM journal_entries").fetchone()
    assert row[0] == "[]"  # still what was recorded: the ABORT is not a silent no-op


# --- identity, as stored ------------------------------------------------------------


def test_the_entry_row_stores_exactly_the_fields_its_key_was_built_from() -> None:
    """§8:194's identity must be readable off the row: a key that cannot be audited is a guess."""
    entry = _proposed_entry()
    fingerprint = entry.fingerprint(MAPPING_VERSION)
    conn = _conn()
    conn.execute(
        _INSERT_ENTRY,
        (
            fingerprint.canonical(),
            fingerprint.contributor_rfc.value,
            fingerprint.source_uuid.value,
            entry.source_hash,
            fingerprint.rule_id,
            fingerprint.rule_version,
            fingerprint.mapping_version,
            entry.posting_state.value,
            entry.entry_date.isoformat(),
            WHEN,
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM journal_entries").fetchone()
    assert row["entry_key"] == fingerprint.canonical()
    assert Uuid(row["source_uuid"]) == fingerprint.source_uuid
    assert Rfc(row["contributor_rfc"]) == fingerprint.contributor_rfc
    assert (row["rule_id"], row["rule_version"]) == ("4.1", "1")
    assert row["mapping_version"] == MAPPING_VERSION
    assert row["posting_state"] == PostingState.PROPOSED.value
    assert row["entry_date"] == "2026-01-31"
    assert row["source_hash"] == HASH


def test_the_same_cfdi_under_two_contributors_is_two_entries() -> None:
    """§8:194: the same UUID in two clients' folders is two postings, not one collision."""
    conn = _conn()
    mine = _proposed_entry().fingerprint(MAPPING_VERSION)
    theirs = replace(_proposed_entry(), contributor_rfc=Rfc(OTHER_RFC_TEXT)).fingerprint(
        MAPPING_VERSION
    )
    for fingerprint in (mine, theirs):
        conn.execute(
            _INSERT_ENTRY,
            (
                fingerprint.canonical(),
                fingerprint.contributor_rfc.value,
                fingerprint.source_uuid.value,
                HASH,
                fingerprint.rule_id,
                fingerprint.rule_version,
                fingerprint.mapping_version,
                PostingState.PROPOSED.value,
                "2026-01-31",
                WHEN,
            ),
        )
    conn.commit()
    assert conn.execute("SELECT count(*) FROM journal_entries").fetchone()[0] == 2

    with pytest.raises(sqlite3.IntegrityError):  # re-proposing the same fact stores it once
        _seed_entry(conn, mine.canonical())


def test_the_entry_table_keeps_the_perspective_inside_the_rule_id() -> None:
    """§8:194: `rule_id` already encodes EMITIDO/RECIBIDO — a column would be a second truth."""
    assert "perspective" not in _columns(_conn(), "journal_entries")


def test_the_assumption_column_stores_the_domain_word_not_a_code() -> None:
    """§8:171/§4: the persisted value is `AccountingAssumption.value` — a stable word."""
    conn = _conn()
    _seed_entry(conn, "entry-1")
    conn.execute(
        "INSERT INTO journal_entries (entry_key, contributor_rfc, source_uuid, source_hash,"
        " rule_id, rule_version, mapping_version, posting_state, entry_date, recorded_at,"
        " assumptions) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "entry-2",
            RFC_TEXT,
            UUID_TEXT,
            HASH,
            "4.1",
            "1",
            MAPPING_VERSION,
            PostingState.POSTED.value,
            "2026-01-31",
            WHEN,
            '["assumed_pue"]',
        ),
    )
    conn.commit()
    rows = conn.execute(
        "SELECT entry_key, assumptions FROM journal_entries ORDER BY entry_key"
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("entry-1", "[]"),  # stored without an assumption: the rule presumed nothing
        ("entry-2", f'["{AccountingAssumption.ASSUMED_PUE.value}"]'),
    ]


def test_assumptions_is_entry_metadata_not_a_key_column() -> None:
    """§8:194: evidence beside the key, so the database derives no identity from it.

    That an assumption cannot move the *application* key is proven on the model
    (`test_an_assumption_never_redefines_line_identity`); the schema only has to state
    that the column is neither part of the key nor optional.
    """
    conn = _conn()
    column = _column(conn, "journal_entries", "assumptions")
    assert (column["pk"], column["notnull"]) == (0, 1)
    keys = [row[1] for row in conn.execute("PRAGMA table_info(journal_entries)") if row[5]]
    assert keys == ["entry_key"]


def test_a_leg_is_identified_by_its_key_and_its_order_is_unique() -> None:
    conn = _conn()
    _seed_entry(conn, "entry-1")
    _seed_line(conn, "entry-1")
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):  # the same leg of the same entry, twice
        _seed_line(conn, "entry-1")
    with pytest.raises(sqlite3.IntegrityError):  # two legs cannot claim the same position
        conn.execute(
            _INSERT_LINE, ("entry-1", 0, "iva", "iva_trasladado_cobrado", "haber", "16.00")
        )
    conn.execute(_INSERT_LINE, ("entry-1", 1, "iva", "iva_trasladado_cobrado", "haber", "16.00"))
    conn.commit()
    rows = conn.execute("SELECT ordinal, line_key FROM journal_lines ORDER BY ordinal").fetchall()
    assert [tuple(row) for row in rows] == [(0, "total"), (1, "iva")]


def test_an_entry_without_legs_is_representable() -> None:
    """§8a:204: 4.10/4.13 propose zero legs — the schema cannot demand them."""
    conn = _conn()
    _seed_entry(conn, "draft-1")
    conn.commit()
    assert conn.execute("SELECT count(*) FROM journal_lines").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM journal_entries").fetchone()[0] == 1


def test_money_columns_are_text_and_never_real() -> None:
    """§4: money is Decimal end to end — a REAL column would round it behind our back."""
    conn = _conn()
    assert _columns(conn, "journal_lines")["amount"] == "TEXT"
    assert _columns(conn, "posting_snapshot")["valuation_rate"] == "TEXT"
    for table in M3_TABLES:
        assert not {"REAL", "FLOAT", "DOUBLE"} & set(_columns(conn, table).values())


def test_the_m3_keys_are_recorded_not_enforced_as_foreign_keys() -> None:
    """The M2-E convention: the parent link is a verified key (SQLite FKs are off repo-wide)."""
    conn = _conn()
    for table in M3_TABLES:
        assert _foreign_keys(conn, table) == []


# --- the two evidence tables --------------------------------------------------------


def test_a_metadata_snapshot_records_an_observation_with_its_evidence() -> None:
    """§6/D-M3-7b: the domain words (`Vigente`/`Cancelado`), never the query's internal codes."""
    conn = _conn()
    conn.execute(_INSERT_METADATA, (UUID_TEXT, RFC_TEXT, "Cancelado", WHEN, HASH))
    conn.execute(
        "INSERT INTO metadata_snapshots (uuid, contributor_rfc, status, cancellation_date,"
        " cancellation_reason, substitution_uuid, retrieved_at, source_hash)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            UUID_TEXT,
            RFC_TEXT,
            "Cancelado",
            "2026-02-01",
            "01",
            SUBSTITUTION_UUID,
            LATER,
            OTHER_HASH,
        ),
    )
    conn.commit()
    rows = conn.execute(
        "SELECT status, cancellation_date, substitution_uuid, source_hash FROM metadata_snapshots"
        " ORDER BY snapshot_id"
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("Cancelado", None, None, HASH),
        ("Cancelado", "2026-02-01", SUBSTITUTION_UUID, OTHER_HASH),
    ]


def test_re_observing_the_same_evidence_is_stored_once() -> None:
    """§6: keyed by (uuid, contributor, retrieved_at, hash), so a re-read appends nothing."""
    conn = _conn()
    conn.execute(_INSERT_METADATA, (UUID_TEXT, RFC_TEXT, "Vigente", WHEN, HASH))
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(_INSERT_METADATA, (UUID_TEXT, RFC_TEXT, "Vigente", WHEN, HASH))


def test_a_posting_snapshot_keeps_the_evidence_and_omits_an_unused_fx_rate() -> None:
    """§11 M3/§8:192: the hash, the three versions, and FX only when a conversion happened."""
    conn = _conn()
    _seed_entry(conn, "entry-1")
    _seed_snapshot(conn, "entry-1")
    conn.commit()
    row = conn.execute(
        "SELECT entry_key, source_hash, rule_version, policy_version, mapping_version,"
        " valuation_date, valuation_source, valuation_rate, posted_at FROM posting_snapshot"
    ).fetchone()
    assert tuple(row) == ("entry-1", HASH, "1", "7", MAPPING_VERSION, None, None, None, WHEN)

    with pytest.raises(sqlite3.IntegrityError):  # one posting, one piece of evidence
        _seed_snapshot(conn, "entry-1")
