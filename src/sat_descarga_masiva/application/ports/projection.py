"""Port for the per-document fiscal projection, as the M2.8 boundary calls it.

`fiscal.projection.project_document` already IS that capability: a pure function of
the facts the boundary established (parsed facts, signature verdict, the artifact's
source hash and the UUID its name carries). This port only names the shape, so the
use case depends on the capability rather than on the module that implements it —
and a test can hand it a recording double.
"""

from __future__ import annotations

from typing import Protocol

from sat_descarga_masiva.domain.model.raw_cfd import RawCfd
from sat_descarga_masiva.domain.model.signature import SignatureVerdict
from sat_descarga_masiva.domain.model.value_objects import Rfc
from sat_descarga_masiva.domain.policy.money import MoneyPolicy
from sat_descarga_masiva.fiscal.projection import ProcessedDocument


class DocumentProjector(Protocol):
    def __call__(  # noqa: PLR0913 (the M2.6 boundary has exactly these facts to hand over)
        self,
        raw: RawCfd,
        verdict: SignatureVerdict,
        *,
        source_hash: str,
        contributor_rfc: Rfc,
        artifact_uuid: str,
        money: MoneyPolicy | None = None,
    ) -> ProcessedDocument: ...
