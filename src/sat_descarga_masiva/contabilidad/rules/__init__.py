"""Rules 4.x — pure functions that propose; the validator decides (§8:159).

``contract`` holds what a rule reads (`PostingContext`) and the only two answers it may
give (`ProposedJournalEntry`, or `Skip`); ``scope`` holds §8:161's exhaustive ``SKIPPED``
set. The rules that post arrive one at a time, each with golden and mutation tests (§12).

Conventions every rule follows:

- a rule is **role-typed**: it names `AccountRole`s, never account numbers — turning a
  role into an account is the versioned mapping's job (§8:196, §8a:207);
- a rule **cannot** assign a posting state: ``POSTED`` is not in this package's
  vocabulary, because the `PostingEligibilityValidator` owns that word (§8:159);
- amounts are `Decimal` through `MoneyPolicy` (§4/§12), and a proposal balances
  (Debe == Haber) before it leaves the rule — the gate is checked again before commit;
- ``rule_id`` is the §8 number (``4.1``…``4.15``) and already encodes the perspective,
  so no entry carries a separate perspective field (§8:194);
- ``rule_version`` changes only when a rule's decision changes: it is part of the posting
  fingerprint, so a historical entry stays explainable years later (§8:194/195).
"""
