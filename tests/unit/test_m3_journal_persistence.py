"""M3: the journal store contract — a posting is written once and read back whole (§8:166).

Behavioral assertions are shared, so ``InMemoryJournalEntryStore`` and
``SqliteJournalEntryStore`` cannot diverge. Scope is the three tables of one posting:
``journal_entries`` (the entry), ``journal_lines`` (its legs, each with the account the role
resolved to at posting time) and ``posting_snapshot`` (the §8:159 evidence recorded with it).

Three truths the tests pin:

- the identity is §8:194's ``entry_key``: re-appending the *identical* record is an
  idempotent no-op, while a different record under the same key is an
  ``ImmutableRecordConflict`` and leaves the stored one untouched — a posting state is
  assigned once (§8:166);
- what is stored is exactly what was recorded: legs keep their order, side, amount, role and
  posting-time account, and assumptions round-trip through §8:171's codec;
- the POSTED-resolves-every-leg invariant is ``JournalEntryRecord``'s (see
  ``test_m3_journal_model.py``), so a store is never handed a contradictory posting — which
  is also why a refused append leaves nothing behind: the failing write never began.
"""

import sqlite3
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import cast

import pytest

from sat_descarga_masiva.application.ports.persistence import JournalEntryStore
from sat_descarga_masiva.contabilidad.journal import (
    AccountingAssumption,
    JournalEntryRecord,
    JournalLine,
    JournalLineRecord,
    LineSide,
    PostingState,
    ProposedJournalEntry,
)
from sat_descarga_masiva.contabilidad.roles import AccountRole
from sat_descarga_masiva.contabilidad.validator import PostingDecision
from sat_descarga_masiva.domain.errors import ImmutableRecordConflict
from sat_descarga_masiva.domain.model.value_objects import Rfc, Uuid
from sat_descarga_masiva.domain.policy.money import NormalizedAmount
from sat_descarga_masiva.infrastructure.persistence.memory import InMemoryJournalEntryStore
from sat_descarga_masiva.infrastructure.persistence.sqlite import (
    SqliteJournalEntryStore,
    init_schema,
)

RFC = Rfc("AAA010101AAA")
OTHER_RFC = Rfc("BBB010101BBB")
UUID = Uuid("4e80345d-917f-40bb-a98f-4a73939353c5")
OTHER_UUID = Uuid("5e80345d-917f-40bb-a98f-4a73939353c5")
SOURCE_HASH = "a" * 64
ENTRY_DATE = date(2026, 1, 31)
RECORDED_AT = datetime(2026, 1, 31, 12, 0, tzinfo=UTC)
LATER = datetime(2026, 2, 1, 9, 30, tzinfo=UTC)
MAPPING_VERSION = "2026.01"
POLICY_VERSION = "1"
RULE_4_1 = "4.1"


@dataclass(frozen=True)
class Stores:
    """The store under test, typed as the port, so both adapters answer the same protocol."""

    journal: JournalEntryStore


def _memory_store() -> Stores:
    return Stores(journal=InMemoryJournalEntryStore())


def _sqlite_store() -> Stores:
    return Stores(journal=SqliteJournalEntryStore(_conn()))


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    return conn


@pytest.fixture(
    params=[
        pytest.param(_memory_store, id="in-memory"),
        pytest.param(_sqlite_store, id="sqlite"),
    ]
)
def stores(request: pytest.FixtureRequest) -> Stores:
    return cast(Stores, request.param())


def _line(
    *,
    role: AccountRole = AccountRole.CLIENTES,
    line_key: str = "total",
    ordinal: int = 0,
    side: LineSide = LineSide.DEBE,
    amount: str = "116.00",
    account: str | None = "105-001",
) -> JournalLineRecord:
    return JournalLineRecord(
        account_role=role,
        line_key=line_key,
        ordinal=ordinal,
        side=side,
        amount=NormalizedAmount(Decimal(amount)),
        resolved_account=account,
    )


def _record(
    *,
    rule_id: str = RULE_4_1,
    contributor_rfc: Rfc = RFC,
    source_uuid: Uuid = UUID,
    posting_state: PostingState = PostingState.POSTED,
    recorded_at: datetime = RECORDED_AT,
    assumptions: tuple[AccountingAssumption, ...] = (),
    lines: tuple[JournalLineRecord, ...] | None = None,
) -> JournalEntryRecord:
    """One posting as the ledger writes it: keyed by the §8:194 fingerprint of its entry."""
    entry = ProposedJournalEntry(
        rule_id=rule_id,
        rule_version="1",
        contributor_rfc=contributor_rfc,
        source_uuid=source_uuid,
        source_hash=SOURCE_HASH,
        entry_date=ENTRY_DATE,
        lines=(
            JournalLine(
                account_role=AccountRole.CLIENTES,
                line_key="total",
                side=LineSide.DEBE,
                amount=NormalizedAmount(Decimal("116.00")),
            ),
        ),
    )
    return JournalEntryRecord(
        entry_key=entry.fingerprint(MAPPING_VERSION).canonical(),
        contributor_rfc=contributor_rfc,
        source_uuid=source_uuid,
        source_hash=SOURCE_HASH,
        rule_id=rule_id,
        rule_version="1",
        mapping_version=MAPPING_VERSION,
        policy_version=POLICY_VERSION,
        posting_state=posting_state,
        entry_date=ENTRY_DATE,
        recorded_at=recorded_at,
        assumptions=assumptions,
        lines=(_line(),) if lines is None else lines,
    )


# --- the shared contract: both adapters, one behavior (§12) --------------------------


def test_a_posting_is_read_back_exactly_as_it_was_recorded(stores: Stores) -> None:
    """§8:166: what is read is the decision that was written, field for field."""
    record = _record(assumptions=(AccountingAssumption.ASSUMED_PUE,))
    stores.journal.append(record)

    assert stores.journal.get(record.entry_key) == record


def test_a_leg_keeps_its_role_side_amount_order_and_posting_time_account(
    stores: Stores,
) -> None:
    """§8a:207: the role the rule named and the account the mapping resolved, per leg."""
    record = _record(
        lines=(
            _line(),
            _line(
                role=AccountRole.IVA_TRASLADADO_COBRADO,
                line_key="iva",
                ordinal=1,
                side=LineSide.HABER,
                amount="16.00",
                account="208-001",
            ),
        )
    )
    stores.journal.append(record)

    stored = stores.journal.get(record.entry_key)
    assert stored is not None
    assert [line.line_key for line in stored.lines] == ["total", "iva"]
    assert [(line.side, line.amount.amount) for line in stored.lines] == [
        (LineSide.DEBE, Decimal("116.00")),
        (LineSide.HABER, Decimal("16.00")),
    ]
    assert [(line.account_role, line.resolved_account) for line in stored.lines] == [
        (AccountRole.CLIENTES, "105-001"),
        (AccountRole.IVA_TRASLADADO_COBRADO, "208-001"),
    ]


def test_an_entry_without_legs_or_assumptions_round_trips(stores: Stores) -> None:
    """§8a:204/§8:161: a zero-line record is representable — nothing is invented for it."""
    record = _record(posting_state=PostingState.SKIPPED, lines=())
    stores.journal.append(record)

    stored = stores.journal.get(record.entry_key)
    assert stored is not None
    assert (stored.lines, stored.assumptions) == ((), ())


def test_a_proposed_refusal_keeps_the_leg_it_could_not_resolve(stores: Stores) -> None:
    """§8:167: an unmapped role is stored as ``None``, so the refusal stays auditable."""
    record = _record(posting_state=PostingState.PROPOSED, lines=(_line(account=None),))
    stores.journal.append(record)

    stored = stores.journal.get(record.entry_key)
    assert stored is not None
    assert stored.lines[0].resolved_account is None


def test_a_naive_recorded_at_is_read_back_as_utc(stores: Stores) -> None:
    """Both adapters normalize on write (the M1/M2 rule), so the two cannot disagree."""
    record = _record(recorded_at=datetime(2026, 1, 31, 12, 0))
    stores.journal.append(record)

    stored = stores.journal.get(record.entry_key)
    assert stored is not None
    assert stored.recorded_at == RECORDED_AT
    assert stored.recorded_at.tzinfo is UTC


def test_an_unknown_key_is_simply_absent(stores: Stores) -> None:
    """A store answers "not recorded" with ``None`` — never by inventing a posting."""
    assert stores.journal.get("no-such-entry") is None


def test_appending_the_identical_record_twice_stores_it_once(stores: Stores) -> None:
    """§8:166: the same fact recorded twice is one fact — idempotent, never duplicated."""
    record = _record()
    stores.journal.append(record)
    stores.journal.append(record)

    assert stores.journal.for_source(RFC, UUID) == (record,)


def test_a_different_record_under_the_same_key_is_refused(stores: Stores) -> None:
    """A posting state is assigned once: a second, different record cannot replace it."""
    record = _record()
    stores.journal.append(record)

    with pytest.raises(ImmutableRecordConflict, match="different"):
        stores.journal.append(replace(record, posting_state=PostingState.PROPOSED))

    assert stores.journal.get(record.entry_key) == record  # the refused write changed nothing
    assert len(stores.journal.for_source(RFC, UUID)) == 1


def test_the_same_posting_for_another_book_or_source_is_another_entry(stores: Stores) -> None:
    """§8:194: one UUID in two clients' books, or two sources of one book, are two postings."""
    mine = _record()
    theirs = _record(contributor_rfc=OTHER_RFC)
    other_source = _record(source_uuid=OTHER_UUID)
    for record in (mine, theirs, other_source):
        stores.journal.append(record)

    assert stores.journal.for_source(RFC, UUID) == (mine,)
    assert stores.journal.for_source(OTHER_RFC, UUID) == (theirs,)
    assert stores.journal.for_source(RFC, OTHER_UUID) == (other_source,)


def test_for_source_reports_one_document_in_a_stable_order(stores: Stores) -> None:
    """Two entries for one document read back oldest first — written out of order on purpose."""
    later = _record(rule_id="4.2", recorded_at=LATER)
    earlier = _record(recorded_at=RECORDED_AT)
    stores.journal.append(later)
    stores.journal.append(earlier)

    assert stores.journal.for_source(RFC, UUID) == (earlier, later)


def test_a_posted_entry_with_an_unresolved_leg_is_never_handed_to_a_store(
    stores: Stores,
) -> None:
    """§8:159: the invariant is enforced while the record is *built*, before any store sees it.

    That is the whole design: there is no adapter-side check to forget, so the refusal is
    identical for both stores — and because the failing write never began, there is nothing
    left to roll back.
    """
    with pytest.raises(ValueError, match="resolves every leg"):
        _record(
            posting_state=PostingState.POSTED,
            lines=(_line(), _line(line_key="iva", ordinal=1, account=None)),
        )

    assert stores.journal.for_source(RFC, UUID) == ()


# --- the bridge: a decision projected into the store ---------------------------------


def _balanced_entry() -> ProposedJournalEntry:
    """A §8:158-balanced proposal, so the decision that carries it may be `POSTED`."""
    return ProposedJournalEntry(
        rule_id=RULE_4_1,
        rule_version="1",
        contributor_rfc=RFC,
        source_uuid=UUID,
        source_hash=SOURCE_HASH,
        entry_date=ENTRY_DATE,
        lines=(
            JournalLine(
                account_role=AccountRole.CLIENTES,
                line_key="total",
                side=LineSide.DEBE,
                amount=NormalizedAmount(Decimal("116.00")),
            ),
            JournalLine(
                account_role=AccountRole.INGRESOS,
                line_key="base",
                side=LineSide.HABER,
                amount=NormalizedAmount(Decimal("116.00")),
            ),
        ),
    )


def test_a_posted_decision_is_stored_as_the_record_it_projects_to(stores: Stores) -> None:
    """§8:166: the slice end to end — a decision projected to a record, written, read back.

    `PostingDecision`'s own contract is `test_m3_posting_validator.py`'s business; what this
    proves is the bridge. `to_record` produces exactly the aggregate both adapters accept —
    keyed by the entry's §8:194 fingerprint under the decision's own mapping version, every
    leg carrying the account the decision resolved — and the store returns it unchanged.
    """
    entry = _balanced_entry()
    decision = PostingDecision(
        entry=entry,
        posting_state=PostingState.POSTED,
        mapping_version=MAPPING_VERSION,
        policy_version=POLICY_VERSION,
        accounts={AccountRole.CLIENTES: "105-001", AccountRole.INGRESOS: "401-001"},
    )
    record = decision.to_record(recorded_at=RECORDED_AT)
    stores.journal.append(record)

    assert record.entry_key == entry.fingerprint(MAPPING_VERSION).canonical()
    assert [line.resolved_account for line in record.lines] == ["105-001", "401-001"]
    assert stores.journal.get(record.entry_key) == record
    assert stores.journal.for_source(RFC, UUID) == (record,)


# --- SQLite: one posting is one transaction ------------------------------------------


def _counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        table: int(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
        for table in ("journal_entries", "journal_lines", "posting_snapshot")
    }


def test_the_entry_its_legs_and_the_evidence_are_written_together() -> None:
    """§8:159: the evidence records the versions *with* the posting — never one without it."""
    conn = _conn()
    store = SqliteJournalEntryStore(conn)
    record = _record(assumptions=(AccountingAssumption.ASSUMED_PUE,))
    store.append(record)

    assert _counts(conn) == {"journal_entries": 1, "journal_lines": 1, "posting_snapshot": 1}
    snapshot = conn.execute("SELECT * FROM posting_snapshot").fetchone()
    assert (snapshot["entry_key"], snapshot["source_hash"]) == (record.entry_key, SOURCE_HASH)
    assert snapshot["rule_version"] == "1"
    assert snapshot["policy_version"] == POLICY_VERSION
    assert snapshot["mapping_version"] == MAPPING_VERSION
    assert snapshot["posted_at"] == RECORDED_AT.isoformat()
    assert (
        snapshot["valuation_date"],
        snapshot["valuation_source"],
        snapshot["valuation_rate"],
    ) == (None, None, None)  # §8:192: no conversion happened, so none is claimed


def test_a_failure_while_writing_rolls_the_whole_posting_back() -> None:
    """§8:166: half a posting is not a posting — the entry and its legs go back with the failure.

    The failure is real, not injected: a `posting_snapshot` row already claims this entry's
    key, so the *third* write hits the primary key after the entry and its legs were already
    inserted. Only the transaction the store opened — and rolls back — makes those two
    writes invisible.
    """
    conn = _conn()
    store = SqliteJournalEntryStore(conn)
    record = _record()
    conn.execute(
        "INSERT INTO posting_snapshot (entry_key, source_hash, rule_version, policy_version,"
        " mapping_version, posted_at) VALUES (?, ?, ?, ?, ?, ?)",
        (
            record.entry_key,
            SOURCE_HASH,
            "1",
            POLICY_VERSION,
            MAPPING_VERSION,
            RECORDED_AT.isoformat(),
        ),
    )
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        store.append(record)

    assert _counts(conn) == {"journal_entries": 0, "journal_lines": 0, "posting_snapshot": 1}
    assert store.get(record.entry_key) is None


def test_commit_false_leaves_the_posting_to_the_unit_of_work() -> None:
    """The M2.8 convention: an outer unit of work decides — the store never commits for it."""
    conn = _conn()
    store = SqliteJournalEntryStore(conn, commit=False)
    record = _record()
    store.append(record)
    assert store.get(record.entry_key) == record  # visible inside the transaction

    conn.rollback()

    assert store.get(record.entry_key) is None  # and gone when the unit of work says no
    assert _counts(conn) == {"journal_entries": 0, "journal_lines": 0, "posting_snapshot": 0}


def test_an_idempotent_re_append_writes_no_second_row() -> None:
    """Both halves of the contract on the adapter that has rows: one entry, one set of legs."""
    conn = _conn()
    store = SqliteJournalEntryStore(conn)
    record = _record()
    store.append(record)
    store.append(record)

    assert _counts(conn) == {"journal_entries": 1, "journal_lines": 1, "posting_snapshot": 1}
