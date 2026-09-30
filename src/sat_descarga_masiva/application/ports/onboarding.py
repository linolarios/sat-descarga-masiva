"""Port for the contributor resolution the M2 onboarding boundary calls.

`fiscal.onboarding.resolve_contributor_profile` already IS that capability: a pure
function of the three identity facts the boundary holds (the configured RFC, the
certificate's RFC and the parsed CSF). This port only names the shape, so the use
case depends on the capability rather than on the module that implements it — and
a test can hand it a recording double, exactly as the M2.8 projection boundary
does (`application/ports/projection.py`).
"""

from __future__ import annotations

from typing import Protocol

from sat_descarga_masiva.domain.model.csf import CsfData
from sat_descarga_masiva.domain.model.value_objects import Rfc
from sat_descarga_masiva.fiscal.onboarding import OnboardingResult


class ContributorResolver(Protocol):
    def __call__(self, *, configured_rfc: Rfc, cert_rfc: Rfc, csf: CsfData) -> OnboardingResult: ...
