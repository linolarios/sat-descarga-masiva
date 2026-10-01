"""Rules 4.11/4.12/4.14 — §8:161's exact ``SKIPPED`` set, and nothing beyond it.

``SKIPPED`` is the narrowest word in the engine: it asserts that **no human decision is
needed**. §8:161 therefore enumerates it exactly — ``T`` (traslado); ``N`` (payroll)
**recibido**; ``R`` (retenciones) **recibido**, the acuse that feeds DIOT. Everything
that merely looks similar is a refusal instead: ``N``/``R`` **emitido** are the payroll
and withholding *drafts* of §8a:205 (zero lines, ``NEEDS_REVIEW``), an undetermined
perspective is never silently skipped (§8:163), and any unsupported document type goes
to review (§8:161: "Unsupported is never silently ``SKIPPED``").

``OUT_OF_SCOPE_RULES`` is that enumeration as data and :func:`out_of_scope` is the only
door to a `Skip`, so the set cannot quietly grow: a new case means a new row in the table
with its own §8 rule id, versioned and covered by tests (§12).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sat_descarga_masiva.contabilidad.journal import ProposedJournalEntry
from sat_descarga_masiva.contabilidad.rules.contract import PostingContext, Skip
from sat_descarga_masiva.domain.enums.comprobante import TipoComprobante
from sat_descarga_masiva.domain.model.perspective import Perspective

#: Bumped when one of these rules' decisions changes. It travels in every entry's
#: fingerprint (§8:194), so a skip recorded today stays explainable against the rule
#: version that produced it.
RULE_VERSION = "1"

#: The two *decided* perspectives. UNDETERMINED is deliberately absent (§8:163): a skip
#: is a decision, and an undetermined perspective is precisely what cannot be decided.
_DECIDED = frozenset({Perspective.EMITIDO, Perspective.RECIBIDO})


@dataclass(frozen=True)
class OutOfScopeRule:
    """One §8:161 case: a document type (and perspective) that is not accounted.

    ``rule_id`` is the case's §8 number and is what the persisted entry carries, so a
    skip is auditable from the ledger alone — ``detail`` is for humans only. The rule is
    one row of data rather than a function because there is nothing to calculate: the
    accounting expression of an out-of-scope document is empty by definition.
    """

    rule_id: str
    tipo: TipoComprobante
    perspectives: frozenset[Perspective]
    detail: str

    def claims(self, tipo: TipoComprobante | None, perspective: Perspective) -> bool:
        """True when this case is the document's: same type, and a perspective it covers."""
        return tipo is self.tipo and perspective in self.perspectives

    def skip(self, request: PostingContext, entry_date: date) -> Skip:
        """The `Skip` for this case, dated on the document's own ``Fecha`` (§8a:209).

        ``entry_date`` is passed in rather than read here because the caller has already
        proven it exists: a skip has to be dated to be recordable, so
        :func:`out_of_scope` refuses an undatable document instead of constructing an
        entry it could never persist.
        """
        return Skip(
            entry=ProposedJournalEntry(
                rule_id=self.rule_id,
                rule_version=RULE_VERSION,
                contributor_rfc=request.contributor_rfc,
                source_uuid=request.document.source_uuid,
                source_hash=request.document.source_hash,
                entry_date=entry_date,
                lines=(),
            ),
            detail=self.detail,
        )


#: §8:161's SKIPPED set, complete. Order is the table's, so a scan enumerates 4.11, 4.12,
#: 4.14; the rules are disjoint (one type plus perspective set each), so order is not
#: load-bearing — but it stays deterministic for logs and tests.
OUT_OF_SCOPE_RULES: tuple[OutOfScopeRule, ...] = (
    OutOfScopeRule(
        rule_id="4.11",
        tipo=TipoComprobante.NOMINA,
        perspectives=frozenset({Perspective.RECIBIDO}),
        detail="tipo N recibido: a payroll receipt is deliberately not accounted (§8:161)",
    ),
    OutOfScopeRule(
        rule_id="4.12",
        tipo=TipoComprobante.TRASLADO,
        perspectives=_DECIDED,
        detail="tipo T: a traslado carries no financial operation (§8:161)",
    ),
    OutOfScopeRule(
        rule_id="4.14",
        tipo=TipoComprobante.RETENCIONES,
        perspectives=frozenset({Perspective.RECIBIDO}),
        detail="tipo R recibido: the retenciones acuse feeds DIOT instead (§8:161)",
    ),
)


def out_of_scope(request: PostingContext) -> Skip | None:
    """The ``SKIPPED`` decision for this document, or ``None`` when it is not a skip case.

    ``None`` deliberately covers every neighbour: the ``N``/``R`` EMITIDO drafts, an
    undetermined perspective, an unsupported document type — and a document that could
    not be **recorded** as a skip, i.e. one with no ``Fecha`` to date its entry by
    (§8a:209) or no TFD UUID to key its fingerprint by (§8:194, §11 M3's NOT NULL
    columns). All of those belong to the validator's refusal path (§8:158/159/161);
    none of them is a skip, and none is a crash.
    """
    document = request.document
    tipo = TipoComprobante.of(document.tipo)
    entry_date = document.fecha
    if tipo is None or entry_date is None or document.source_uuid is None:
        return None
    for rule in OUT_OF_SCOPE_RULES:
        if rule.claims(tipo, request.perspective):
            return rule.skip(request, entry_date)
    return None
