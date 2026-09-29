"""Per-document fiscal projection: one ProcessedDocument per artifact (M2.6, §6/§11).

Pure composition, no I/O and no persistence: this module reads the values the M2.8
orchestration boundary establishes (a parsed `RawCfd`, a `SignatureVerdict`, the artifact's
`source_hash` and the UUID its name carries) and combines the three existing producers —

    build_fiscal_document  money + fiscal facts (parse flags)
    resolve_perspective    contributor role (perspective flags)
    review_flags_for       authenticity (signature flags)

— into an immutable `ProcessedDocument` whose review state is the *additive union* of all
three, so no producer can erase another's flags (§8).

Identity is the TFD UUID, never the artifact filename or hash (§6). When that identity is
missing or contradicts the artifact's name, the document is **quarantined**: the result carries
a deterministic `quarantine_reason` and simply is not a projected document. Nothing is invented
to fill the gap — no new review-flag type, no new error type, no reconciliation of the two
identifiers. `PARTIAL` is a parse *outcome*, not an identity defect, and stays projectable.

The exact-byte invariant belongs to the caller: M2.8 supplies the same original bytes to the
verifier and to the parser. This module never reads, rewrites, serializes or hashes a file.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sat_descarga_masiva.domain.model.fiscal_document import (
    FiscalDocument,
    FiscalParseResult,
    ParseOutcome,
)
from sat_descarga_masiva.domain.model.perspective import Perspective
from sat_descarga_masiva.domain.model.raw_cfd import RawCfd
from sat_descarga_masiva.domain.model.review import ReviewFlags
from sat_descarga_masiva.domain.model.signature import SignatureVerdict
from sat_descarga_masiva.domain.model.value_objects import Rfc, Uuid
from sat_descarga_masiva.domain.policy.money import MoneyPolicy
from sat_descarga_masiva.fiscal.parse import build_fiscal_document
from sat_descarga_masiva.fiscal.perspective import resolve_perspective
from sat_descarga_masiva.fiscal.signature import review_flags_for


@dataclass(frozen=True)
class ProcessedDocument:
    """The composed fiscal result for one artifact, plus whether it may be projected.

    `document` keeps the parsed facts even when the result is quarantined: the facts are not
    discarded, they are simply not projected. `quarantine_reason` is the only quarantine
    channel — persistence (M2.8) counts such a result instead of writing a projection row.
    """

    outcome: ParseOutcome
    document: FiscalDocument | None
    perspective: Perspective
    review_flags: ReviewFlags = field(default_factory=ReviewFlags)
    quarantine_reason: str | None = None

    @property
    def is_quarantined(self) -> bool:
        return self.quarantine_reason is not None

    @property
    def uuid(self) -> Uuid | None:
        """The TFD UUID of a *projected* document; None for a quarantined or unparsed one."""
        if self.is_quarantined or self.document is None:
            return None
        return self.document.source_uuid


def project_document(  # noqa: PLR0913 (each argument is a fact the boundary already established)
    raw: RawCfd,
    verdict: SignatureVerdict,
    *,
    source_hash: str,
    contributor_rfc: Rfc,
    artifact_uuid: str,
    money: MoneyPolicy | None = None,
) -> ProcessedDocument:
    """Compose the per-document fiscal result from already-established facts.

    `artifact_uuid` is the UUID the artifact's name carries — compared against the TFD UUID,
    never used as the identity itself.
    """
    parsed: FiscalParseResult = build_fiscal_document(raw, source_hash, money)
    resolution = resolve_perspective(contributor_rfc, raw)
    return ProcessedDocument(
        outcome=parsed.outcome,
        document=parsed.document,
        perspective=resolution.perspective,
        review_flags=parsed.review_flags.merged_with(
            resolution.review_flags, review_flags_for(verdict)
        ),
        quarantine_reason=_quarantine_reason(parsed.document, raw.version, artifact_uuid),
    )


def _quarantine_reason(
    document: FiscalDocument | None, version: str, artifact_uuid: str
) -> str | None:
    """The first applicable rule, in a deliberate order; None admits the document.

    A document with no fiscal projection cannot be identified at all, so that rule reports
    first (§11 M2: an unsupported CFDI version is never a partial parse). Then the TFD UUID
    must exist, and must agree with the UUID the artifact is named for; disagreeing
    identifiers are reported together and never reconciled silently (§6).
    """
    if document is None:
        return f"no fiscal projection: CFDI version {version} unsupported"
    tfd_uuid = document.source_uuid
    if tfd_uuid is None:
        return "no TFD UUID: no trustworthy document identity"
    try:
        artifact = Uuid(artifact_uuid)
    except ValueError:
        return f"artifact name {artifact_uuid} is not a CFDI UUID"
    if tfd_uuid != artifact:
        return f"TFD UUID {tfd_uuid.value} differs from the artifact UUID {artifact.value}"
    return None
