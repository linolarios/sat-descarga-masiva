"""What every comprobante rule shares: its amounts, and why they cannot be booked (§8).

Two rule modules book two families of §8 row — `posting.py` the `I` rows (4.1–4.4) and
`credit_notes.py` the credit-note rows (4.6–4.9) — and both start from the same arithmetic:
`base = SubTotal − Descuento` under M3's `discount_policy = net` (§8a:204), one IVA leg
however many `002` traslados contribute to it, and `Total == base + IVA` as the gate that makes
the proposed entry balance. §8:173's preconditions are shared for the same reason: an absent
`SubTotal`/`Total`, an unreadable traslado, a retención or a non-IVA impuesto mean the same thing
whichever row asked, and a refusal must never be reworded per family.

This module is therefore **not** a second rule engine. It holds no row, no registry and no
`claims`: it is the arithmetic and the refusal vocabulary, extracted so that neither rule module
imports the other — `posting.py` appends `credit_notes.py`'s rows to the one registry, and the
rows themselves may not depend on the module that appends them. Nothing here reads a mapping, a
ledger or a clock: a rule stays the pure function of its `PostingContext` that makes it replayable.

The legs' structural keys travel with the arithmetic: §8:194 keys a leg by them, so every
rule in the family names the same legs (a money leg, a base, one IVA line) with the same words,
and a re-keyed leg cannot silently pass for a new fact.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sat_descarga_masiva.contabilidad.classification import ClassificationRefusalKind
from sat_descarga_masiva.contabilidad.rules.contract import (
    _PRECONDITION_DETAIL,
    EntryKey,
    PostingContext,
    ReviewRequest,
    _flag,
    _recordability_flags,
    _review,
)
from sat_descarga_masiva.domain.model.fiscal_document import FiscalDocument
from sat_descarga_masiva.domain.model.review import ReviewFlag, ReviewFlagType
from sat_descarga_masiva.domain.model.value_objects import Uuid
from sat_descarga_masiva.domain.policy.money import NormalizedAmount

#: One of §8's payment methods, as the CFDI writes it (`MetodoPago`). Never normalized: §8:179
#: and §8:180 are chosen by this exact word, so a document that does not carry one is reviewed.
METODO_PUE = "PUE"
METODO_PPD = "PPD"

#: `c_Impuesto` for IVA — the only traslado these rules book (§8:179–182).
IVA_IMPUESTO = "002"

#: `c_TipoFactor`'s exempt value: an exempt traslado moves no amount, so it books no leg.
EXENTO = "Exento"

#: The legs' structural keys: §8:179/180/181/182 name one money leg (the clearing account, a
#: receivable or a payable), one base and one IVA line, whatever the rates. §8:194 keys each leg
#: by these, so they stay structural — `ASSUMED_PUE` is evidence about the entry
#: (`AccountingAssumption`) and never appears here.
CLEARING_KEY = "clearing"
BASE_KEY = "base"
IVA_KEY = "iva"
TOTAL_KEY = "total"

#: Why a document that does not classify to one postable role is reviewed, as §8:167 flag types.
#: The refusal's ``kind`` is the machine-readable bit, so this map is total over
#: `ClassificationRefusalKind` and a reworded ``detail`` can never change a decision.
REFUSAL_FLAGS: Mapping[ClassificationRefusalKind, ReviewFlagType] = {
    ClassificationRefusalKind.MISSING_CLAVE_PROD_SERV: ReviewFlagType.MISSING_SOURCE_FIELD,
    ClassificationRefusalKind.UNCLASSIFIED_PRODUCT: ReviewFlagType.UNMAPPED_ACCOUNT,
    ClassificationRefusalKind.CAPITAL_GOODS: ReviewFlagType.UNSUPPORTED_RULE,
    ClassificationRefusalKind.MIXED_CATEGORIES: ReviewFlagType.UNSUPPORTED_RULE,
}


@dataclass(frozen=True)
class BaseAmounts:
    """The three amounts §8:175/179/180/181/182 book, once the document is proven to carry them.

    ``source_uuid``/``entry_date`` travel with them because they are what the posting is keyed
    and dated by (§8:194, §8a:209): proving they exist is part of the same precondition check,
    and handing them over narrowed is what stops a rule from inventing either.
    """

    source_uuid: Uuid
    entry_date: date
    base: NormalizedAmount
    iva: NormalizedAmount
    total: NormalizedAmount


def base_amounts(request: PostingContext, row: EntryKey) -> BaseAmounts | ReviewRequest:
    """§8:173's preconditions: what `base`, `IVA` and `Total` come from, or why they cannot.

    ``row`` is only carried so a refusal can be keyed by the rule that made it.
    """
    document = request.document
    entry_date, source_uuid = document.fecha, document.source_uuid
    if entry_date is None or source_uuid is None:
        return _review(
            request,
            row,
            flags=_recordability_flags(document),
            detail="the document cannot be dated or keyed, so no entry can record it",
        )

    unbookable = unbookable_tax(document)
    if unbookable is not None:
        return _review(request, row, flags=(unbookable,), detail=_PRECONDITION_DETAIL)

    base = net_base(document)
    total = document.total
    if base is None or total is None:
        return _review(
            request,
            row,
            flags=(
                _flag(
                    ReviewFlagType.MISSING_SOURCE_FIELD,
                    "SubTotal/Total are absent: the entry's base and its total come from them",
                ),
            ),
            detail=_PRECONDITION_DETAIL,
        )

    flags: list[ReviewFlag] = []
    iva = iva_traslado(document, flags)
    if flags:
        return _review(request, row, flags=tuple(flags), detail=_PRECONDITION_DETAIL)

    refusals = amount_refusals(base, iva, total)
    if refusals:
        return _review(request, row, flags=refusals, detail=_PRECONDITION_DETAIL)

    return BaseAmounts(
        source_uuid=source_uuid, entry_date=entry_date, base=base, iva=iva, total=total
    )


def net_base(document: FiscalDocument) -> NormalizedAmount | None:
    """§8:175/191 + §8a:204: `base = SubTotal − Descuento` under M3's `discount_policy = net`.

    ``None`` when the source carries no ``SubTotal`` (an unnormalized currency nulls it, M2) —
    the caller reviews instead of treating a missing base as zero, which would credit revenue
    that the document does not state.
    """
    if document.subtotal is None:
        return None
    descuento = document.descuento.amount if document.descuento is not None else Decimal("0")
    return NormalizedAmount(document.subtotal.amount - descuento)


def iva_traslado(document: FiscalDocument, flags: list[ReviewFlag]) -> NormalizedAmount:
    """The document's own IVA traslados, summed into the one leg §8:179/180/181/182 names.

    An exempt traslado moves nothing and contributes nothing. A traslado that carries a rate but
    no `Importe` is a source the rule cannot compute from, so it appends its reason to ``flags``
    and the caller refuses — the returned total is then meaningless by construction.
    """
    total = Decimal("0")
    for traslado in document.impuestos.traslados:
        if traslado.importe is None:
            if traslado.tipo_factor == EXENTO:
                continue
            flags.append(
                _flag(
                    ReviewFlagType.MISSING_SOURCE_FIELD,
                    f"IVA traslado ({traslado.tipo_factor}) carries no Importe: the tax leg"
                    " cannot be computed from it",
                )
            )
            continue
        total += traslado.importe.amount
    return NormalizedAmount(total)


def amount_refusals(
    base: NormalizedAmount, iva: NormalizedAmount, total: NormalizedAmount
) -> tuple[ReviewFlag, ...]:
    """§12 + §8:175: the amounts must be usable *and* consistent with the document's own total.

    A corrupt amount — non-finite, zero or negative — is never posted (non-finite is checked
    first, because a comparison against ``NaN`` raises rather than answering, and a raise is not
    a review decision either). And with no retenciones and only IVA traslados (§8a's plain
    shape) a consistent CFDI states `Total == base + IVA`; checking it here is also what makes
    the proposed entry balance, so an inconsistent source is a corrupt document, not an entry.
    """
    values = (("base", base), ("IVA", iva), ("Total", total))
    non_finite = [
        f"{name} {value.amount}" for name, value in values if not value.amount.is_finite()
    ]
    if non_finite:
        return (
            _flag(
                ReviewFlagType.INVALID_AMOUNT,
                f"non-finite source amounts: {', '.join(non_finite)}",
            ),
        )
    # The base and the total must move something — a comprobante that sells nothing is not a
    # posting. IVA may legitimately be zero: an exempt document has no tax to book, so it gets
    # no IVA leg at all (§8:179/181) rather than a zero leg, which is not a leg.
    if base.amount <= 0 or total.amount <= 0 or iva.amount < 0:
        return (
            _flag(
                ReviewFlagType.INVALID_AMOUNT,
                f"unusable source amounts: base {base.amount}, IVA {iva.amount},"
                f" Total {total.amount}",
            ),
        )
    if total.amount != base.amount + iva.amount:
        return (
            _flag(
                ReviewFlagType.INVALID_AMOUNT,
                f"Total {total.amount} != base {base.amount} + IVA {iva.amount}: the document's"
                " own arithmetic is inconsistent",
            ),
        )
    return ()


def unbookable_tax(document: FiscalDocument) -> ReviewFlag | None:
    """The one tax shape these rules book: IVA traslados and nothing else (§8:179–182).

    Retenciones and every other impuesto are *not* silently dropped — dropping them would
    produce an entry that does not state what the document says — so the document is reviewed
    instead, with the elements named.
    """
    if document.impuestos.retenciones:
        return _flag(
            ReviewFlagType.UNSUPPORTED_RULE,
            "the document carries retenciones, which §8:179–182's entries do not book",
        )
    others = sorted(
        {
            traslado.impuesto
            for traslado in document.impuestos.traslados
            if traslado.impuesto != IVA_IMPUESTO
        }
    )
    if others:
        return _flag(
            ReviewFlagType.UNSUPPORTED_RULE,
            f"the document carries traslados of impuesto {', '.join(others)}: §8:179–182 book"
            " IVA only, and a partial entry would misstate the document",
        )
    return None
