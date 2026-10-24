"""Rule 4.15 — a cancelled source, and the two answers §8:194 gives for it.

§8:194 states the temporal rule the whole cancellation path exists for: the fiscal status of a
source is resolved from the metadata observation, and a *later* cancellation never rewrites what
was posted. The two cases it names are different claims, so they are distinct rules here:

- **4.15a** — cancelled and never posted. There is no accounting fact to reverse, so nothing is
  booked and nothing is reversed: the entry is ``SKIPPED``, dated by the cancellation's own
  effective date. That is not a review case (§8:161: no human decision is needed), and it must
  never fabricate a reversal — nor a date: an undated cancellation has no key to record under, so
  it is refused on the document (§8a:218) rather than dated by the invoice's ``Fecha``.
- **4.15b** — cancelled after being posted. The original record is a permanent historical fact
  (§8:167/194), so the correction is a *new* compensating entry: every leg of the posted fact is
  inverted (DEBE to HABER, HABER to DEBE) with the same amount, the same account role and the
  same resolved account, dated by the cancellation effective date. The result carries a blocking
  review condition — ``CANCELLED_AFTER_POSTING`` — because an append-only reversal of a posted
  fact is a human decision before it becomes an accepted accounting fact (§8:189).

Two invariants keep the module narrow on purpose. **The reversal is computed from the persisted
record, not from the document**: no classification, no account mapping, no IVA or base
recalculation, so a chart that changed since the posting cannot rewrite history — the reversal
books the accounts the original was booked to. **The rule reads nothing**: the application layer
resolves the history and the cancellation date into `PostingContext`
(``previous_entries``/``cancellation_effective_on``), exactly as it does for the REP and
credit-note seams, so this module imports no store, no adapter and no I/O (§4).

Only ``POSTED`` history is a reversal candidate: a ``PROPOSED`` refusal or a ``SKIPPED`` row moved
nothing. A source with two ``POSTED`` records is refused loudly rather than guessed: §8:194 gives
one source one accounting fact, so *which* of the two a cancellation would reverse is an ambiguity
this rule declines to adjudicate (§8a:221). It neither picks a winner nor assumes the ledger
re-posted by itself — it stops and asks for the ruling.
"""

from __future__ import annotations

from sat_descarga_masiva.contabilidad.journal import (
    JournalEntryRecord,
    JournalLine,
    PostingState,
    ProposedJournalEntry,
)
from sat_descarga_masiva.contabilidad.roles import AccountRole
from sat_descarga_masiva.contabilidad.rules.contract import (
    PostingContext,
    Proposal,
    ReviewRequest,
    Skip,
    _flag,
)
from sat_descarga_masiva.domain.model.fiscal_document import FiscalDocumentStatus
from sat_descarga_masiva.domain.model.review import ReviewFlag, ReviewFlagType

#: The two outcomes of §8:194. They are separate rule ids because they are separate claims: a
#: skip moved nothing and a reversal moves an exact mirror, so they must never share an entry key.
RULE_4_15A = "4.15a"
RULE_4_15B = "4.15b"

#: §8:194 keys every posting by (rule_id, rule_version), so the two rows are versioned apart: a
#: change to one decision can never re-identify the other. 4.15a is v2 because an undated
#: cancellation is now refused on the document (§8a:218) instead of dated by the invoice's Fecha;
#: 4.15b's decision — legs, accounts, date provenance, flag — is unchanged.
RULE_VERSION_4_15A = "2"
RULE_VERSION_4_15B = "1"

#: The registry ids the §8:194 rules answer under.
CANCELLATION_RULE_VERSIONS = {RULE_4_15A: RULE_VERSION_4_15A, RULE_4_15B: RULE_VERSION_4_15B}

#: Prefix of every reversal line key. The stable historical identity of a leg is its own key, so
#: a retry derives the identical key and two distinct historical legs never collide.
REVERSAL_LINE_KEY_PREFIX = "reversal:"

#: The one cancellation skip this module may record. §8:161's SKIPPED set is exhaustive, so the
#: validator honours this id beside the out-of-scope table rather than widening the set by rule.
CANCELLATION_SKIP_RULE_IDS = frozenset({RULE_4_15A})


def cancellation(request: PostingContext) -> Proposal | None:
    """§8:194 for a cancelled source, or ``None`` when the source is not cancelled.

    ``None`` is the signal that this state override does not apply, so the ordinary
    shape-driven rows stay in charge of every other document: the cancellation policy never
    widens to a fiscal state it was not written for.
    """
    if request.document.status is not FiscalDocumentStatus.CANCELLED:
        return None
    posted = _posted_entries(request)
    if not posted:
        return _skip(request)
    return _reversal(request, posted[0])


def _posted_entries(request: PostingContext) -> tuple[JournalEntryRecord, ...]:
    """The reversal candidates: ``POSTED`` records only, and at most one of them.

    A ``PROPOSED`` refusal and a ``SKIPPED`` row moved nothing, so neither is a fact that a
    cancellation can undo. Two ``POSTED`` records for one source are not a policy question the
    engine may answer by picking one: §8:194 gives one source one accounting fact, so *which* of
    the two a cancellation would reverse is an ambiguity this rule declines to adjudicate
    (§8a:221). It does not claim a silent re-post happened — the cause is not adjudicated here —
    it refuses to name a reversal target it cannot justify.
    """
    posted = tuple(
        record for record in request.previous_entries if record.posting_state is PostingState.POSTED
    )
    if len(posted) > 1:
        keys = ", ".join(record.entry_key for record in posted)
        raise ValueError(
            f"§8:194: source {request.document.source_uuid} holds {len(posted)} POSTED"
            f" entries [{keys}]: one source has one accounting fact, so which of them a"
            " cancellation reverses is an ambiguity this rule declines to adjudicate — the"
            " target needs a human ruling rather than a first-wins pick (§8a:221)"
        )
    return posted


def _reversal(request: PostingContext, record: JournalEntryRecord) -> ReviewRequest:
    """Rule 4.15b: the posted fact, leg for leg, booked the other way round (§8:194).

    The arithmetic is the persisted record's, not the document's: every leg keeps its amount, its
    role and the account it was *posted* to (§8a:207), and only the side moves. Nothing is
    re-derived — no classification, no mapping, no IVA or base recalculation — so a chart that
    changed since the posting cannot rewrite history.

    The date is the cancellation's effective date and nothing else (§8a:203/209). Without one the
    reversal is not dated at all, and that refusal is attached to the *document* (§8:167): an entry
    with no date has no §8:194 identity to be keyed by.
    """
    effective_on = request.cancellation_effective_on
    if effective_on is None:
        return ReviewRequest(
            flags=(
                _flag(
                    ReviewFlagType.MISSING_SOURCE_FIELD,
                    f"source {record.source_uuid} holds a POSTED entry under {record.rule_id}"
                    f" v{record.rule_version} and the cancellation states no FechaCancelacion:"
                    " §8a:203 dates the reversal by the cancellation's effective date, never by"
                    " retrieved_at, by the invoice's Fecha or by today",
                ),
            ),
            detail="a cancellation with no effective date cannot date its reversal, so nothing"
            " is proposed rather than an entry dated by a guess",
        )
    return ReviewRequest(
        entry=ProposedJournalEntry(
            rule_id=RULE_4_15B,
            rule_version=RULE_VERSION_4_15B,
            contributor_rfc=request.contributor_rfc,
            source_uuid=record.source_uuid,
            source_hash=record.source_hash,
            entry_date=effective_on,
            lines=tuple(
                JournalLine(
                    account_role=leg.account_role,
                    line_key=f"{REVERSAL_LINE_KEY_PREFIX}{leg.line_key}",
                    side=leg.side.opposite,
                    amount=leg.amount,
                )
                for leg in sorted(record.lines, key=lambda line: line.ordinal)
            ),
        ),
        flags=(
            _flag(
                ReviewFlagType.CANCELLED_AFTER_POSTING,
                f"source {record.source_uuid} was posted under {record.rule_id}"
                f" v{record.rule_version} and the SAT cancelled it on {effective_on}: the exact"
                " compensating reversal is proposed and a human accepts it before it becomes an"
                " accounting fact",
            ),
        ),
        detail="an accounting fact the SAT later cancelled is corrected by an append-only"
        " reversal, never by rewriting the original posting (§8:166/194)",
        accounts=_historical_accounts(record),
    )


def _historical_accounts(record: JournalEntryRecord) -> dict[AccountRole, str]:
    """The accounts the original was posted to (§8a:207) — never today's chart.

    Keyed by role because the posting resolved one account per role: a ``POSTED`` record whose legs
    disagree about their role's account contradicts its own §8:159 verdict, so it is refused here
    rather than silently collapsed into whichever account came last.
    """
    accounts: dict[AccountRole, str] = {}
    for leg in record.lines:
        resolved = leg.resolved_account
        if resolved is None:
            raise ValueError(
                f"§8:159: POSTED record {record.entry_key} leaves {leg.line_key!r} unresolved,"
                " so there is no historical account to reverse"
            )
        known = accounts.get(leg.account_role)
        if known is not None and known != resolved:
            raise ValueError(
                f"§8a:207: {leg.account_role} was posted to two accounts in"
                f" {record.entry_key} ({known!r}, {resolved!r})"
            )
        accounts[leg.account_role] = resolved
    return accounts


def _skip(request: PostingContext) -> Skip | ReviewRequest:
    """Cancelled with nothing posted: ``SKIPPED``, and never a fabricated reversal (§8:194).

    The record is dated by the cancellation's own effective date and by nothing else (§8a:218):
    ``FiscalDocument.fecha`` is the *invoice's* date and says nothing about when the cancellation
    took effect, so an undated cancellation is handed to `_unkeyable` rather than dated by it.
    A dated cancellation whose document carries no ``Fecha`` still skips — the invoice's date is
    simply not a fact this rule reads.
    """
    document = request.document
    effective_on = request.cancellation_effective_on
    if effective_on is None or document.source_uuid is None:
        return _unkeyable(request)
    return Skip(
        entry=ProposedJournalEntry(
            rule_id=RULE_4_15A,
            rule_version=RULE_VERSION_4_15A,
            contributor_rfc=request.contributor_rfc,
            source_uuid=document.source_uuid,
            source_hash=document.source_hash,
            entry_date=effective_on,
            lines=(),
        ),
        detail="the source was cancelled before it was ever posted: there is no accounting"
        " fact to reverse, so the cancellation books nothing (§8:194)",
    )


def _unkeyable(request: PostingContext) -> ReviewRequest:
    """A cancellation that cannot be keyed is flagged on the document (§8a:218/§8:194).

    An entry is keyed by (source UUID, date, rule, version): without either half there is no key
    to record under, and inventing one is worse than flagging the document itself (§8:167). The
    date half is the *cancellation's* effective date — never the invoice's ``Fecha`` — so an
    undated cancellation is `MISSING_SOURCE_FIELD` even when the document is fully dated. Both
    reasons travel together when both halves are missing, so a human sees the whole refusal.
    """
    document = request.document
    flags: list[ReviewFlag] = []
    if request.cancellation_effective_on is None:
        flags.append(
            _flag(
                ReviewFlagType.MISSING_SOURCE_FIELD,
                f"source {document.source_uuid} was cancelled and the cancellation states no"
                " FechaCancelacion: §8a:218 dates a cancellation by its own effective date,"
                " never by the invoice's Fecha, by recorded_at, by retrieved_at or by today",
            )
        )
    if document.source_uuid is None:
        flags.append(
            _flag(
                ReviewFlagType.MISSING_POSTING_IDENTITY,
                "no TFD UUID: §8:194 keys the entry by it, so there is nothing to record",
            )
        )
    return ReviewRequest(
        flags=tuple(flags),
        detail="a cancellation decision without the identity an entry is keyed by (a source"
        " UUID and the cancellation's own effective date) is flagged on the document: nothing"
        " is recorded under an invented key",
    )
