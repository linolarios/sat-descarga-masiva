"""Review flags — typed, additive, clearable review state (§8). Domain-only.

Review state is orthogonal to FiscalDocumentStatus (SAT fact) and to processing
lifecycle. A flag is attached at parse time; the PostingEligibilityValidator
(M3) independently refuses to post a document with an open flag.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ReviewFlagType(StrEnum):
    UNSUPPORTED_CURRENCY = "unsupported_currency"
    PERSPECTIVE_MISMATCH = "perspective_mismatch"
    PERSPECTIVE_UNDETERMINED = "perspective_undetermined"


class ReviewFlagState(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


@dataclass(frozen=True)
class ReviewFlag:
    flag_type: ReviewFlagType
    reason: str
    state: ReviewFlagState = ReviewFlagState.OPEN

    def closed(self) -> ReviewFlag:
        """Return a NEW flag with CLOSED state (additive; never mutates)."""
        return ReviewFlag(self.flag_type, self.reason, ReviewFlagState.CLOSED)


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
