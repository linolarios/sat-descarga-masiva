"""M2: ReviewFlag shape + FiscalDocumentStatus (review state is additive, §8)."""

from sat_descarga_masiva.domain.model.fiscal_document import FiscalDocumentStatus
from sat_descarga_masiva.domain.model.review import (
    ReviewFlag,
    ReviewFlags,
    ReviewFlagState,
    ReviewFlagType,
)


def test_fiscal_document_status_is_unknown_by_default_in_m2() -> None:
    assert FiscalDocumentStatus.UNKNOWN.value == "unknown"
    assert FiscalDocumentStatus.VIGENTE.value == "vigente"
    assert FiscalDocumentStatus.CANCELLED.value == "cancelled"


def test_flag_opens_with_typed_identity() -> None:
    flag = ReviewFlag(ReviewFlagType.UNSUPPORTED_CURRENCY, "EUR is unsupported")
    assert flag.flag_type is ReviewFlagType.UNSUPPORTED_CURRENCY
    assert flag.state is ReviewFlagState.OPEN


def test_flag_is_queryable_and_countable() -> None:
    flags = ReviewFlags().with_flag(ReviewFlag(ReviewFlagType.UNSUPPORTED_CURRENCY, "EUR"))
    assert flags.has_open(ReviewFlagType.UNSUPPORTED_CURRENCY) is True
    assert flags.count(ReviewFlagType.UNSUPPORTED_CURRENCY) == 1
    assert len(flags.open_of(ReviewFlagType.UNSUPPORTED_CURRENCY)) == 1


def test_open_to_close_is_reflected_in_review_state() -> None:
    original = ReviewFlags().with_flag(ReviewFlag(ReviewFlagType.UNSUPPORTED_CURRENCY, "EUR"))
    closed = original.close_open(ReviewFlagType.UNSUPPORTED_CURRENCY)
    assert closed.has_open(ReviewFlagType.UNSUPPORTED_CURRENCY) is False
    assert closed.count(ReviewFlagType.UNSUPPORTED_CURRENCY) == 1  # still tracked, now closed
    assert closed.open_of(ReviewFlagType.UNSUPPORTED_CURRENCY) == ()


def test_closing_does_not_mutate_original() -> None:
    original = ReviewFlags().with_flag(ReviewFlag(ReviewFlagType.UNSUPPORTED_CURRENCY, "EUR"))
    original.close_open(ReviewFlagType.UNSUPPORTED_CURRENCY)
    # Nothing posted is mutated (trivially true in M2); the original is unchanged.
    assert original.has_open(ReviewFlagType.UNSUPPORTED_CURRENCY) is True


def test_flags_merge_additively_keeping_every_source_flag() -> None:
    currency = ReviewFlags().with_flag(ReviewFlag(ReviewFlagType.UNSUPPORTED_CURRENCY, "EUR"))
    signature = ReviewFlags().with_flag(ReviewFlag(ReviewFlagType.CFDI_SIGNATURE, "no sello"))
    mismatch = ReviewFlags().with_flag(ReviewFlag(ReviewFlagType.PERSPECTIVE_MISMATCH, "neither"))
    merged = currency.merged_with(signature, mismatch)
    assert merged.count(ReviewFlagType.UNSUPPORTED_CURRENCY) == 1
    assert merged.count(ReviewFlagType.CFDI_SIGNATURE) == 1
    assert merged.count(ReviewFlagType.PERSPECTIVE_MISMATCH) == 1
    # Additive union: no source erases another, and no source is itself mutated.
    assert currency.count(ReviewFlagType.CFDI_SIGNATURE) == 0
    assert merged.open_of(ReviewFlagType.UNSUPPORTED_CURRENCY)[0].reason == "EUR"


def test_merging_with_nothing_is_the_same_review_state() -> None:
    flags = ReviewFlags().with_flag(ReviewFlag(ReviewFlagType.CFDI_SIGNATURE, "absent"))
    assert flags.merged_with() == flags
    assert ReviewFlags().merged_with() == ReviewFlags()
