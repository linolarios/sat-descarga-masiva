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

The module's second half is what the ledger *writes down*: :func:`encode_assumptions`
and :func:`decode_assumptions`, the one codec for that evidence (``assumptions`` is
persisted TEXT, §8:171), and the frozen :class:`JournalEntryRecord` /
:class:`JournalLineRecord` aggregate a ``JournalEntryStore`` accepts. The records live
here, beside the model they project, because the invariant that makes a posting
trustworthy — ``POSTED`` resolves every leg (§8:159) — is a property of the decision,
not of an adapter, so it must be enforced in the one type both stores take.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
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


def encode_assumptions(assumptions: tuple[AccountingAssumption, ...]) -> str:
    """§8:171's evidence as the persisted TEXT: a JSON array of the stable words.

    ``()`` encodes to ``"[]"``, which is the domain's own value for "presumed nothing",
    so an entry that presumed nothing needs no special case. The tuple's order is
    reproduced rather than sorted — the rule's order is what it stated — and the encoding
    is compact, so the same evidence always produces the same text. A record of what was
    presumed may not depend on how a serializer happened to format it.
    """
    return json.dumps(
        [item.value for item in assumptions],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def decode_assumptions(text: str) -> tuple[AccountingAssumption, ...]:
    """The inverse of :func:`encode_assumptions`, and it fails closed.

    A word this engine does not know, a duplicate, a non-string element or anything that
    is not a JSON array is a ``ValueError``. Nothing is dropped, deduplicated or
    truncated on the way in: an entry whose assumptions cannot be read is *not* the entry
    that was written, so reading it as a lesser one would forge evidence. Decoding
    something the codec never wrote is a bug to fix, never a case to tolerate.
    """
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"assumptions are not valid JSON: {text!r}") from error
    if not isinstance(decoded, list):
        raise ValueError(f"assumptions are a JSON array of words, got {text!r}")
    words: list[AccountingAssumption] = []
    for item in decoded:
        if not isinstance(item, str):
            raise ValueError(f"assumptions are words, got {item!r} in {text!r}")
        try:
            words.append(AccountingAssumption(item))
        except ValueError as error:
            raise ValueError(f"{item!r} is not an accounting assumption: {text!r}") from error
    if len(set(words)) != len(words):
        raise ValueError(f"an assumption stated twice adds nothing: {text!r}")
    return tuple(words)


@dataclass(frozen=True)
class JournalLineRecord:
    """One leg as it was written: its role, its position, and the account it was posted to.

    ``resolved_account`` is the §8a:207 resolution the leg was posted to *at posting
    time*, recorded so the line stays explainable without the client's current YAML. It
    is ``None`` for a leg nothing was posted to: an unmapped role on a refused,
    ``PROPOSED`` entry is the audit evidence §8:167 asks for, and ``None`` is the honest
    value for "no account was chosen" — never a placeholder for an account that was.
    ``ordinal`` is the position the rule proposed the leg in: §8:194's ``line_key``
    identifies the leg, the ordinal keeps the entry's order.
    """

    account_role: AccountRole
    line_key: str
    ordinal: int
    side: LineSide
    amount: NormalizedAmount
    resolved_account: str | None = None

    def __post_init__(self) -> None:
        if not self.line_key:
            raise ValueError(
                "a journal line needs a non-empty line_key: §8:194 identifies the lines of"
                " one entry by it, and the empty key belongs to the entry itself"
            )
        if self.ordinal < 0:
            raise ValueError(f"an ordinal is a position, never negative: {self.ordinal}")
        if self.resolved_account is not None and not self.resolved_account.strip():
            raise ValueError(
                "a blank account is not a resolution: §8a:207 resolves a role to an"
                " account or to nothing at all (None)"
            )


@dataclass(frozen=True)
class JournalEntryRecord:
    """One decision as the durable fact: what §8:166 assigns once and never rewrites.

    A record is the projection of a ``PostingDecision`` (built by
    ``PostingDecision.to_record``), so it is always a decision the
    ``PostingEligibilityValidator`` stood behind, never a hand-assembled claim about one.
    It carries what a reader needs to explain the posting without re-reading the source
    or today's mapping: §8:194's ``entry_key`` *and* the fields it was computed from, the
    rule/policy/mapping versions §8:159 records, §8:171's ``assumptions``, and each leg's
    posting-time account (§8a:207).

    The invariant this type exists for is the one no field can express: **``POSTED``
    resolves every leg** (§8:159's "deterministic account mapping"). An entry that says
    ``POSTED`` and leaves a leg without an account contradicts its own verdict. That is a
    *writer* bug, not a document-level review case, so it is refused here — in the one
    aggregate both stores accept — and no adapter can bypass it. The other states are
    free to be incomplete, truthfully: a ``PROPOSED`` leg may carry ``None`` precisely so
    the unmapped role a human must resolve stays auditable, and a ``SKIPPED`` entry has
    no legs at all (§8:161).
    """

    entry_key: str
    contributor_rfc: Rfc
    source_uuid: Uuid
    source_hash: str
    rule_id: str
    rule_version: str
    mapping_version: str
    policy_version: str
    posting_state: PostingState
    entry_date: date
    recorded_at: datetime
    assumptions: tuple[AccountingAssumption, ...] = ()
    lines: tuple[JournalLineRecord, ...] = ()

    def __post_init__(self) -> None:
        if not self.entry_key.strip():
            raise ValueError(
                "an entry is keyed by §8:194's fingerprint, so an empty entry_key has no"
                " identity to append under"
            )
        if not self.source_hash.strip():
            raise ValueError("§8:159: source_hash is part of every posting's identity")
        if not self.rule_id.strip() or not self.rule_version.strip():
            raise ValueError("§8:194: a posting is keyed by rule_id + rule_version")
        if not self.mapping_version.strip():
            raise ValueError("§8:194: a posting is keyed by mapping_version")
        if not self.policy_version.strip():
            raise ValueError("§8:159: the policy version is recorded with every posting")
        if len(set(self.assumptions)) != len(self.assumptions):
            raise ValueError(
                "an assumption is evidence about the entry, so stating one twice adds"
                f" nothing: {[item.value for item in self.assumptions]}"
            )
        keys = [line.line_key for line in self.lines]
        if len(set(keys)) != len(keys):
            raise ValueError(
                f"line_key identifies a line within its entry (§8:194): {sorted(set(keys))}"
            )
        ordinals = [line.ordinal for line in self.lines]
        if len(set(ordinals)) != len(ordinals):
            raise ValueError(f"a leg has one position: {sorted(set(ordinals))}")
        if self.posting_state is PostingState.SKIPPED and self.lines:
            raise ValueError(
                f"§8:161: a skip posts nothing, so it carries no legs: {len(self.lines)} given"
            )
        if self.posting_state is PostingState.POSTED:
            if not self.lines:
                raise ValueError("§8:159: POSTED needs the entry it posted, with its legs")
            unresolved = [line.line_key for line in self.lines if line.resolved_account is None]
            if unresolved:
                raise ValueError(
                    "§8:159: a POSTED entry resolves every leg — §8a:207's mapping is a"
                    f" precondition, not a hope; unresolved: {unresolved}"
                )
