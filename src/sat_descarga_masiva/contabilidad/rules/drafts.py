"""Rules 4.10/4.13 — the payroll and withholding *drafts* (§8a:205), and nothing beyond them.

`SKIPPED` (§8:161, in `scope.py`) is the narrowest word in the engine: it asserts that **no human
decision is needed**. A payroll (`N`) or withholding (`R`) comprobante the contributor **issued** is
the opposite — M3 parses no `nomina12` and no retenciones root, and has no payroll or retention
roles, so it cannot be accounted yet. §8a:205's answer is neither a skip nor a guess: it is a
`NEEDS_REVIEW` draft — an entry with **zero lines** plus a reason, so a human completes it (§8:173's
"preconditions unmet ⇒ `NEEDS_REVIEW`"). The **received** side is different and stays the skip it
always was: `N`/`R` **recibido** are `SKIPPED` as 4.11/4.14 (`scope.py`).

``DRAFT_RULES`` is that pair as data and :func:`draft` is the only door to a draft, mirroring
`scope.py`: a new case means a new row in the table with its own §8 rule id, versioned and covered
by tests (§12). A draft is keyed as an entry — by contributor, source TFD UUID, rule id/version and
the document's own `Fecha` (§8:169/194, §8a:209) — exactly like a skip, so it is auditable from the
ledger alone. It is a *refusal*, not a proposal: it keys the entry through `contract._review`, so a
document that cannot be dated or identified is flagged on the document itself rather than keyed by
an entry that could never persist.
"""

from __future__ import annotations

from dataclasses import dataclass

from sat_descarga_masiva.contabilidad.rules.contract import (
    PostingContext,
    ReviewRequest,
    _flag,
    _review,
)
from sat_descarga_masiva.domain.enums.comprobante import TipoComprobante
from sat_descarga_masiva.domain.model.perspective import Perspective
from sat_descarga_masiva.domain.model.review import ReviewFlagType

#: Bumped when one of these rules' decisions changes. It travels in every entry's fingerprint
#: (§8:194), so a draft recorded today stays explainable against the rule version that produced it.
RULE_VERSION = "1"

RULE_4_10 = "4.10"
RULE_4_13 = "4.13"


@dataclass(frozen=True)
class DraftRule:
    """One §8a:205 case: a document type (and perspective) whose treatment is a human's to finish.

    ``rule_id`` is the case's §8 number and is what the keyed entry carries, so a draft is
    auditable from the ledger alone; ``reason`` is the sentence a human reads in the flag and
    ``detail`` the headline on the review request (§8:167), so a reworded sentence can never change
    a decision. The rule carries ``rule_id``/``rule_version`` because that is what
    :func:`contract._review` needs to key the zero-line entry (`contract.EntryKey`), the same way a
    skip or a proposal is keyed. The rule is one row of data rather than a function because there is
    nothing to calculate — the accounting expression of a draft M3 cannot build is empty by
    definition.
    """

    rule_id: str
    rule_version: str
    tipo: TipoComprobante
    perspective: Perspective
    flag_type: ReviewFlagType
    reason: str
    detail: str

    def claims(self, tipo: TipoComprobante | None, perspective: Perspective) -> bool:
        """True when this case is the document's: same type, and the same perspective it covers."""
        return tipo is self.tipo and perspective is self.perspective

    def draft(self, request: PostingContext) -> ReviewRequest:
        """The zero-line `ReviewRequest` for this case, keyed as an entry when it can be.

        The entry's provenance — contributor, source TFD UUID, rule id/version — comes from
        ``request`` and ``self`` (``_review``), and the document's own `Fecha` dates it (§8a:209):
        a draft is a recorded fact, not a silence. When the document cannot be dated or identified
        there is nothing to key, so `_review` flags the document itself (§8:167).
        """
        return _review(
            request,
            self,
            flags=(_flag(self.flag_type, self.reason),),
            detail=self.detail,
        )


#: §8:188/190's two **emitido** drafts, and nothing else. Order is the table's, so a scan
#: enumerates 4.10, 4.13; the rules are disjoint (one type each), so order is not load-bearing —
#: but it stays deterministic for logs and tests.
DRAFT_RULES: tuple[DraftRule, ...] = (
    DraftRule(
        rule_id=RULE_4_10,
        rule_version=RULE_VERSION,
        tipo=TipoComprobante.NOMINA,
        perspective=Perspective.EMITIDO,
        flag_type=ReviewFlagType.PAYROLL_DRAFT_UNSUPPORTED,
        reason=(
            "tipo N emitido: M3 parses no nomina12 and has no payroll roles, so the payroll"
            " draft is a human's to complete (§8a:205)"
        ),
        detail="a payroll draft: zero lines now, `NEEDS_REVIEW` until a human accounts for it",
    ),
    DraftRule(
        rule_id=RULE_4_13,
        rule_version=RULE_VERSION,
        tipo=TipoComprobante.RETENCIONES,
        perspective=Perspective.EMITIDO,
        flag_type=ReviewFlagType.RETENCION_DRAFT_UNSUPPORTED,
        reason=(
            "tipo R emitido: the withholding draft is outside M3's rules and parser, so a human"
            " must complete it (§8a:205)"
        ),
        detail="a withholding draft: zero lines now, `NEEDS_REVIEW` until a human accounts for it",
    ),
)


def draft(request: PostingContext) -> ReviewRequest | None:
    """The draft decision for this document, or ``None`` when it is not a draft case.

    ``None`` deliberately covers every neighbour: the ``N``/``R`` **recibido** skips (§8:161,
    which `scope.out_of_scope` owns), an undetermined perspective (§8:163), the ``I``/``E``/``P``
    rows the posting rules own, and any unsupported document type (§8:161). All of those belong to
    another door; none of them is a draft, and none is a crash.
    """
    tipo = TipoComprobante.of(request.document.tipo)
    if tipo is None:
        return None
    for rule in DRAFT_RULES:
        if rule.claims(tipo, request.perspective):
            return rule.draft(request)
    return None
