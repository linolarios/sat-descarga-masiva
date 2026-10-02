"""M3 step 3: the rule contract — what a rule reads and the two answers it may give.

§8:173 fixes a rule's shape (preconditions → inputs → calculation → journal lines →
review/skip conditions) and §8:159 fixes who decides: **a rule proposes; the
`PostingEligibilityValidator` decides**. These tests pin the half of that sentence a rule
*cannot* express, because that is where the safety lives:

- no vocabulary for `POSTED`: the outcomes add no state field, and
  `ProposedJournalEntry.posting_state` is derived and always `PROPOSED`;
- a `Skip` refuses legs — "deliberately outside accounting scope" and "posts nothing" are
  one statement (§8:161), so the contradictory combination is not constructible;
- a `Skip` refuses to be silent: its prose is for the log while the machine-readable
  reason is the rule id, so a reworded sentence can never change a decision;
- a `PostingContext` is frozen and closed, so a rule cannot accumulate state between
  documents (no clock, no repository, no client aggregate) and two runs over the same
  facts cannot disagree.
"""

from dataclasses import FrozenInstanceError, fields
from datetime import date
from decimal import Decimal

import pytest

from sat_descarga_masiva.contabilidad.journal import (
    JournalLine,
    LineSide,
    PostingState,
    ProposedJournalEntry,
)
from sat_descarga_masiva.contabilidad.roles import AccountRole
from sat_descarga_masiva.contabilidad.rules.contract import PostingContext, ReviewRequest, Skip
from sat_descarga_masiva.domain.model.fiscal_document import FiscalDocument, Impuestos
from sat_descarga_masiva.domain.model.perspective import Perspective
from sat_descarga_masiva.domain.model.review import ReviewFlag, ReviewFlagType
from sat_descarga_masiva.domain.model.value_objects import Rfc, Uuid
from sat_descarga_masiva.domain.policy.money import NormalizedAmount

CONTRIBUTOR = Rfc("AAA010101AAA")
UUID = Uuid("4e80345d-917f-40bb-a98f-4a73939353c5")
SOURCE_HASH = "a" * 64
LEG = JournalLine(
    account_role=AccountRole.CLIENTES,
    line_key="total",
    side=LineSide.DEBE,
    amount=NormalizedAmount(Decimal("116.00")),
)


def _entry(*, lines: tuple[JournalLine, ...] = ()) -> ProposedJournalEntry:
    """A rule's proposal: zero lines unless a test supplies legs."""
    return ProposedJournalEntry(
        rule_id="4.12",
        rule_version="1",
        contributor_rfc=CONTRIBUTOR,
        source_uuid=UUID,
        source_hash=SOURCE_HASH,
        entry_date=date(2026, 1, 15),
        lines=lines,
    )


def _context() -> PostingContext:
    return PostingContext(
        contributor_rfc=CONTRIBUTOR,
        document=FiscalDocument(
            tipo="I",
            version="4.0",
            moneda="MXN",
            tipo_cambio=None,
            emisor_rfc=CONTRIBUTOR,
            receptor_rfc=Rfc("BBB010101BBB"),
            conceptos=(),
            impuestos=Impuestos(),
            total=None,
            source_hash=SOURCE_HASH,
            source_uuid=UUID,
        ),
        perspective=Perspective.EMITIDO,
    )


def test_a_skip_wraps_the_entry_the_rule_would_have_posted() -> None:
    """The skip is provenance-complete: the same entry type, with no legs (§8:169/194)."""
    entry = _entry()
    skip = Skip(entry=entry, detail="tipo T carries no financial operation")
    assert skip.entry is entry
    assert skip.entry.posting_state is PostingState.PROPOSED


def test_a_skip_with_legs_is_unrepresentable() -> None:
    """§8:161: "deliberately out of scope" and "has journal lines" cannot both hold."""
    with pytest.raises(ValueError, match="cannot have legs"):
        Skip(entry=_entry(lines=(LEG,)), detail="tipo T carries no financial operation")


def test_a_skip_refuses_a_blank_explanation() -> None:
    """A silent skip would be indistinguishable from a document nobody looked at."""
    with pytest.raises(ValueError, match="must say why"):
        Skip(entry=_entry(), detail="   ")


def test_the_context_is_frozen() -> None:
    """A rule cannot mutate its input, so no rule can leak state into the next document."""
    context = _context()
    with pytest.raises(FrozenInstanceError):
        context.perspective = Perspective.RECIBIDO  # type: ignore[misc]


def test_the_context_is_exactly_the_books_the_document_and_the_perspective() -> None:
    """The closed input set (§8:194): no clock, no repository, no client aggregate."""
    assert [field.name for field in fields(PostingContext)] == [
        "contributor_rfc",
        "document",
        "perspective",
    ]


# --- the third answer: a rule that cannot compute says so (§8:173) --------------------


def test_a_review_request_wraps_the_entry_it_could_not_post() -> None:
    """§8a:212: a rule may propose a correction *and* refuse to post it — both facts are kept."""
    review = ReviewRequest(
        flags=(ReviewFlag(ReviewFlagType.REP_INVARIANT_VIOLATION, "saldo mismatch"),),
        detail="the payment does not reconcile with its original",
        entry=_entry(lines=(LEG,)),
    )
    assert review.entry is not None and review.entry.lines == (LEG,)
    assert review.entry.posting_state is PostingState.PROPOSED


def test_a_review_request_without_an_entry_flags_the_document() -> None:
    """§8:167: an undatable, unidentified document is flagged on itself, not on a fake entry."""
    review = ReviewRequest(
        flags=(ReviewFlag(ReviewFlagType.MISSING_POSTING_IDENTITY, "no TFD UUID"),),
        detail="the document carries no identity to key an entry by",
    )
    assert review.entry is None


def test_a_review_request_must_name_a_reason() -> None:
    """§8:173: an unmet precondition is a *reason*; silence would be a rule that stopped."""
    with pytest.raises(ValueError, match="must name why"):
        ReviewRequest(flags=(), detail="something is off")


def test_a_review_request_must_say_what_it_could_not_decide() -> None:
    with pytest.raises(ValueError, match="must say what"):
        ReviewRequest(flags=(ReviewFlag(ReviewFlagType.AMBIGUOUS_FX, "no rate"),), detail="   ")
