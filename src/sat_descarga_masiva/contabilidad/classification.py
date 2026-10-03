"""``AccountingCategory`` and the purchase-classification seam (§8a:196/207).

A *received* income comprobante (rules 4.3/4.4) is a purchase in the contributor's books,
and §8's row for it names the base leg ``Gasto/Inv`` — *which* of the two is not in the
document. It is a **classification decision**: ``ClaveProdServ → AccountingCategory`` is
configuration (§8a:207), and ``AccountingCategory → AccountRole`` is this module's fixed
vocabulary. The rule stays role-typed: it never reads a ClaveProdServ→role table and never
defaults to Gasto.

Three deliberate properties:

- **fixed, not invented.** The categories are the engine's vocabulary, and the one category
  M3 posts nothing for is ``ACTIVO_FIJO``: §8a:196's "capital goods ⇒ fixed-asset review"
  means a capital product yields a *refusal to classify* — never a guessed fixed-asset
  posting and never a silent Gasto;
- **total or refused.** `classify_document` answers with exactly one role **or** a typed
  `ClassificationRefusal`: a concept with no ClaveProdServ, a product the client's mapping
  does not classify, capital goods, or concepts that disagree on the role. There is no
  first-wins and no sum — a mixed document is reviewed whole (§8:173);
- **no second version.** The ``ClaveProdServ → AccountingCategory`` table rides the same
  versioned mapping YAML, so it needs no version of its own (§8a:207).

The purchase rules (4.3/4.4) read the resolved answer off their `PostingContext.classification`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from sat_descarga_masiva.contabilidad.roles import AccountRole
from sat_descarga_masiva.domain.model.fiscal_document import FiscalDocument


class AccountingCategory(StrEnum):
    """The purchase semantics a ``ClaveProdServ`` is classified into (§8a:207).

    Values are the lowercased names so the mapping YAML keys on the same words the engine
    uses, and a persisted config line stays readable without the enum.
    """

    GASTO = "gasto"
    INVENTARIO = "inventario"
    ACTIVO_FIJO = "activo_fijo"


#: The role each category books to. ``ACTIVO_FIJO`` is deliberately absent: §8a:196's
#: "capital goods ⇒ fixed-asset review" means M3 posts nothing for it. The role itself exists
#: (``AccountRole.ACTIVO_FIJO``) so a review can *name* what the contador's ruling will need,
#: but selecting it here would be inventing a fixed-asset posting the engine may not make.
_POSTABLE_ROLE: Mapping[AccountingCategory, AccountRole] = {
    AccountingCategory.GASTO: AccountRole.GASTO,
    AccountingCategory.INVENTARIO: AccountRole.INVENTARIO,
}


def role_for(category: AccountingCategory) -> AccountRole | None:
    """The role ``category`` books to, or ``None`` when M3 posts nothing for it (§8a:196).

    ``None`` is not "unmapped": it is the capital-goods case, which `classify_document`
    turns into a review rather than a posting.
    """
    return _POSTABLE_ROLE.get(category)


class ClassificationRefusalKind(StrEnum):
    """Why a document's concepts cannot be reduced to one postable role (§8a:207).

    ``kind`` is the machine-readable bit — the rule that turns this into a `ReviewRequest`
    keys on it, so a reworded ``detail`` can never change a decision (§8:167).
    """

    MISSING_CLAVE_PROD_SERV = "missing_clave_prod_serv"
    UNCLASSIFIED_PRODUCT = "unclassified_product"
    CAPITAL_GOODS = "capital_goods"
    MIXED_CATEGORIES = "mixed_categories"


@dataclass(frozen=True)
class ClassificationRefusal:
    """A document whose concepts do not classify to a single postable role (§8a:207).

    A refusal always says why: a silent non-classification would be indistinguishable from a
    document nobody looked at (§8:167).
    """

    kind: ClassificationRefusalKind
    detail: str

    def __post_init__(self) -> None:
        if not self.detail.strip():
            raise ValueError(
                "a classification refusal must say why the document was not classified (§8:167)"
            )


#: The answer to "what does this purchase book to?": one role, or why it is not one.
Classification = AccountRole | ClassificationRefusal


def classify_document(
    document: FiscalDocument, classification: Mapping[str, AccountingCategory]
) -> Classification:
    """The single role every concept of ``document`` classifies to, or why it is not one.

    ``classification`` is the client's ``ClaveProdServ → AccountingCategory`` table (§8a:207).
    Concepts that all land on the same role classify; anything else — a missing or unclassified
    ClaveProdServ, capital goods, or concepts that disagree — is refused whole, because taking
    the first or summing the roles would misstate the document (§8:173).
    """
    roles: set[AccountRole] = set()
    for concepto in document.conceptos:
        clave = concepto.clave_prod_serv.strip()
        if not clave:
            return ClassificationRefusal(
                ClassificationRefusalKind.MISSING_CLAVE_PROD_SERV,
                "a concepto carries no ClaveProdServ, so its purchase cannot be classified",
            )
        category = classification.get(clave)
        if category is None:
            return ClassificationRefusal(
                ClassificationRefusalKind.UNCLASSIFIED_PRODUCT,
                f"ClaveProdServ {clave!r} is not classified by the client's mapping:"
                " §8a:196 reviews a missing mapping rather than defaulting to Gasto",
            )
        role = role_for(category)
        if role is None:
            return ClassificationRefusal(
                ClassificationRefusalKind.CAPITAL_GOODS,
                f"ClaveProdServ {clave!r} is classified {category.value}: §8a:196 routes"
                " capital goods to a fixed-asset review rather than a guessed posting",
            )
        roles.add(role)
    if not roles:
        return ClassificationRefusal(
            ClassificationRefusalKind.MISSING_CLAVE_PROD_SERV,
            "the document carries no conceptos, so its purchase cannot be classified",
        )
    if len(roles) > 1:
        named = ", ".join(sorted(role.value for role in roles))
        return ClassificationRefusal(
            ClassificationRefusalKind.MIXED_CATEGORIES,
            f"the document's concepts classify to more than one role ({named}): taking one or"
            " summing them would misstate the document (§8:173)",
        )
    return next(iter(roles))
