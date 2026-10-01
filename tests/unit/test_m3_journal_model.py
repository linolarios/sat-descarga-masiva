"""M3 step 2: the journal model — role-typed legs, a proposed entry, its identity (sec 8).

**A rule proposes; the `PostingEligibilityValidator` decides** (§8:158). These tests
pin both halves of that sentence: what a rule *can* build (legs, totals, the
`PostingFingerprint`) and what it cannot express at all — a `POSTED` state, a leg
with two sides, a negative amount, an account number, or a mapping version.

Three of §8's invariants are structural here rather than checked later:

- a leg is one side of the entry (`debe`/`haber`) with a strictly positive
  `NormalizedAmount`, so "a credit written as a negative debit" is unrepresentable;
- `line_key` is non-empty and unique within its entry: §8:194 distinguishes the
  *lines of one entry* by it, and the empty key is reserved for the entry itself;
- `mapping_version` is an argument to the fingerprint, never a field of the entry —
  a rule targets `AccountRole`s and never sees the chart of accounts (§8:196).
"""

from dataclasses import fields
from datetime import date
from decimal import Decimal

import pytest

from sat_descarga_masiva.contabilidad.journal import (
    JournalLine,
    LineSide,
    PostingFingerprint,
    PostingState,
    ProposedJournalEntry,
)
from sat_descarga_masiva.contabilidad.roles import AccountRole
from sat_descarga_masiva.domain.model.value_objects import Rfc, Uuid
from sat_descarga_masiva.domain.policy.money import NormalizedAmount

RFC = Rfc("AAA010101AAA")
OTHER_RFC = Rfc("BBB010101BBB")
UUID = Uuid("4e80345d-917f-40bb-a98f-4a73939353c5")
SOURCE_HASH = "a" * 64
ENTRY_DATE = date(2026, 1, 31)
RULE_ID = "4.1"
RULE_VERSION = "1"
MAPPING_VERSION = "2026.01"


def _amount(text: str) -> NormalizedAmount:
    return NormalizedAmount(Decimal(text))


def _line(
    *,
    role: AccountRole = AccountRole.CLIENTES,
    line_key: str = "total",
    side: LineSide = LineSide.DEBE,
    amount: str = "116.00",
) -> JournalLine:
    return JournalLine(account_role=role, line_key=line_key, side=side, amount=_amount(amount))


def _entry(**overrides: object) -> ProposedJournalEntry:
    base: dict[str, object] = {
        "rule_id": RULE_ID,
        "rule_version": RULE_VERSION,
        "contributor_rfc": RFC,
        "source_uuid": UUID,
        "source_hash": SOURCE_HASH,
        "entry_date": ENTRY_DATE,
        "lines": (_line(),),
    }
    base.update(overrides)
    return ProposedJournalEntry(**base)  # type: ignore[arg-type]


# --- posting state -----------------------------------------------------------------


def test_the_posting_states_are_the_three_from_8_166() -> None:
    assert {state.value for state in PostingState} == {"proposed", "posted", "skipped"}


def test_a_rule_builds_a_proposed_entry_and_cannot_express_a_posted_one() -> None:
    """§8:158/§8:166: the state is derived, so no rule can hand itself `POSTED`."""
    assert _entry().posting_state is PostingState.PROPOSED
    assert "posting_state" not in {field.name for field in fields(ProposedJournalEntry)}


# --- a leg is one side, one positive amount -----------------------------------------


def test_a_leg_is_one_side_of_the_entry_never_a_signed_pair() -> None:
    """The illegal state is unrepresentable: no debit and credit field to fill at once."""
    leg = _line()
    assert {field.name for field in fields(JournalLine)} == {
        "account_role",
        "line_key",
        "side",
        "amount",
    }
    assert leg.side is LineSide.DEBE
    assert leg.amount == _amount("116.00")


@pytest.mark.parametrize("amount", ("0.00", "-116.00"))
def test_a_leg_refuses_a_non_positive_amount(amount: str) -> None:
    """A leg that moves nothing is not a leg; a sign is not a second way to say 'haber'."""
    with pytest.raises(ValueError, match="positive amount"):
        _line(amount=amount)


def test_a_leg_refuses_an_empty_line_key() -> None:
    """The empty key belongs to the entry: a leg using it would collide with §8:194's key."""
    with pytest.raises(ValueError, match="line_key"):
        _line(line_key="")


def test_the_opposite_side_is_the_reversal_side() -> None:
    """§8:193: a compensating entry books the opposite sides of the original."""
    assert LineSide.DEBE.opposite is LineSide.HABER
    assert LineSide.HABER.opposite is LineSide.DEBE


def test_an_entry_refuses_two_legs_with_the_same_line_key() -> None:
    """§8:194: `line_key` identifies a leg *within* its entry, so it cannot repeat."""
    legs = (
        _line(line_key="iva", role=AccountRole.IVA_TRASLADADO_COBRADO),
        _line(line_key="iva"),
    )
    with pytest.raises(ValueError, match="line_key"):
        _entry(lines=legs)


# --- totals and the Debe == Haber gate ----------------------------------------------


def test_totals_are_exact_decimals_per_side() -> None:
    """§8:158's gate is arithmetic on `Decimal`, never on a float that 'almost' balances."""
    entry = _entry(
        lines=(
            _line(line_key="subtotal", role=AccountRole.INGRESOS, amount="0.10"),
            _line(
                line_key="iva",
                role=AccountRole.IVA_TRASLADADO_NO_COBRADO,
                side=LineSide.HABER,
                amount="0.20",
            ),
        )
    )
    assert entry.debe_total() == Decimal("0.10")
    assert entry.haber_total() == Decimal("0.20")
    assert entry.is_balanced() is False


def test_an_entry_whose_sides_agree_is_balanced() -> None:
    entry = _entry(
        lines=(
            _line(line_key="total", amount="116.00"),
            _line(
                line_key="subtotal",
                role=AccountRole.INGRESOS,
                side=LineSide.HABER,
                amount="100.00",
            ),
            _line(
                line_key="iva",
                role=AccountRole.IVA_TRASLADADO_COBRADO,
                side=LineSide.HABER,
                amount="16.00",
            ),
        )
    )
    assert entry.debe_total() == entry.haber_total() == Decimal("116.00")
    assert entry.is_balanced() is True


def test_an_entry_without_legs_balances_arithmetically() -> None:
    """§8a:204: 4.10/4.13 propose **zero lines** — their block is the flag, not a total."""
    entry = _entry(lines=())
    assert entry.debe_total() == Decimal("0")
    assert entry.haber_total() == Decimal("0")
    assert entry.is_balanced() is True
    assert entry.lines == ()


def test_the_entry_keeps_its_accounting_date() -> None:
    """§8a:208: the CFDI date drives journal dating (and 4.15's cancellation period)."""
    assert _entry().entry_date == date(2026, 1, 31)


# --- PostingFingerprint (§8:194) ----------------------------------------------------


def test_the_entry_fingerprint_carries_all_six_fields_of_8_194() -> None:
    fingerprint = _entry().fingerprint(MAPPING_VERSION)
    assert isinstance(fingerprint, PostingFingerprint)
    assert fingerprint.contributor_rfc == RFC
    assert fingerprint.source_uuid == UUID
    assert fingerprint.rule_id == RULE_ID
    assert fingerprint.rule_version == RULE_VERSION
    assert fingerprint.mapping_version == MAPPING_VERSION
    assert fingerprint.line_key == ""  # the entry itself: no line


@pytest.mark.parametrize(
    "overrides",
    (
        {"rule_id": "4.3"},
        {"rule_version": "2"},
        {"contributor_rfc": OTHER_RFC},
        {"source_uuid": Uuid("9f1a4c8b-1f8e-4a63-9c47-5d0ee93a2e11")},
    ),
)
def test_every_8_194_field_changes_the_key(overrides: dict[str, object]) -> None:
    """Each field is part of the identity — changing one is a different fact."""
    entry = _entry(**overrides)
    assert entry.fingerprint(MAPPING_VERSION).canonical() != (
        _entry().fingerprint(MAPPING_VERSION).canonical()
    )


def test_the_mapping_version_changes_the_key() -> None:
    """§8:194: a new mapping version is a different posting fact, never a silent rebuild."""
    entry = _entry()
    assert entry.fingerprint("2026.02").canonical() != entry.fingerprint("2026.01").canonical()


def test_the_same_client_and_rule_reproduce_the_same_key() -> None:
    """Idempotency: re-running the rule over the same source proposes the same fact."""
    assert _entry().fingerprint(MAPPING_VERSION) == _entry().fingerprint(MAPPING_VERSION)
    assert _entry().fingerprint(MAPPING_VERSION).canonical() == (
        _entry().fingerprint(MAPPING_VERSION).canonical()
    )


def test_the_two_managed_clients_get_different_keys() -> None:
    """§8:194: the same CFDI UUID in two clients' folders is two postings, not one."""
    mine = _entry(contributor_rfc=RFC)
    theirs = _entry(contributor_rfc=OTHER_RFC)
    assert mine.fingerprint(MAPPING_VERSION) != theirs.fingerprint(MAPPING_VERSION)


def test_every_line_has_its_own_key_under_the_entry_key() -> None:
    """§8:194 carries `line_key`, so identity is per leg — and never the entry's own key."""
    entry = _entry(
        lines=(
            _line(line_key="total"),
            _line(line_key="iva", role=AccountRole.IVA_TRASLADADO_COBRADO, side=LineSide.HABER),
        )
    )
    line_keys = [fp.line_key for fp in entry.line_fingerprints(MAPPING_VERSION)]
    assert line_keys == ["total", "iva"]
    canonical = {fp.canonical() for fp in entry.line_fingerprints(MAPPING_VERSION)}
    assert len(canonical) == 2
    assert entry.fingerprint(MAPPING_VERSION).canonical() not in canonical


def test_the_key_cannot_be_forged_across_a_field_boundary() -> None:
    """A delimiter-joined key would collide on ('4.1', 'a:b') vs ('4.1:a', 'b')."""
    left = _entry(rule_id="4.1", lines=(_line(line_key="a:b"),))
    right = _entry(rule_id="4.1:a", lines=(_line(line_key="b"),))
    assert left.line_fingerprints(MAPPING_VERSION)[0] != right.line_fingerprints(MAPPING_VERSION)[0]


def test_a_key_is_hashable_and_comparable() -> None:
    fingerprint = _entry().fingerprint(MAPPING_VERSION)
    assert len({fingerprint, _entry().fingerprint(MAPPING_VERSION)}) == 1
    assert isinstance(fingerprint.canonical(), str)


def test_an_entry_without_a_source_uuid_has_no_fingerprint() -> None:
    """§8:158: `source_uuid` + `source_hash` are POSTED preconditions, so identity fails closed."""
    with pytest.raises(ValueError, match="source_uuid"):
        _entry(source_uuid=None).fingerprint(MAPPING_VERSION)


def test_the_entry_never_carries_the_mapping_version() -> None:
    """§8:196: rules are role-typed — the mapping version arrives with the resolution."""
    assert "mapping_version" not in {field.name for field in fields(ProposedJournalEntry)}
