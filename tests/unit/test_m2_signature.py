"""M2.5: a SignatureVerdict maps to additive review state, never to a SAT fact.

VALID adds nothing; every other outcome opens exactly one CFDI_SIGNATURE flag whose reason
carries the outcome token, so downstream review (M3) can act on a deterministic string. No
crypto, no XML and no satcfdi here: these tests use fabricated verdicts only.
"""

from dataclasses import FrozenInstanceError

import pytest

from sat_descarga_masiva.domain.model.review import (
    ReviewFlag,
    ReviewFlags,
    ReviewFlagState,
    ReviewFlagType,
)
from sat_descarga_masiva.domain.model.signature import SignatureOutcome, SignatureVerdict
from sat_descarga_masiva.fiscal.signature import reason_for, review_flags_for

_NON_VALID = (
    SignatureOutcome.INVALID,
    SignatureOutcome.ABSENT,
    SignatureOutcome.UNSUPPORTED,
    SignatureOutcome.VALIDATION_ERROR,
)


def test_valid_verdict_adds_no_review_flag() -> None:
    flags = review_flags_for(SignatureVerdict(SignatureOutcome.VALID))
    assert flags == ReviewFlags()
    assert flags.has_open(ReviewFlagType.CFDI_SIGNATURE) is False


def test_valid_verdict_ignores_its_detail() -> None:
    verdict = SignatureVerdict(SignatureOutcome.VALID, "detail that must not become a reason")
    assert review_flags_for(verdict).count(ReviewFlagType.CFDI_SIGNATURE) == 0


@pytest.mark.parametrize("outcome", _NON_VALID)
def test_every_non_valid_outcome_opens_exactly_one_flag(outcome: SignatureOutcome) -> None:
    flags = review_flags_for(SignatureVerdict(outcome, "detail"))
    assert flags.count(ReviewFlagType.CFDI_SIGNATURE) == 1
    assert flags.has_open(ReviewFlagType.CFDI_SIGNATURE) is True


@pytest.mark.parametrize("outcome", _NON_VALID)
def test_flag_reason_carries_the_outcome_token_and_the_detail(outcome: SignatureOutcome) -> None:
    flag = review_flags_for(SignatureVerdict(outcome, "detail")).open_of(
        ReviewFlagType.CFDI_SIGNATURE
    )[0]
    assert flag.reason == f"cfdi signature {outcome.value}: detail"
    assert flag.state is ReviewFlagState.OPEN


def test_reason_without_a_detail_stays_deterministic() -> None:
    assert reason_for(SignatureVerdict(SignatureOutcome.ABSENT)) == "cfdi signature absent"
    assert reason_for(SignatureVerdict(SignatureOutcome.ABSENT, "   ")) == "cfdi signature absent"


def test_signature_flag_does_not_touch_other_flag_types() -> None:
    flags = review_flags_for(SignatureVerdict(SignatureOutcome.INVALID, "closed material"))
    for other in (
        ReviewFlagType.UNSUPPORTED_CURRENCY,
        ReviewFlagType.PERSPECTIVE_MISMATCH,
        ReviewFlagType.PERSPECTIVE_UNDETERMINED,
    ):
        assert flags.has_open(other) is False


def test_signature_flag_composes_additively_with_existing_flags() -> None:
    existing = ReviewFlags().with_flag(ReviewFlag(ReviewFlagType.UNSUPPORTED_CURRENCY, "EUR"))
    added = review_flags_for(SignatureVerdict(SignatureOutcome.ABSENT, "no sello"))
    merged = existing.with_flag(added.open_of(ReviewFlagType.CFDI_SIGNATURE)[0])
    assert merged.count(ReviewFlagType.UNSUPPORTED_CURRENCY) == 1
    assert merged.count(ReviewFlagType.CFDI_SIGNATURE) == 1
    unchanged = merged.open_of(ReviewFlagType.UNSUPPORTED_CURRENCY)[0]
    assert unchanged.reason == "EUR"
    assert unchanged.state is ReviewFlagState.OPEN


def test_verdict_is_frozen_and_reports_validity() -> None:
    verdict = SignatureVerdict(SignatureOutcome.VALID)
    assert verdict.is_valid is True
    assert verdict.detail == ""
    with pytest.raises(FrozenInstanceError):
        verdict.detail = "mutated"


def test_only_an_established_valid_outcome_is_valid() -> None:
    assert SignatureVerdict(SignatureOutcome.UNSUPPORTED).is_valid is False
    assert SignatureVerdict(SignatureOutcome.ABSENT, "no sello").is_valid is False
    assert SignatureVerdict(SignatureOutcome.VALIDATION_ERROR, "no sello").is_valid is False
