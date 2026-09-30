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
    UNSUPPORTED_CURRENCY = "unsupported_currency"
    PERSPECTIVE_MISMATCH = "perspective_mismatch"
    PERSPECTIVE_UNDETERMINED = "perspective_undetermined"
    CFDI_SIGNATURE = "cfdi_signature"


class ReviewFlagState(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class ReviewSubjectKind(StrEnum):
    """What a persisted review fact is attached to.

    M2 has exactly one producer of persisted flags — the per-document fiscal
    review (M2.6) — so only ``DOCUMENT`` exists today. §8's entries are the same
    shape and extend this column additively (the value is stored as TEXT), so
    nothing is built here for M3.
    """

    DOCUMENT = "document"


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
