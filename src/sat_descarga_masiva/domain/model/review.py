"""Review flags — typed, additive, clearable review state (§8). Domain-only.

Review state is orthogonal to FiscalDocumentStatus (SAT fact) and to processing
lifecycle. A flag is attached at parse time; the PostingEligibilityValidator
(M3) independently refuses to post a document with an open flag.

Persisted facts are additive (§11 M2 `review_flags`): opening a flag appends a
row, closing it appends *another* row that points at the open one. No row is
ever updated in place, so "does this document have unresolved review flags?"
(§11 M3) is answerable from the facts alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class ReviewFlagType(StrEnum):
    """Why a document or an entry needs a human decision (§8).

    A flag is *review* state, never a posting state: it records that a human must
    decide, and it can never make anything post. Values are persisted TEXT, so an
    existing value is never renamed — §8's vocabulary only grows.
    """

    # Parse/composition — M2's producers (parser, perspective, signature).
    UNSUPPORTED_CURRENCY = "unsupported_currency"
    PERSPECTIVE_MISMATCH = "perspective_mismatch"
    PERSPECTIVE_UNDETERMINED = "perspective_undetermined"
    CFDI_SIGNATURE = "cfdi_signature"

    # Posting eligibility — the PostingEligibilityValidator's refusal reasons (§8).
    # Raised once per document by the validator; a rule never raises one itself.
    UNSUPPORTED_RULE = "unsupported_rule"
    MISSING_SOURCE_FIELD = "missing_source_field"
    MISSING_POSTING_IDENTITY = "missing_posting_identity"
    UNMAPPED_ACCOUNT = "unmapped_account"
    UNBALANCED_ENTRY = "unbalanced_entry"
    AMBIGUOUS_FX = "ambiguous_fx"

    # Rules — declared by a rule's review_conditions (§8 rule contract).
    MISSING_REP_ORIGINAL = "missing_rep_original"
    REP_INVARIANT_VIOLATION = "rep_invariant_violation"
    PAYROLL_DRAFT_UNSUPPORTED = "payroll_draft_unsupported"
    RETENCION_DRAFT_UNSUPPORTED = "retencion_draft_unsupported"

    # Temporal cancellation — status from the MetadataSnapshot join (§8).
    INELIGIBLE_SOURCE_STATE = "ineligible_source_state"

    # ⚠ Pending contador confirmation (§8a): these paths may flag, and must never post.
    IEPS_CREDITABLE_UNCONFIRMED = "ieps_creditable_unconfirmed"
    EGRESO_IVA_EVIDENCE_MISSING = "egreso_iva_evidence_missing"
    FX_DIFFERENCE_UNCONFIRMED = "fx_difference_unconfirmed"
    PARTIAL_PAYMENT_REVERSAL_UNSPECIFIED = "partial_payment_reversal_unspecified"


class ReviewFlagState(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class ReviewSubjectKind(StrEnum):
    """What a persisted review fact is attached to.

    ``DOCUMENT`` is the per-document fiscal review (M2.6), keyed on the CFDI's TFD
    UUID (§6). ``JOURNAL_ENTRY`` is the accounting review (§8): an entry carries
    review state of its own — "a PROPOSED entry the validator could not post, **or**
    any entry carrying an open flag" — so review facts attach to entries as well as
    to documents.

    A ``JOURNAL_ENTRY``'s ``subject_id`` is the entry's ``PostingFingerprint``
    (§8): it is unique per entry by construction and stable across runs, unlike a
    surrogate row id. The column is TEXT, so adding a kind rewrites no persisted
    row and needs no migration.
    """

    DOCUMENT = "document"
    JOURNAL_ENTRY = "journal_entry"


@dataclass(frozen=True)
class ReviewFlag:
    flag_type: ReviewFlagType
    reason: str
    state: ReviewFlagState = ReviewFlagState.OPEN

    def closed(self) -> ReviewFlag:
        """Return a NEW flag with CLOSED state (additive; never mutates)."""
        return ReviewFlag(self.flag_type, self.reason, ReviewFlagState.CLOSED)


@dataclass(frozen=True)
class ReviewFlagRecord:
    """One persisted review fact: an *opening* row or a *closing* row (§8).

    ``closes_flag_id is None`` marks the row that opened the flag (its ``flag``
    is OPEN). A closing row carries ``closes_flag_id = <opening flag_id>`` and a
    CLOSED ``flag`` — the persisted form of :meth:`ReviewFlag.closed`. Current
    state is therefore reconstructed from the facts: an opening row is open
    exactly while no closing row references it.
    """

    flag_id: int
    subject_kind: ReviewSubjectKind
    subject_id: str
    flag: ReviewFlag
    occurred_at: datetime
    closes_flag_id: int | None = None


@dataclass(frozen=True)
class ReviewFlags:
    """Immutable, queryable/countable review state attached to a document."""

    _flags: tuple[ReviewFlag, ...] = ()

    def with_flag(self, flag: ReviewFlag) -> ReviewFlags:
        return ReviewFlags((*self._flags, flag))

    def open_of(self, flag_type: ReviewFlagType) -> tuple[ReviewFlag, ...]:
        return tuple(
            f for f in self._flags if f.state is ReviewFlagState.OPEN and f.flag_type is flag_type
        )

    def open_flags(self) -> tuple[ReviewFlag, ...]:
        """Every OPEN flag, in producer order — the durable review state (§8).

        M2.8 persists exactly these facts, once each: they are the review state a
        projected document actually claims. Closed flags are history, not state, so
        they are not returned; the flags themselves are never mutated.
        """
        return tuple(f for f in self._flags if f.state is ReviewFlagState.OPEN)

    def has_open(self, flag_type: ReviewFlagType) -> bool:
        return bool(self.open_of(flag_type))

    def count(self, flag_type: ReviewFlagType) -> int:
        return sum(1 for f in self._flags if f.flag_type is flag_type)

    def close_open(self, flag_type: ReviewFlagType) -> ReviewFlags:
        return ReviewFlags(
            tuple(
                f.closed() if (f.flag_type is flag_type and f.state is ReviewFlagState.OPEN) else f
                for f in self._flags
            )
        )

    def merged_with(self, *others: ReviewFlags) -> ReviewFlags:
        """Union of review state: no producer's flags can erase another's (§8).

        M2.6 composes flags observed independently — by the parser (money), by the
        perspective resolution, and by the signature verification. Merging is
        additive and total: every source keeps every flag it opened, the sources
        themselves are untouched, order is producer order (deterministic), and
        merging no sources preserves the same logical review state.
        """
        flags = self._flags
        for other in others:
            flags = (*flags, *other._flags)
        return ReviewFlags(flags)
