"""OnboardContributorUseCase — onboard one contributor from its CSF (§7a, §11 M2).

§7's onboarding step, in order:

    retain the original -> index it -> parse it -> resolve identity -> persist profile

Decisions, each traceable:

- **The evidence comes first and is never rolled back.** §7a makes the CSF "an
  immutable source artifact": hash it, retain the original, never modify it. So
  the bytes go to the sink and their record into `csf_artifacts` *before* anything
  is parsed or claimed. A parse failure or an identity mismatch therefore leaves
  the retained original and its index row in place — retention is an independent
  fact, not a side effect of a successful onboarding.
- **One byte string, one parse.** The exact object the caller supplied is what the
  sink stores and what the parser reads, so the stored original, the recorded hash
  and the parsed facts all describe the same bytes; nothing is re-serialized (the
  same rule the process stage follows, §6a.3).
- **The retained original is indexed once.** The `csf_artifacts` row is an
  append-only fact keyed by content hash, so a hash already on record keeps the
  instant it was first recorded: onboarding the same constancia again retains the
  bytes (idempotently, on disk) and reuses the recorded row instead of re-dating
  it — while a same-hash claim pointing at a different location is still refused,
  because the original is never repointed.
- **The certificate RFC is an input.** §7's onboarding "needs no FIEL" for the
  identity check: the three-way check compares the configured RFC, the certificate
  RFC and the CSF RFC, all three of which arrive as values. Nothing here loads a
  key, a password or a signing identity (D1).
- **A mismatch is a result, not an exception and not a status.** §7a's three-way
  check already answers with `RfcIdentityCheckResult` (matching + the offending
  pairs). A mismatch returns that result, persists *no* profile, and mints no new
  review vocabulary: §7a already says the fiscal layer maps it to NEEDS_REVIEW, and
  the onboarding step owns only "did the identity resolve?" (D5).
- **`profile_version` is append-only.** §7a's versioned config means each version
  is immutable, so a new profile version is `latest + 1` — never an in-place edit
  of a confirmed profile (§7a: "require human confirmation").
- **The profile timestamps are the use case's clock.** `csf_obtained_at` says when
  onboarding obtained this constancia, so it comes from the injected `Clock` (one
  read per onboarding), not from a filesystem mtime or the sink's own timestamp.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from sat_descarga_masiva.application.ports.csf import CsfArtifactSink, CSFParser
from sat_descarga_masiva.application.ports.onboarding import ContributorResolver
from sat_descarga_masiva.application.ports.persistence import (
    ContributorProfileRepository,
    CsfArtifactRepository,
)
from sat_descarga_masiva.application.ports.services import Clock
from sat_descarga_masiva.domain.model.contributor import (
    ContributorProfile,
    ContributorProfileRecord,
)
from sat_descarga_masiva.domain.model.csf import CsfArtifact
from sat_descarga_masiva.domain.model.value_objects import Rfc
from sat_descarga_masiva.fiscal.identity import RfcIdentityCheckResult


@dataclass(frozen=True)
class OnboardingOutcome:
    """What one onboarding produced: the retained CSF, the verdict, the profile.

    `identity` is the existing `RfcIdentityCheckResult` verbatim — this use case
    reports the verdict, it does not translate it into review vocabulary (§7a
    leaves that to the fiscal layer). `profile_record` is ``None`` exactly when no
    profile was persisted (an unresolved identity), so `onboarded` answers "is this
    contributor now onboarded?" without re-deriving it from the identity result.

    Lives here rather than in `domain/model/results.py` because it carries a fiscal
    verdict: domain must never import `fiscal`.
    """

    identity: RfcIdentityCheckResult
    csf_artifact: CsfArtifact
    profile_record: ContributorProfileRecord | None = None

    @property
    def onboarded(self) -> bool:
        return self.profile_record is not None


class OnboardContributorUseCase:
    """Onboard a contributor: retain and parse the CSF, then version its profile."""

    def __init__(  # noqa: PLR0913 (each collaborator is a distinct capability, none optional)
        self,
        *,
        sink: CsfArtifactSink,
        parser: CSFParser,
        resolver: ContributorResolver,
        artifacts: CsfArtifactRepository,
        profiles: ContributorProfileRepository,
        clock: Clock,
    ) -> None:
        self._sink = sink
        self._parser = parser
        self._resolver = resolver
        self._artifacts = artifacts
        self._profiles = profiles
        self._clock = clock

    def onboard(self, csf_bytes: bytes, *, client_rfc: Rfc, cert_rfc: Rfc) -> OnboardingOutcome:
        """Retain the CSF, parse it, resolve the identity, persist a new version."""
        artifact = self._retain(csf_bytes)
        csf = self._parser.parse(csf_bytes)
        resolution = self._resolver(configured_rfc=client_rfc, cert_rfc=cert_rfc, csf=csf)
        if not resolution.identity.matches:
            return OnboardingOutcome(identity=resolution.identity, csf_artifact=artifact)
        latest = self._profiles.latest(client_rfc)
        if latest is not None and self._unchanged(latest, resolution.profile, artifact.sha256):
            # D4: same source, same profile -> the confirmed version still describes
            # this contributor, so no new version is written (and no timestamp is
            # spent proving it).
            return OnboardingOutcome(
                identity=resolution.identity, csf_artifact=artifact, profile_record=latest
            )
        record = self._profile_record(
            latest=latest,
            profile=resolution.profile,
            csf_hash=artifact.sha256,
            now=self._clock.now(),
        )
        self._profiles.save(record)
        return OnboardingOutcome(
            identity=resolution.identity, csf_artifact=artifact, profile_record=record
        )

    def _retain(self, csf_bytes: bytes) -> CsfArtifact:
        """Retain the original and index it as an append-only fact, before any claim (§7a).

        The sink owns *disk* retention (its own idempotence) and reports the instant it
        was asked to store. The `csf_artifacts` row is the recorded fact, so a hash
        already on record keeps the instant it was first recorded: re-indexing the
        sink's later instant would re-date an immutable row — and against the real
        store, whose rule is "same hash with different content is a conflict", it would
        refuse a legitimate re-onboarding of the same constancia. The store is still
        asked to index this hash, so a same-hash claim pointing somewhere else is
        refused rather than quietly accepted (the original is never repointed).
        """
        artifact = self._sink.store(csf_bytes)
        recorded = self._artifacts.get(artifact.sha256)
        if recorded is None:
            self._artifacts.record(artifact)
            return artifact
        self._artifacts.record(replace(artifact, recorded_at=recorded.recorded_at))
        return recorded

    @staticmethod
    def _unchanged(
        latest: ContributorProfileRecord, profile: ContributorProfile, csf_hash: str
    ) -> bool:
        """Is the stored latest version already *this* source and profile (§7a)?

        Both halves matter: §7a's audit chain is profile -> `csf_hash` -> original,
        so a re-issued constancia with the same facts is still a different source
        and therefore a new version.
        """
        return latest.csf_hash == csf_hash and latest.profile == profile

    def _profile_record(
        self,
        *,
        latest: ContributorProfileRecord | None,
        profile: ContributorProfile,
        csf_hash: str,
        now: datetime,
    ) -> ContributorProfileRecord:
        """The next immutable version of this contributor's profile.

        §7a's versioned config: a confirmed profile is never edited in place, so a
        corrected extraction becomes a new version — `latest + 1`, and `1` for the
        first one. The provenance is stamped with the instant onboarding observed
        this constancia, which is the same instant that gets recorded.
        """
        return ContributorProfileRecord(
            profile=profile,
            csf_hash=csf_hash,
            csf_obtained_at=now,
            profile_version=1 if latest is None else latest.profile_version + 1,
            recorded_at=now,
        )
