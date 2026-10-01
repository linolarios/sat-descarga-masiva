"""Accounting engine — turns fiscal documents into posted journal entries (§8).

Boundary (§4, §8a):

- depends on ``domain`` and ``application`` only; ``fiscal/`` is a sibling, never
  an import — a rule reads ``domain.model.fiscal_document.FiscalDocument``;
- never imports ``infrastructure``: no ``sqlite3``, ``openpyxl``, ``lxml``,
  ``satcfdi``, ``requests``;
- speaks ``AccountRole``s, never account numbers: the accounts come from the
  versioned mapping YAML behind the ``MappingProvider`` port, so no rule can name
  one — mapping stays a human-owned, versioned decision;
- amounts are ``Decimal`` through ``domain.policy.money.MoneyPolicy`` (§12).

Step 0: package skeleton only. The vocabularies (``roles.py``), the journal model,
the ``PostingEligibilityValidator`` and the rules land in the following units. The
boundary guards for all of the above live in
``tests/unit/test_m3_contabilidad_boundaries.py``.
"""
