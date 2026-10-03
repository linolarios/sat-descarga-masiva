"""The `PostingEligibilityValidator` — the only thing in the engine that may say `POSTED` (§8:159).

**A rule proposes; the validator decides.** §8:159 makes `POSTED` require *all* of: source
state eligible at posting time · all required source fields present · deterministic account
mapping · deterministic FX valuation · valid `Decimal` amounts · balanced `Debe == Haber` ·
no unresolved review flags · supported rule · `source_uuid` + `source_hash` present ·
`rule_version`/`policy_version`/`mapping_version` recorded. **Any failure ⇒ `NEEDS_REVIEW`**,
and this class is where that sentence is implemented: `decide()` collects the failing
conditions as open `ReviewFlag`s and leaves the entry ``PROPOSED``, so a refusal always says
why (§8:167) and never mutates anything (§8:166).

Three deliberate properties:

- **fail closed, always.** The default answer is ``PROPOSED`` + a reason. `POSTED` is the
  only state that requires proof, so an unknown document type, an unbuilt rule, a currency
  with no resolved rate or a status nobody has looked up all end in a human's queue.
- **`SKIPPED` and `NEEDS_REVIEW` are the rules' answers, not this class's judgments.** A
  `Skip` is honoured — and *only* for a §8:161 out-of-scope rule id, so a fabricated skip is
  refused rather than persisted as a decision. A `ReviewRequest` becomes ``PROPOSED`` with the
  flags the rule declared, **even when its entry happens to balance**: a rule that asks for
  review can never be posted by passing a validator.
- **balance is checked twice.** Here is the first check — an unbalanced proposal is
  ``UNBALANCED_ENTRY`` + `NEEDS_REVIEW`. The second check belongs to the ledger commit and
  *aborts* instead of flagging (defense in depth): by then a human has been asked once
  already, and a commit path that silently dropped the balance gate would be a bug, not a
  review case.

The fiscal state this validator sees is what the fiscal layer has: §8:194's cancellation
handling joins a `MetadataSnapshot`. §8:159 requires the source to be **vigente** at posting
time, and the engine's golden rule is that nothing uncertain is posted — so condition 1 passes
only on a resolved ``VIGENTE``. A known cancellation fails it (`INELIGIBLE_SOURCE_STATE`), and
a status nobody has resolved — ``UNKNOWN``, the fiscal layer's only value until the metadata
join exists — fails it too (`UNKNOWN_SOURCE_STATE`). §6:123's live-check fallback is to the
latest *known* metadata observation, never a presumption that an unread status is vigente. A
cancellation discovered after a posting is 4.15's compensating entry, never an edit
(§8:166/194).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

from sat_descarga_masiva.application.ports.accounting import AccountMapping, MappingProvider
from sat_descarga_masiva.contabilidad.journal import (
    JournalEntryRecord,
    JournalLineRecord,
    PostingState,
    ProposedJournalEntry,
)
from sat_descarga_masiva.contabilidad.roles import AccountRole
from sat_descarga_masiva.contabilidad.rules.contract import PostingContext, ReviewRequest, Skip
from sat_descarga_masiva.contabilidad.rules.scope import OUT_OF_SCOPE_RULES
from sat_descarga_masiva.domain.model.fiscal_document import FiscalDocumentStatus
from sat_descarga_masiva.domain.model.review import ReviewFlag, ReviewFlagType

#: The engine's monetary/valuation policy version. §8:159 records it with every posting (it
#: travels to `posting_snapshot.policy_version`), so a posting stays explainable against the
#: policy that produced it.
POLICY_VERSION = "1"

#: The one currency this engine can value without an FX decision (§8:193). Anything else needs
#: a resolved rate — source-document → REP → configured → Banxico — and that resolution does
#: not exist yet, so a foreign-currency document is `AMBIGUOUS_FX` rather than quietly valued
#: at the source's own rate.
_NATIONAL_CURRENCY = "MXN"

#: §8:161's out-of-scope rule ids: the only `Skip`s the engine may record as `SKIPPED`.
_SKIP_RULE_IDS = frozenset(rule.rule_id for rule in OUT_OF_SCOPE_RULES)


#: The one-word refusal helper every condition below shares, so each flag's reason is written
#: exactly where the condition is evaluated.
def _flag(flag_type: ReviewFlagType, reason: str) -> ReviewFlag:
    return ReviewFlag(flag_type=flag_type, reason=reason)


@dataclass(frozen=True)
class PostingDecision:
    """What the validator decided about one document, and on what evidence (§8:159/166).

    ``posting_state`` is the verdict, assigned once here and never rewritten: ``POSTED``
    (every §8:159 condition held), ``SKIPPED`` (the rule declared the document deliberately
    out of scope) or ``PROPOSED`` (a human must decide — a refusal, in §8:167's words). The
    invariants below are that sentence in code, so a decision that contradicts itself cannot
    be constructed by any caller:

    - ``POSTED`` needs the entry it posted, with its legs, no open flag, a balanced entry and
      a resolved account for **every** leg (§8:158/159);
    - ``SKIPPED`` posts nothing, resolves nothing and raises nothing: §8:161's skip needs no
      human decision, which is exactly why it cannot carry an open flag;
    - ``PROPOSED`` always carries at least one open flag. "Could not post" is a *reason*, and
      a reason-less refusal would be indistinguishable from an entry nobody validated.

    ``entry`` is ``None`` only when the document itself could not be keyed — no `Fecha` to
    date it by (§8a:209) or no TFD UUID (§8:194). The review fact then attaches to the
    *document* (§8:167), and inventing either would be worse than asking.
    """

    entry: ProposedJournalEntry | None
    posting_state: PostingState
    mapping_version: str
    policy_version: str
    review_flags: tuple[ReviewFlag, ...] = ()
    accounts: Mapping[AccountRole, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.mapping_version.strip():
            raise ValueError(
                "§8:194: a decision without a mapping version cannot key the posting it names"
            )
        if not self.policy_version.strip():
            raise ValueError("§8:159: the policy version is recorded with every posting")
        if self.posting_state is PostingState.POSTED:
            self._check_posted()
        elif self.posting_state is PostingState.SKIPPED:
            if self.entry is None or self.entry.lines:
                raise ValueError("§8:161: a skip posts nothing, so it carries no legs")
            if self.review_flags:
                raise ValueError("§8:161: a skip needs no human decision, so no open flag")
            if self.accounts:
                raise ValueError("a skip consumes no account")
        elif not self.review_flags:
            raise ValueError(
                "§8:167: NEEDS_REVIEW always carries a reason — a PROPOSED decision without an"
                " open flag says nothing about why nothing was posted"
            )

    def _check_posted(self) -> None:
        if self.entry is None or not self.entry.lines:
            raise ValueError("§8:159: POSTED needs the entry it posted, with its legs")
        if self.review_flags:
            raise ValueError("§8:159: POSTED requires no unresolved review flag")
        if not self.entry.is_balanced():
            raise ValueError("§8:158: an unbalanced entry is never POSTED")
        unmapped = {line.account_role for line in self.entry.lines} - set(self.accounts)
        if unmapped:
            raise ValueError(f"§8:159: POSTED resolves every leg; unresolved: {unmapped}")

    def account_for(self, role: AccountRole) -> str | None:
        """The account a leg was posted to, or ``None`` when nothing was posted for it."""
        return self.accounts.get(role)

    def to_record(self, *, recorded_at: datetime) -> JournalEntryRecord:
        """This decision as the durable record a ``JournalEntryStore`` accepts (§8:166).

        The projection lives beside the verdict it records, not in an adapter: both stores
        then receive the same aggregate, and none of them gets to decide what a decision
        "really" was. The entry's §8:194 fingerprint *is* the ``entry_key`` — recomputed
        from the decision's own mapping version, so the key and the versions it names
        cannot drift apart — and each leg carries the account this decision resolved
        (§8a:207), or ``None`` when it resolved nothing (which is why an unresolved leg on
        a ``POSTED`` decision cannot be turned into a record at all: ``JournalEntryRecord``
        refuses it, and this class never produces it in the first place).

        A decision that names no entry is not recordable: ``entry is None`` means the
        document itself could not be keyed (§8a:209's missing date, or no TFD UUID), and
        §8:167 attaches that review fact to the *document* — there is no §8:194 identity to
        append under, and inventing one is what that rule exists to prevent.
        """
        if self.entry is None:
            raise ValueError(
                "§8:167: a decision that names no entry has nothing to record — without"
                " source_uuid + entry_date there is no §8:194 identity to append under"
            )
        source_uuid = self.entry.source_uuid
        if source_uuid is None:
            raise ValueError(
                "§8:194: an entry without a source_uuid has no fingerprint to be keyed by,"
                " so it cannot be recorded (§8:167 flags the document instead)"
            )
        return JournalEntryRecord(
            entry_key=self.entry.fingerprint(self.mapping_version).canonical(),
            contributor_rfc=self.entry.contributor_rfc,
            source_uuid=source_uuid,
            source_hash=self.entry.source_hash,
            rule_id=self.entry.rule_id,
            rule_version=self.entry.rule_version,
            mapping_version=self.mapping_version,
            policy_version=self.policy_version,
            posting_state=self.posting_state,
            entry_date=self.entry.entry_date,
            recorded_at=recorded_at,
            assumptions=self.entry.assumptions,
            lines=tuple(
                JournalLineRecord(
                    account_role=line.account_role,
                    line_key=line.line_key,
                    ordinal=ordinal,
                    side=line.side,
                    amount=line.amount,
                    resolved_account=self.account_for(line.account_role),
                )
                for ordinal, line in enumerate(self.entry.lines)
            ),
        )


class PostingEligibilityValidator:
    """Implements §8:159's conjunctive gate over one `PostingContext` and one rule answer.

    ``supported_rules`` is the engine's own registry — ``rule_id`` → current ``rule_version``
    (``rules.posting.POSTING_RULES``) — injected like the mapping is, because "which rules this
    engine can post" is a wiring decision, and this class must be able to refuse a rule id or a
    version its caller does not stand behind.
    """

    def __init__(
        self,
        mapping: MappingProvider,
        *,
        supported_rules: Mapping[str, str],
        policy_version: str = POLICY_VERSION,
    ) -> None:
        if not policy_version.strip():
            raise ValueError("§8:159 requires a policy version to record with each posting")
        self._mapping = mapping
        self._supported_rules = dict(supported_rules)
        self._policy_version = policy_version

    def decide(
        self,
        request: PostingContext,
        proposal: ProposedJournalEntry | Skip | ReviewRequest,
        *,
        mapping: AccountMapping | None = None,
    ) -> PostingDecision:
        """The verdict for this document's rule answer: `POSTED`, `SKIPPED` or `PROPOSED`.

        The client's mapping is resolved first — once, for the whole decision — because it is
        what versions the outcome (§8:194) and the only way a role becomes an account
        (§8a:207). A client with no mapping configured is a *configuration* failure that
        raises (`MappingNotConfigured`) rather than a per-document review case: it would fail
        identically for every document the client owns, so pretending otherwise would bury one
        fixable mistake under thousands of flags.

        ``mapping`` lets a caller that has *already* resolved the client's chart hand that same
        object in, so one document's decision is versioned by exactly one mapping and a run
        pays for the YAML once (§8a:207). It is optional and keyword-only, and passing nothing
        simply means this class does the resolving — either way the object the outcome names is
        the one the port answered with, never a second read that could disagree.
        """
        resolved = (
            mapping if mapping is not None else self._mapping.mapping_for(request.contributor_rfc)
        )
        if isinstance(proposal, Skip):
            return self._skip(proposal, resolved)
        if isinstance(proposal, ReviewRequest):
            return self._review(proposal, resolved)
        return self._post(request, proposal, resolved)

    def _skip(self, skip: Skip, mapping: AccountMapping) -> PostingDecision:
        """§8:161: a skip is honoured — for an out-of-scope rule id, and only for one."""
        if skip.entry.rule_id not in _SKIP_RULE_IDS:
            return self._refuse(
                skip.entry,
                mapping,
                (
                    _flag(
                        ReviewFlagType.UNSUPPORTED_RULE,
                        f"rule {skip.entry.rule_id!r} is not one of §8:161's out-of-scope rules:"
                        " SKIPPED asserts no human decision is needed, so it cannot be claimed"
                        " by a rule the engine does not know",
                    ),
                ),
            )
        return PostingDecision(
            entry=skip.entry,
            posting_state=PostingState.SKIPPED,
            mapping_version=mapping.mapping_version,
            policy_version=self._policy_version,
        )

    def _review(self, review: ReviewRequest, mapping: AccountMapping) -> PostingDecision:
        """A rule's own refusal: its flags travel as declared, and §8:159 is not re-run.

        The rule already decided it must not post, so re-checking the conditions would only
        pile reasons onto a decision that is `NEEDS_REVIEW` regardless (§8:167). The entry
        stays ``PROPOSED`` even when it balances: a rule asking for review has said its
        calculation is not the whole treatment (§8a:212), and no validator may overrule that.
        """
        return PostingDecision(
            entry=review.entry,
            posting_state=PostingState.PROPOSED,
            mapping_version=mapping.mapping_version,
            policy_version=self._policy_version,
            review_flags=review.flags,
        )

    def _post(
        self,
        request: PostingContext,
        entry: ProposedJournalEntry,
        mapping: AccountMapping,
    ) -> PostingDecision:
        refusals = self._refusals(request, entry, mapping)
        if refusals:
            return self._refuse(entry, mapping, refusals)
        roles = {line.account_role for line in entry.lines}
        accounts = {
            role: account for role in roles if (account := mapping.resolve(role)) is not None
        }
        return PostingDecision(
            entry=entry,
            posting_state=PostingState.POSTED,
            mapping_version=mapping.mapping_version,
            policy_version=self._policy_version,
            accounts=accounts,
        )

    def _refuse(
        self,
        entry: ProposedJournalEntry,
        mapping: AccountMapping,
        flags: tuple[ReviewFlag, ...],
    ) -> PostingDecision:
        """`NEEDS_REVIEW`: the entry stays ``PROPOSED`` and the flags say why (§8:167)."""
        return PostingDecision(
            entry=entry,
            posting_state=PostingState.PROPOSED,
            mapping_version=mapping.mapping_version,
            policy_version=self._policy_version,
            review_flags=flags,
        )

    def _refusals(
        self,
        request: PostingContext,
        entry: ProposedJournalEntry,
        mapping: AccountMapping,
    ) -> tuple[ReviewFlag, ...]:
        """§8:159's ten conditions, in its own order, each contributing at most one reason.

        All of them are evaluated rather than the first failing one: a human clearing the entry
        wants every reason at once, and the order they appear in is §8:159's, so the list is
        deterministic for a given document.
        """
        document = request.document
        flags: list[ReviewFlag] = []

        # 1. source state eligible at posting time (§8:159: "source state eligible (vigente)").
        #    Only a resolved *VIGENTE* passes: a known cancellation is ineligible, and an
        #    unresolved status is no evidence of eligibility — nothing uncertain is posted — so
        #    it waits for the metadata join that would resolve it (§8:194/§6a) instead of being
        #    presumed alive. §6:123's live-check fallback is to the latest *known* observation,
        #    so a failed live check never turns an UNKNOWN into a posting either.
        if document.status is FiscalDocumentStatus.CANCELLED:
            flags.append(
                _flag(
                    ReviewFlagType.INELIGIBLE_SOURCE_STATE,
                    "the source is cancelled: a known-cancelled document is never posted"
                    " (§8:159/194)",
                )
            )
        elif document.status is not FiscalDocumentStatus.VIGENTE:
            flags.append(
                _flag(
                    ReviewFlagType.UNKNOWN_SOURCE_STATE,
                    f"the source state is {document.status.value!r}, not 'vigente': §8:159"
                    " requires an eligible source at posting time, and nothing uncertain is"
                    " posted — resolve the metadata snapshot before posting (§8:194/§6a)",
                )
            )
        # 2. all required source fields present. §8:173 makes the fields a rule's own
        #    precondition — a rule that cannot compute says so with a `ReviewRequest` and a
        #    reason — so what is left here is the structural half: a posting rule that proposed
        #    no legs did not have the fields its calculation needs.
        if not entry.lines:
            flags.append(
                _flag(
                    ReviewFlagType.MISSING_SOURCE_FIELD,
                    f"rule {entry.rule_id} proposed no legs: the source fields its calculation"
                    " needs are not all present",
                )
            )
        # 3. deterministic account mapping (§8a:207): every role of every leg resolves, or the
        #    entry is not bookable. Never a default account.
        unmapped = mapping.unresolved(line.account_role for line in entry.lines)
        if unmapped:
            named = "/".join(role.value for role in unmapped)
            flags.append(
                _flag(
                    ReviewFlagType.UNMAPPED_ACCOUNT,
                    f"mapping {mapping.mapping_version} names no account for {named}",
                )
            )
        # 4. deterministic FX valuation (§8:193). Only the national currency is valuated without
        #    an FX decision; a foreign-currency document awaits the resolution §8:193 defines
        #    (and the ⚠ contador ruling behind it), so it is never valued at a rate the engine
        #    picked on its own.
        if document.moneda.upper() != _NATIONAL_CURRENCY:
            flags.append(
                _flag(
                    ReviewFlagType.AMBIGUOUS_FX,
                    f"currency {document.moneda!r} has no resolved FX rate: §8:193's priority"
                    " chain is not implemented yet",
                )
            )
        # 5. valid Decimal amounts (§4/§12). Construction already refuses a non-positive or
        #    non-finite leg, so this is the second pair of eyes §8:159 asks for.
        if any(not line.amount.amount.is_finite() for line in entry.lines):
            flags.append(
                _flag(
                    ReviewFlagType.INVALID_AMOUNT,
                    "a leg carries a non-finite amount: a corrupt source amount is never posted"
                    " (§12)",
                )
            )
        # 6. balanced Debe == Haber (§8:158). The first of the two checks; the pre-commit check
        #    aborts, which is the commit path's job, not a review reason.
        if not entry.is_balanced():
            flags.append(
                _flag(
                    ReviewFlagType.UNBALANCED_ENTRY,
                    f"Debe {entry.debe_total()} != Haber {entry.haber_total()}",
                )
            )
        # 7. no unresolved review flags: the document's own M2 flags (signature, currency,
        #    perspective) are review state that outranks any posting decision (§8:161).
        flags.extend(document.review_flags.open_flags())
        # 8./10. supported rule, and the rule version that produced this entry. The registry
        #    carries the *current* version, so a stale or unknown rule_version is not the
        #    decision the engine stands behind — and it is part of what the posting is keyed by
        #    (§8:194). `mapping_version`/`policy_version` are recorded by construction: the
        #    mapping cannot exist without a version, and neither can this validator.
        if self._supported_rules.get(entry.rule_id) != entry.rule_version:
            flags.append(
                _flag(
                    ReviewFlagType.UNSUPPORTED_RULE,
                    f"rule {entry.rule_id!r} version {entry.rule_version!r} is not a supported"
                    " posting rule of this engine (§8:159)",
                )
            )
        # 9. source_uuid + source_hash present: §8:194 keys the posting by them, so without them
        #    there is no identity to record an accounting fact under.
        if entry.source_uuid is None or not entry.source_hash.strip():
            flags.append(
                _flag(
                    ReviewFlagType.MISSING_POSTING_IDENTITY,
                    "source_uuid + source_hash are POSTED preconditions: without them the entry"
                    " has no §8:194 identity",
                )
            )
        return tuple(flags)
