"""M3 step 1: review state attaches to entries too, and §8's flag set is approved (§8).

Review state is additive and orthogonal to posting state: a flag says a human must
decide, never that something posted. This file pins the vocabulary the validator
and the rules will raise, and proves the M2 values it extends are untouched —
they are persisted TEXT, so a rename would silently orphan stored facts.
"""

from datetime import datetime

from sat_descarga_masiva.domain.model.review import (
    ReviewFlag,
    ReviewFlagRecord,
    ReviewFlags,
    ReviewFlagType,
    ReviewSubjectKind,
)

#: M2's values, unchanged: the parser, perspective and signature producers.
M2_FLAGS = {
    "UNSUPPORTED_CURRENCY": "unsupported_currency",
    "PERSPECTIVE_MISMATCH": "perspective_mismatch",
    "PERSPECTIVE_UNDETERMINED": "perspective_undetermined",
    "CFDI_SIGNATURE": "cfdi_signature",
}

#: The M3 additions: validator refusals, rule-declared conditions, ⚠-gated paths.
M3_FLAGS = {
    "UNSUPPORTED_RULE": "unsupported_rule",
    "MISSING_SOURCE_FIELD": "missing_source_field",
    "MISSING_POSTING_IDENTITY": "missing_posting_identity",
    "UNMAPPED_ACCOUNT": "unmapped_account",
    "UNBALANCED_ENTRY": "unbalanced_entry",
    "AMBIGUOUS_FX": "ambiguous_fx",
    "MISSING_REP_ORIGINAL": "missing_rep_original",
    "REP_INVARIANT_VIOLATION": "rep_invariant_violation",
    "PAYROLL_DRAFT_UNSUPPORTED": "payroll_draft_unsupported",
    "RETENCION_DRAFT_UNSUPPORTED": "retencion_draft_unsupported",
    "INELIGIBLE_SOURCE_STATE": "ineligible_source_state",
    "IEPS_CREDITABLE_UNCONFIRMED": "ieps_creditable_unconfirmed",
    "EGRESO_IVA_EVIDENCE_MISSING": "egreso_iva_evidence_missing",
    "FX_DIFFERENCE_UNCONFIRMED": "fx_difference_unconfirmed",
    "PARTIAL_PAYMENT_REVERSAL_UNSPECIFIED": "partial_payment_reversal_unspecified",
}


def test_the_m2_flag_values_are_unchanged() -> None:
    """Persisted TEXT: renaming one would orphan every stored review fact."""
    assert {flag.name: flag.value for flag in ReviewFlagType if flag.name in M2_FLAGS} == M2_FLAGS


def test_the_m3_flags_are_the_approved_set() -> None:
    """The vocabulary only grows — and only by the approved names."""
    m3 = {flag.name: flag.value for flag in ReviewFlagType if flag.name not in M2_FLAGS}
    assert m3 == M3_FLAGS


def test_every_flag_value_is_the_lowercased_name() -> None:
    """The stored word and the code word stay the same word (§8's readability)."""
    for flag in ReviewFlagType:
        assert flag.value == flag.name.lower()


def test_a_regimen_condition_is_deferred_not_stubbed() -> None:
    """§8a: no M3 rule branches on régimen, so nothing could raise one yet."""
    assert not hasattr(ReviewFlagType, "REGIMEN_TREATMENT_UNDEFINED")


def test_an_entry_is_a_review_subject_kind() -> None:
    """§8: review state attaches to entries as well as to documents."""
    assert ReviewSubjectKind.DOCUMENT == "document"
    assert ReviewSubjectKind.JOURNAL_ENTRY == "journal_entry"
    assert [kind.name for kind in ReviewSubjectKind] == ["DOCUMENT", "JOURNAL_ENTRY"]


def test_the_new_vocabulary_round_trips_through_the_persisted_text_column() -> None:
    """`sqlite.py` rebuilds these enums from TEXT, so the stored words must resolve."""
    assert ReviewFlagType("unmapped_account") is ReviewFlagType.UNMAPPED_ACCOUNT
    assert ReviewSubjectKind("journal_entry") is ReviewSubjectKind.JOURNAL_ENTRY


def test_review_state_can_be_opened_against_a_journal_entry() -> None:
    """The entry-level form of the same additive fact: opened, not yet closed."""
    flags = ReviewFlags().with_flag(ReviewFlag(ReviewFlagType.UNMAPPED_ACCOUNT, "no mapping"))
    record = ReviewFlagRecord(
        flag_id=7,
        subject_kind=ReviewSubjectKind.JOURNAL_ENTRY,
        subject_id="fingerprint",
        flag=flags.open_flags()[0],
        occurred_at=datetime(2026, 1, 31, 12, 0),
    )
    assert record.closes_flag_id is None
    assert record.subject_kind is ReviewSubjectKind.JOURNAL_ENTRY
    assert flags.has_open(ReviewFlagType.UNMAPPED_ACCOUNT) is True


def test_a_gated_flag_is_still_only_review_state() -> None:
    """§8a ⚠: flagging never posts — the flag carries no posting authority."""
    flags = ReviewFlags().with_flag(ReviewFlag(ReviewFlagType.IEPS_CREDITABLE_UNCONFIRMED, "⚠"))
    assert flags.open_flags()[0].flag_type is ReviewFlagType.IEPS_CREDITABLE_UNCONFIRMED
    # §8: posting state is {PROPOSED, POSTED, SKIPPED} and is the validator's alone —
    # so no posting state may be spelled as a review flag.
    assert not {flag.value for flag in ReviewFlagType} & {"proposed", "posted", "skipped"}
