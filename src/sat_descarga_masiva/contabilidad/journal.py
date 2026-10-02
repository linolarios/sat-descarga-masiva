"""Journal model — what a rule proposes, and the identity of what it proposes (§8).

A rule is a pure function: it reads the fiscal model and returns a
``ProposedJournalEntry``. It never assigns a posting state — what a rule builds is
``PROPOSED`` by construction, and only the ``PostingEligibilityValidator`` may assign
``POSTED`` (§8:158, §8:166). Two consequences are visible in this module:

- a leg moves *one* side: ``LineSide.DEBE`` or ``LineSide.HABER``, carrying one
  strictly positive ``NormalizedAmount``. A negative debit is not a way to say credit,
  so the Debe == Haber gate cannot be satisfied by two signed halves of one leg;
- the mapping is absent. A rule names :class:`AccountRole`s; which account a role
  resolves to — and under which ``mapping_version`` — is the mapping's decision
  (§8:196), so the mapping version enters only when a fingerprint is computed.

What a rule *assumes* is likewise not a line. ``ASSUMED_PUE`` is accounting
evidence — "this clearing leg is the SAT's PUE presumption, not verified payment"
(§8:171) — so it travels on the entry as an :class:`AccountingAssumption`, never
inside a ``line_key``: the key is §8:194's structural identity for a leg, and an
assumption that may later be promoted to ``SUPPORTED_BY_BANK`` must not be able to
redefine it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from sat_descarga_masiva.contabilidad.roles import AccountRole
from sat_descarga_masiva.domain.model.value_objects import Rfc, Uuid
from sat_descarga_masiva.domain.policy.money import NormalizedAmount


class PostingState(StrEnum):
    """The state of a proposed entry (§8:166).

    Assigned once and never rewritten: ``PROPOSED`` and ``SKIPPED`` may be persisted
    for audit, but only the ``PostingEligibilityValidator`` brings an entry to
    ``POSTED``. The stored words are stable — they are part of the schema, not of a
    message.
    """

    PROPOSED = "proposed"
    POSTED = "posted"
    SKIPPED = "skipped"


class LineSide(StrEnum):
    """Which side of the entry a leg moves — Spanish Debe/Haber, as the contador reads it."""

    DEBE = "debe"
    HABER = "haber"

    @property
    def opposite(self) -> LineSide:
        """The reversal side: a compensating entry books the opposite side (§8:193)."""
        return LineSide.HABER if self is LineSide.DEBE else LineSide.DEBE


class AccountingAssumption(StrEnum):
    """What a rule presumed rather than verified — evidence about an entry (§8:171).

    An assumption is deliberately *not* a leg and not a ``line_key``: §8:194's key is the
    structural identity of a line, and §8:171's ``ASSUMED_PUE`` is a statement about the
    world (the SAT presumes a PUE comprobante paid in one exhibition; no bank movement was
    confirmed) that a later reconciliation promotes to ``SUPPORTED_BY_BANK``. Recording it
    beside the legs keeps a posting explainable without letting the assumption redefine which
    facts were posted. Values are persisted TEXT, so an existing value is never renamed.
    """

    ASSUMED_PUE = "assumed_pue"


@dataclass(frozen=True)
class JournalLine:
    """One leg of an entry: a semantic role, one side, one positive amount (§8a).

    ``line_key`` names this leg inside its entry (§8:194) — never empty, because the
    empty key is the entry's own, and never repeated within the entry, because it is
    what identifies the leg.
    """

    account_role: AccountRole
    line_key: str
    side: LineSide
    amount: NormalizedAmount

    def __post_init__(self) -> None:
        if not self.line_key:
            raise ValueError(
                "a journal line needs a non-empty line_key: §8:194 identifies the lines of"
                " one entry by it, and the empty key belongs to the entry itself"
            )
        if not self.amount.amount.is_finite() or self.amount.amount <= 0:
            raise ValueError(
                f"a journal line moves a strictly positive amount, got {self.amount.amount}:"
                " a sign is not a second way to name the side, and a corrupt (non-finite)"
                " amount is not an amount at all (§8:159)"
            )


@dataclass(frozen=True)
class PostingFingerprint:
    """The idempotency key of one journal line (§8:194), entry-level when ``line_key`` is empty.

    ``canonical()`` is the key as stored text. It is JSON rather than a joined string so
    no field boundary can be forged: the same delimiter inside a value cannot be made to
    look like a separator.
    """

    contributor_rfc: Rfc
    source_uuid: Uuid
    rule_id: str
    rule_version: str
    line_key: str
    mapping_version: str

    def canonical(self) -> str:
        """The key as text — fixed field order, every field quoted and escaped."""
        return json.dumps(
            [
                self.contributor_rfc.value,
                self.source_uuid.value,
                self.rule_id,
                self.rule_version,
                self.line_key,
                self.mapping_version,
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )


@dataclass(frozen=True)
class ProposedJournalEntry:
    """What a rule proposes (§8): a dated set of legs, tied to the CFDI it came from.

    ``posting_state`` is derived, never a field — a rule cannot express ``POSTED``, so
    the gate in §8:158 cannot be short-circuited from a rule.

    ``mapping_version`` is likewise absent: it is an argument of :meth:`fingerprint`,
    because a rule is role-typed and the mapping is resolved after it runs (§8:196).

    ``assumptions`` carries what the rule *presumed* rather than verified (§8:171) — evidence
    about the entry, never part of the entry's or a leg's identity. It is deliberately not a
    ``line_key``: §8:194's key is structural, so an assumption that a later reconciliation
    promotes to `SUPPORTED_BY_BANK` may change without changing which facts were posted.
    """

    rule_id: str
    rule_version: str
    contributor_rfc: Rfc
    source_uuid: Uuid | None
    source_hash: str
    entry_date: date
    lines: tuple[JournalLine, ...]
    assumptions: tuple[AccountingAssumption, ...] = ()

    def __post_init__(self) -> None:
        keys = [line.line_key for line in self.lines]
        if len(set(keys)) != len(keys):
            repeated = sorted({key for key in keys if keys.count(key) > 1})
            raise ValueError(
                f"line_key identifies a line within its entry (§8:194), so it cannot repeat:"
                f" {repeated}"
            )
        if len(set(self.assumptions)) != len(self.assumptions):
            raise ValueError(
                "an assumption is evidence about the entry, so stating one twice adds nothing:"
                f" {[item.value for item in self.assumptions]}"
            )

    @property
    def posting_state(self) -> PostingState:
        """Always ``PROPOSED``: a rule proposes, the ``PostingEligibilityValidator`` decides."""
        return PostingState.PROPOSED

    def debe_total(self) -> Decimal:
        return self._total(LineSide.DEBE)

    def haber_total(self) -> Decimal:
        return self._total(LineSide.HABER)

    def is_balanced(self) -> bool:
        """§8:158's Debe == Haber gate, as arithmetic on ``Decimal``.

        An entry with no legs balances: §8a:204 has 4.10/4.13 propose zero lines, and
        what stops those is their review flag, not a nonzero total.
        """
        return self.debe_total() == self.haber_total()

    def fingerprint(self, mapping_version: str) -> PostingFingerprint:
        """The entry's identity: §8:194's tuple with an empty ``line_key``."""
        return self._fingerprint(mapping_version, line_key="")

    def line_fingerprints(self, mapping_version: str) -> tuple[PostingFingerprint, ...]:
        """One identity per leg, in the order the rule proposed them."""
        return tuple(
            self._fingerprint(mapping_version, line_key=line.line_key) for line in self.lines
        )

    def _total(self, side: LineSide) -> Decimal:
        return sum((line.amount.amount for line in self.lines if line.side is side), Decimal("0"))

    def _fingerprint(self, mapping_version: str, *, line_key: str) -> PostingFingerprint:
        if self.source_uuid is None:
            raise ValueError(
                "an entry without a source_uuid has no posting identity: §8:158 requires"
                " source_uuid + source_hash, so the fingerprint fails closed instead of"
                " inventing one"
            )
        return PostingFingerprint(
            contributor_rfc=self.contributor_rfc,
            source_uuid=self.source_uuid,
            rule_id=self.rule_id,
            rule_version=self.rule_version,
            line_key=line_key,
            mapping_version=mapping_version,
        )
