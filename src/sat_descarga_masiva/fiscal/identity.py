"""Static three-way RFC identity check (AGENT.md §7a). Pure domain logic.

Compares the configured (folder) RFC, the FIEL certificate RFC, and the CSF RFC
at onboarding. Mismatch -> result with the offending pairs; the fiscal layer
maps a mismatch to NEEDS_REVIEW.
"""

from __future__ import annotations

from dataclasses import dataclass

from sat_descarga_masiva.domain.model.value_objects import Rfc


@dataclass(frozen=True)
class RfcIdentityCheckResult:
    matches: bool
    mismatched_pairs: tuple[str, ...] = ()


def check_three_way_rfc(*, configured: Rfc, cert: Rfc, csf: Rfc) -> RfcIdentityCheckResult:
    """Return OK if all three RFCs agree; else list the differing pairs."""
    mismatches: list[str] = []
    if configured != cert:
        mismatches.append("configured-vs-cert")
    if configured != csf:
        mismatches.append("configured-vs-csf")
    if cert != csf:
        mismatches.append("cert-vs-csf")
    return RfcIdentityCheckResult(matches=not mismatches, mismatched_pairs=tuple(mismatches))
