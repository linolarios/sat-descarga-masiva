"""Per-CFDI perspective router (AGENT.md §7a/§8). Pure; review state additive.

Decides whether the contributor is the emisor (EMITIDO) or receptor (RECIBIDO) of a
CFDI Comprobante. Anything ambiguous -- no match, both participants matching, or a
generic RFC -- resolves to UNDETERMINED and opens a review flag. A mismatch is never a
silent resolution and never a skip. This module has no SAT/XML/network dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sat_descarga_masiva.domain.model.perspective import Perspective
from sat_descarga_masiva.domain.model.raw_cfd import RawCfd
from sat_descarga_masiva.domain.model.review import (
    ReviewFlag,
    ReviewFlags,
    ReviewFlagType,
)
from sat_descarga_masiva.domain.model.value_objects import Rfc

# Generic RFCs are never, on their own, sufficient to infer perspective (§8).
_GENERIC_RFCS = frozenset({"XAXX010101000", "XEXX010101000"})


@dataclass(frozen=True)
class PerspectiveResolution:
    """Factual perspective plus the review flags it implies (additive).

    `matched` is True only when a perspective was safely resolved (EMITIDO/RECIBIDO).
    It is False for every UNDETERMINED outcome -- a concrete mismatch, a both-match, a
    generic contributor, or a missing participant. It is *not* "the contributor matched
    some participant", and it never encodes accounting/posting state.
    """

    perspective: Perspective
    matched: bool
    review_flags: ReviewFlags = field(default_factory=ReviewFlags)


def resolve_perspective(contributor_rfc: Rfc, raw: RawCfd) -> PerspectiveResolution:
    """Resolve the contributor perspective for a parsed CFDI Comprobante.

    RFCs are compared through the canonical `Rfc` value object (it normalizes on
    construction), so no local strip/upper is duplicated here.
    """
    contributor = contributor_rfc.value
    emisor = Rfc(raw.emisor_rfc).value
    receptor = Rfc(raw.receptor_rfc).value if raw.receptor_rfc is not None else None

    matches_emisor = contributor == emisor
    matches_receptor = receptor is not None and contributor == receptor

    # Decision order is deliberate and load-bearing (§8). It fixes the three generic
    # cases without a separate branch: generic + neither -> mismatch (1); generic +
    # both -> undetermined (2); generic + exactly one -> undetermined (3).
    if not matches_emisor and not matches_receptor:
        return _unresolved(
            ReviewFlagType.PERSPECTIVE_MISMATCH,
            f"contributor {contributor} matches neither emisor {emisor} nor receptor {receptor}",
        )
    if matches_emisor and matches_receptor:
        return _unresolved(
            ReviewFlagType.PERSPECTIVE_UNDETERMINED,
            f"contributor {contributor} matches both emisor and receptor",
        )
    if contributor in _GENERIC_RFCS:
        return _unresolved(
            ReviewFlagType.PERSPECTIVE_UNDETERMINED,
            f"contributor {contributor} is generic; perspective cannot be inferred",
        )
    perspective = Perspective.EMITIDO if matches_emisor else Perspective.RECIBIDO
    return PerspectiveResolution(perspective=perspective, matched=True)


def _unresolved(flag_type: ReviewFlagType, reason: str) -> PerspectiveResolution:
    """UNDETERMINED result carrying exactly one additive review flag."""
    return PerspectiveResolution(
        perspective=Perspective.UNDETERMINED,
        matched=False,
        review_flags=ReviewFlags().with_flag(ReviewFlag(flag_type, reason)),
    )
