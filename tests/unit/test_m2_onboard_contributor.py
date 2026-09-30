"""M2-T W1: OnboardContributorUseCase — contributor onboarding (§7a, §11 M2).

The use case is orchestration, so its collaborators are recording doubles: what
it owns is the *sequence* and the *decisions* (retain the evidence, parse those
same bytes, evaluate the three-way identity, version the profile, persist only a
confirmed identity) — never the CSF parsing (M2.2), never the RFC comparison
(M2.2) and never the storage semantics (M2.7, proved in
test_m2_persistence_contract.py).

Locked decisions exercised here (M2-T D1/D4/D5/D6): the certificate RFC is an
input, so nothing here loads a FIEL key or password; the profile is versioned
`1 -> latest + 1`; an identity mismatch is an expected result carrying the
existing `RfcIdentityCheckResult` and persists no profile (no new review
vocabulary); and the immutable CSF retention is an independent fact that
survives a parse failure or a mismatch.

Values are real domain objects; only collaborators with I/O or library
dependencies are faked, so a change in any domain shape shows up here. Every
double appends to one shared `events` list, which is how the *order* of the audit
chain (§7a: profile -> csf_hash -> retained original) is proved.
"""

from __future__ import annotations

import ast
import inspect
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sat_descarga_masiva.application.use_cases import (
    onboard_contributor as onboard_contributor_module,
)
from sat_descarga_masiva.application.use_cases.onboard_contributor import (
    OnboardContributorUseCase,
)
from sat_descarga_masiva.domain.errors import CsfParseError, ImmutableRecordConflict
from sat_descarga_masiva.domain.model.contributor import (
    ContributorProfile,
    ContributorProfileRecord,
    PersonaTipo,
    RegimenFiscal,
    SituacionFiscal,
)
from sat_descarga_masiva.domain.model.csf import CsfArtifact, CsfData
from sat_descarga_masiva.domain.model.source import sha256_hex
from sat_descarga_masiva.domain.model.value_objects import Rfc
from sat_descarga_masiva.fiscal.identity import RfcIdentityCheckResult
from sat_descarga_masiva.fiscal.onboarding import OnboardingResult, resolve_contributor_profile
from sat_descarga_masiva.infrastructure.persistence.memory import (
    InMemoryContributorProfileRepository,
    InMemoryCsfArtifactRepository,
)
from sat_descarga_masiva.infrastructure.source.csf_sink import FilesystemCsfArtifactSink

RFC = Rfc("WATM640917J45")
OTHER_RFC = Rfc("AAA010101AAA")
CSF_PDF = b"%PDF-1.4 constancia de situacion fiscal"
T0 = datetime(2026, 1, 1, tzinfo=UTC)
T1 = datetime(2026, 1, 2, tzinfo=UTC)
CSF_HASH = sha256_hex(CSF_PDF)


def _csf(rfc: Rfc = RFC, *, nombre: str = "PERSONA FISICA DE PRUEBA") -> CsfData:
    return CsfData(
        rfc=rfc,
        nombre=nombre,
        persona_tipo=PersonaTipo.FISICA,
        regimen_fiscal=RegimenFiscal("612", "Personas fisicas con actividades empresariales"),
        situacion_fiscal=SituacionFiscal.ACTIVO,
    )


def _artifact(sha256: str = CSF_HASH, *, recorded_at: datetime = T1) -> CsfArtifact:
    """What the sink returns: a hash-named retained original.

    `recorded_at` is deliberately a *different* instant than the use case's clock
    so the test can prove which clock the persisted profile timestamps use.
    """
    return CsfArtifact(sha256=sha256, stored_path=f"csf/{sha256}.pdf", recorded_at=recorded_at)


@dataclass
class _Clock:
    instants: list[datetime]
    calls: int = 0

    def now(self) -> datetime:
        instant = self.instants[self.calls]
        self.calls += 1
        return instant


@dataclass
class _Sink:
    """Records the bytes it is handed; returns the artifact the harness chose."""

    artifact: CsfArtifact
    events: list[str]
    calls: list[bytes] = field(default_factory=list)

    def store(self, content: bytes) -> CsfArtifact:
        self.calls.append(content)
        self.events.append("retain_csf")
        return self.artifact


@dataclass
class _RedatingSink:
    """A sink that reports a fresh retention instant on every call, as a clock would."""

    instants: list[datetime]
    events: list[str]
    calls: list[bytes] = field(default_factory=list)
    stored_artifact: CsfArtifact | None = None

    def store(self, content: bytes) -> CsfArtifact:
        self.calls.append(content)
        self.events.append("retain_csf")
        self.stored_artifact = _artifact(
            sha256_hex(content), recorded_at=self.instants[min(len(self.calls) - 1, 1)]
        )
        return self.stored_artifact


@dataclass
class _Parser:
    csf: CsfData
    events: list[str]
    fault: Exception | None = None
    calls: list[bytes] = field(default_factory=list)

    def parse(self, pdf_bytes: bytes) -> CsfData:
        self.calls.append(pdf_bytes)
        self.events.append("parse_csf")
        if self.fault is not None:
            raise self.fault
        return self.csf


@dataclass
class _Resolver:
    """Stands in for `fiscal.onboarding.resolve_contributor_profile`."""

    result: OnboardingResult
    events: list[str]
    calls: list[tuple[Rfc, Rfc, CsfData]] = field(default_factory=list)

    def __call__(self, *, configured_rfc: Rfc, cert_rfc: Rfc, csf: CsfData) -> OnboardingResult:
        self.calls.append((configured_rfc, cert_rfc, csf))
        self.events.append("resolve_identity")
        return self.result


class _Artifacts:
    """`CsfArtifactRepository`: append-only, keyed by content hash (§7a)."""

    def __init__(self, events: list[str] | None = None) -> None:
        self.records: list[CsfArtifact] = []
        self._events = [] if events is None else events

    def record(self, artifact: CsfArtifact) -> None:
        existing = self.get(artifact.sha256)
        if existing is not None and existing != artifact:
            raise ImmutableRecordConflict(
                f"CSF artifact {artifact.sha256} already recorded with different content"
            )
        if existing is None:
            self.records.append(artifact)
        self._events.append("index_csf")

    def get(self, sha256: str) -> CsfArtifact | None:
        return next((item for item in self.records if item.sha256 == sha256), None)


class _Profiles:
    """`ContributorProfileRepository`: versions are immutable and (rfc, version) keyed."""

    def __init__(
        self,
        existing: tuple[ContributorProfileRecord, ...] = (),
        events: list[str] | None = None,
    ) -> None:
        self.rows: dict[tuple[str, int], ContributorProfileRecord] = {
            (row.profile.rfc.value, row.profile_version): row for row in existing
        }
        self.saved: list[ContributorProfileRecord] = []
        self._events = [] if events is None else events

    def save(self, record: ContributorProfileRecord) -> None:
        self.saved.append(record)
        self.rows[(record.profile.rfc.value, record.profile_version)] = record
        self._events.append("persist_profile")

    def get(self, client_rfc: Rfc, profile_version: int) -> ContributorProfileRecord | None:
        return self.rows.get((client_rfc.value, profile_version))

    def latest(self, client_rfc: Rfc) -> ContributorProfileRecord | None:
        versions = [version for (rfc, version) in self.rows if rfc == client_rfc.value]
        return None if not versions else self.rows[(client_rfc.value, max(versions))]


class _Harness:
    """The use case plus every double it was wired to, so a test can inspect both."""

    def __init__(
        self,
        *,
        csf: CsfData | None = None,
        csf_bytes: bytes = CSF_PDF,
        resolve_with: OnboardingResult | None = None,
        parser_fault: Exception | None = None,
        profiles: _Profiles | None = None,
    ) -> None:
        self.events: list[str] = []
        self.parsed = csf if csf is not None else _csf()
        resolved = (
            resolve_with
            if resolve_with is not None
            else resolve_contributor_profile(configured_rfc=RFC, cert_rfc=RFC, csf=self.parsed)
        )
        self.sink = _Sink(artifact=_artifact(sha256_hex(csf_bytes)), events=self.events)
        self.parser = _Parser(csf=self.parsed, events=self.events, fault=parser_fault)
        self.resolver = _Resolver(result=resolved, events=self.events)
        self.artifacts = _Artifacts(events=self.events)
        self.profiles = profiles if profiles is not None else _Profiles(events=self.events)
        self.clock = _Clock(instants=[T0, T1])
        self.use_case = OnboardContributorUseCase(
            sink=self.sink,
            parser=self.parser,
            resolver=self.resolver,
            artifacts=self.artifacts,
            profiles=self.profiles,
            clock=self.clock,
        )

    def onboard(self, *, csf_bytes: bytes = CSF_PDF, cert_rfc: Rfc = RFC):
        return self.use_case.onboard(csf_bytes, client_rfc=RFC, cert_rfc=cert_rfc)


def _record(
    *,
    profile_version: int = 1,
    csf_hash: str = CSF_HASH,
    nombre: str = "PERSONA FISICA DE PRUEBA",
    recorded_at: datetime = T0,
) -> ContributorProfileRecord:
    """A profile row shaped like the one the use case writes (a store fixture)."""
    return ContributorProfileRecord(
        profile=ContributorProfile(
            rfc=RFC,
            nombre=nombre,
            persona_tipo=PersonaTipo.FISICA,
            regimen_fiscal=RegimenFiscal("612", "Personas fisicas con actividades empresariales"),
            situacion_fiscal=SituacionFiscal.ACTIVO,
        ),
        csf_hash=csf_hash,
        csf_obtained_at=T0,
        profile_version=profile_version,
        recorded_at=recorded_at,
    )


def test_the_first_onboarding_persists_profile_version_one() -> None:
    harness = _Harness()
    outcome = harness.onboard()
    assert len(harness.profiles.saved) == 1
    record = harness.profiles.saved[0]
    assert record.profile_version == 1
    assert record.profile.rfc == RFC
    assert record.profile.persona_tipo is PersonaTipo.FISICA
    assert record.profile.regimen_fiscal.code == "612"
    assert outcome.profile_record == record


def test_the_stored_profile_points_back_at_the_retained_csf_hash() -> None:
    """§7a audit chain: profile -> csf_hash -> the retained original."""
    harness = _Harness()
    outcome = harness.onboard()
    assert outcome.csf_artifact.sha256 == CSF_HASH
    assert outcome.profile_record is not None
    assert outcome.profile_record.csf_hash == CSF_HASH
    assert harness.artifacts.get(CSF_HASH) == outcome.csf_artifact


def test_the_parser_receives_exactly_the_supplied_bytes() -> None:
    harness = _Harness()
    harness.onboard()
    assert harness.sink.calls == [CSF_PDF]
    assert harness.sink.calls[0] is CSF_PDF
    assert harness.parser.calls == [CSF_PDF]
    assert harness.parser.calls[0] is CSF_PDF


def test_the_resolver_receives_the_configured_and_certificate_rfcs() -> None:
    harness = _Harness()
    harness.onboard(cert_rfc=OTHER_RFC)
    assert len(harness.resolver.calls) == 1
    configured, cert, csf = harness.resolver.calls[0]
    assert configured == RFC
    assert cert == OTHER_RFC
    assert csf is harness.parsed


def test_the_profile_timestamps_come_from_the_use_case_clock() -> None:
    """One clock read, and it is the use case's clock — not the artifact's own."""
    harness = _Harness()
    outcome = harness.onboard()
    assert harness.clock.calls == 1
    assert outcome.csf_artifact.recorded_at == T1  # what the sink reported
    assert outcome.profile_record is not None
    assert outcome.profile_record.csf_obtained_at == T0
    assert outcome.profile_record.recorded_at == T0


def test_the_outcome_carries_the_identity_result() -> None:
    harness = _Harness()
    outcome = harness.onboard()
    assert outcome.identity == RfcIdentityCheckResult(matches=True)
    assert outcome.onboarded is True


def test_the_evidence_is_retained_and_indexed_before_anything_is_claimed() -> None:
    """Order is the contract: the original is stored and indexed first (§7a)."""
    harness = _Harness()
    harness.onboard()
    assert harness.events == [
        "retain_csf",
        "index_csf",
        "parse_csf",
        "resolve_identity",
        "persist_profile",
    ]


def test_the_use_case_takes_no_credential_or_certificate_collaborator() -> None:
    """D1: the certificate RFC is an input, so no FIEL key/password is loaded here."""
    parameters = inspect.signature(OnboardContributorUseCase.__init__).parameters
    collaborators = sorted(name for name in parameters if name != "self")
    assert collaborators == ["artifacts", "clock", "parser", "profiles", "resolver", "sink"]
    assert not [
        name
        for name in collaborators
        if any(word in name for word in ("fiel", "cert", "key", "password", "credential"))
    ]


def test_the_onboard_module_imports_nothing_from_infrastructure_or_libraries() -> None:
    """Purity check: the use case must not reach into adapters or PDF/crypto libraries."""
    source = Path(onboard_contributor_module.__file__ or "").read_text(encoding="utf-8")
    tree = ast.parse(source)
    modules = [
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    ]
    modules += [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    ]
    assert modules  # the check is only meaningful against a real import list
    assert not [
        module
        for module in modules
        if module.startswith(
            ("sat_descarga_masiva.infrastructure", "sqlite3", "lxml", "satcfdi", "pypdf")
        )
    ]


# --- W2: mismatch, retention under failure, and profile versioning -------------


def test_an_identity_mismatch_persists_no_profile_and_reports_the_verdict() -> None:
    """§7a/D5: the three-way check's answer is reported, never re-invented."""
    mismatch = resolve_contributor_profile(configured_rfc=RFC, cert_rfc=OTHER_RFC, csf=_csf())
    harness = _Harness(resolve_with=mismatch)
    outcome = harness.onboard(cert_rfc=OTHER_RFC)
    assert outcome.onboarded is False
    assert outcome.profile_record is None
    assert outcome.identity.matches is False
    assert outcome.identity.mismatched_pairs == ("configured-vs-cert", "cert-vs-csf")
    assert harness.profiles.saved == []
    assert harness.clock.calls == 0  # nothing to timestamp: no profile is written


def test_a_mismatch_still_leaves_the_retained_and_indexed_csf() -> None:
    """D6: retention is an independent fact — nothing is deleted or rolled back."""
    mismatch = resolve_contributor_profile(configured_rfc=RFC, cert_rfc=OTHER_RFC, csf=_csf())
    harness = _Harness(resolve_with=mismatch)
    outcome = harness.onboard(cert_rfc=OTHER_RFC)
    assert harness.artifacts.get(CSF_HASH) == outcome.csf_artifact
    assert harness.artifacts.records == [outcome.csf_artifact]
    assert harness.events == ["retain_csf", "index_csf", "parse_csf", "resolve_identity"]


def test_a_csf_that_cannot_be_parsed_keeps_the_retained_original() -> None:
    """A parse fault is a technical failure (it propagates), not a retention failure."""
    harness = _Harness(parser_fault=CsfParseError("not a constancia"))
    with pytest.raises(CsfParseError, match="not a constancia"):
        harness.onboard()
    assert harness.artifacts.records  # retained and indexed before parsing
    assert harness.profiles.saved == []
    assert harness.events == ["retain_csf", "index_csf", "parse_csf"]


def test_re_onboarding_the_identical_csf_writes_no_new_version() -> None:
    """D4: an unchanged source and profile is a no-op — not a `latest + 1` copy."""
    existing = _record(profile_version=1)
    harness = _Harness(profiles=_Profiles(existing=(existing,), events=[]))
    outcome = harness.onboard()
    assert harness.profiles.saved == []  # the store's own no-op is not what decides
    assert harness.profiles.latest(RFC) == existing
    assert outcome.profile_record == existing
    assert outcome.onboarded is True


def test_a_changed_csf_appends_a_new_version_and_keeps_the_old_one() -> None:
    """D4: a corrected constancia is a new immutable version, never an edit."""
    existing = _record(profile_version=1)
    harness = _Harness(
        csf=_csf(nombre="PERSONA FISICA RENOMBRADA"),
        csf_bytes=b"%PDF-1.4 a later constancia",
        profiles=_Profiles(existing=(existing,), events=[]),
    )
    outcome = harness.onboard(csf_bytes=b"%PDF-1.4 a later constancia")
    record = harness.profiles.saved[0]
    assert record.profile_version == 2
    assert record.csf_hash == sha256_hex(b"%PDF-1.4 a later constancia")
    assert record.profile.nombre == "PERSONA FISICA RENOMBRADA"
    assert harness.profiles.get(RFC, 1) == existing  # version 1 is untouched
    assert harness.profiles.get(RFC, 2) == record
    assert outcome.profile_record == record


def test_the_next_version_follows_the_latest_stored_version() -> None:
    """Versioning is `latest + 1`, not a count of whatever the store happens to hold."""
    earlier = _record(profile_version=7, csf_hash=sha256_hex(b"an earlier constancia"))
    harness = _Harness(profiles=_Profiles(existing=(earlier,), events=[]))
    harness.onboard()
    assert [record.profile_version for record in harness.profiles.saved] == [8]


def test_re_onboarding_the_same_bytes_never_re_dates_the_retained_artifact() -> None:
    """§7a: the retained original is an append-only fact — a later store must not re-date it.

    The sink reports the instant it was *asked* to retain, so a second call naturally
    reports a later one. Re-indexing that claim for a hash already on record would either
    rewrite an immutable row or (as the real store does) refuse the legitimate re-run.
    """
    events: list[str] = []
    sink = _RedatingSink(instants=[T0, T1], events=events)
    artifacts = _Artifacts(events=events)
    use_case = OnboardContributorUseCase(
        sink=sink,
        parser=_Parser(csf=_csf(), events=events),
        resolver=_Resolver(
            result=resolve_contributor_profile(configured_rfc=RFC, cert_rfc=RFC, csf=_csf()),
            events=events,
        ),
        artifacts=artifacts,
        profiles=_Profiles(events=events),
        clock=_Clock(instants=[T0, T1]),
    )

    first = use_case.onboard(CSF_PDF, client_rfc=RFC, cert_rfc=RFC)
    second = use_case.onboard(CSF_PDF, client_rfc=RFC, cert_rfc=RFC)

    assert sink.calls == [CSF_PDF, CSF_PDF]  # retaining the same bytes is idempotent
    assert first.csf_artifact.recorded_at == T0
    assert artifacts.records == [first.csf_artifact]  # one row, dated when first retained
    assert second.csf_artifact == first.csf_artifact  # the recorded fact, not the new claim


def test_the_real_stores_accept_the_versioning_and_keep_every_version(
    tmp_path: Path,
) -> None:
    """The real hash-named sink and the real repositories, end to end in-process."""
    profiles = InMemoryContributorProfileRepository()
    artifacts = InMemoryCsfArtifactRepository()
    sink = FilesystemCsfArtifactSink(tmp_path)

    def onboard(csf_bytes: bytes, csf: CsfData):
        events: list[str] = []
        return OnboardContributorUseCase(
            sink=sink,
            parser=_Parser(csf=csf, events=events),
            resolver=_Resolver(
                result=resolve_contributor_profile(configured_rfc=RFC, cert_rfc=RFC, csf=csf),
                events=events,
            ),
            artifacts=artifacts,
            profiles=profiles,
            clock=_Clock(instants=[T0, T1]),
        ).onboard(csf_bytes, client_rfc=RFC, cert_rfc=RFC)

    first = onboard(CSF_PDF, _csf())
    second = onboard(b"%PDF-1.4 a later constancia", _csf(nombre="OTRO NOMBRE"))
    assert first.profile_record is not None and second.profile_record is not None
    assert (first.profile_record.profile_version, second.profile_record.profile_version) == (1, 2)
    assert profiles.get(RFC, 1) == first.profile_record
    assert profiles.latest(RFC) == second.profile_record
    assert artifacts.get(CSF_HASH) == first.csf_artifact
    assert (tmp_path / "csf" / f"{CSF_HASH}.pdf").read_bytes() == CSF_PDF
