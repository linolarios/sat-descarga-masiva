"""Signature verdict -> review flags (M2.5). Pure; review state is additive (§8).

The single mapping in the codebase from a SignatureVerdict to review state. VALID adds
nothing; every other outcome opens exactly one CFDI_SIGNATURE flag that carries the
outcome token, so a reason is deterministic and greppable. This module has no satcfdi, XML,
crypto or network dependency: it only translates a domain fact into review state.
"""

from __future__ import annotations

from sat_descarga_masiva.domain.model.review import (
    ReviewFlag,
    ReviewFlags,
    ReviewFlagType,
)
from sat_descarga_masiva.domain.model.signature import SignatureOutcome, SignatureVerdict


def review_flags_for(verdict: SignatureVerdict) -> ReviewFlags:
    """The ADDITIVE review state a verdict implies: empty for VALID, else one flag."""
    if verdict.outcome is SignatureOutcome.VALID:
        return ReviewFlags()
    return ReviewFlags().with_flag(ReviewFlag(ReviewFlagType.CFDI_SIGNATURE, reason_for(verdict)))


def reason_for(verdict: SignatureVerdict) -> str:
    """Deterministic flag reason: outcome token, then the verifier detail when present."""
    detail = verdict.detail.strip()
    if not detail:
        return f"cfdi signature {verdict.outcome.value}"
    return f"cfdi signature {verdict.outcome.value}: {detail}"
