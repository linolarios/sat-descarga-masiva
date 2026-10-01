"""CFDI document type (``c_TipoDeComprobante``) — the letters §8's rule table speaks.

Domain-only vocabulary: the letter is the *source* fact (§8:177-189 distinguishes the
six cases), never a satcfdi object and never an accounting meaning. A letter outside
the catalog is **not** a type: :meth:`TipoComprobante.of` answers ``None`` rather than
raising or guessing, because §8:161 requires an **unsupported document type** to reach
the ``PostingEligibilityValidator``'s ``NEEDS_REVIEW`` path — never a silent
``SKIPPED``.
"""

from __future__ import annotations

from enum import StrEnum


class TipoComprobante(StrEnum):
    """One of the six document types the accounting rule table distinguishes (§8:177)."""

    INGRESO = "I"
    EGRESO = "E"
    TRASLADO = "T"
    NOMINA = "N"
    PAGO = "P"
    RETENCIONES = "R"

    @classmethod
    def of(cls, value: str) -> TipoComprobante | None:
        """The typed document type, or ``None`` when the source letter is not one.

        ``None`` is the entire vocabulary for "unsupported": the caller must send it
        down its review path (§8:159/161), so it can never be mistaken for a type that
        posts. Nothing is normalized here — the letter is compared as sourced.
        """
        try:
            return cls(value)
        except ValueError:
            return None
