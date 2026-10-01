"""Tier-2: the M2-T chain — onboard a contributor (§7a), then run the Process flow (§7).

Tier-1 proves the two use cases against recording doubles. This tier crosses the seams
with production adapters only: the real `PdfCsfParser` reads the committed constancia, the
real hash-named sink retains it on disk, the real SQLite stores persist the profile and the
run, the real extractor writes the XML, the real reader re-reads it, the real satcfdi parser
and verifier consume it and the real M2.6 projection composes it. Only the clock is fixed,
so every artifact, hash and timestamp below is reproducible.

The chain runs on one file-backed database, so its own evidence can be re-opened by a second
connection — that is what makes §7a's audit chain (an accounting entry → `ContributorProfile`
→ `csf_hash` → the original CSF) checkable against the bytes on disk instead of in memory.

One honest limitation of the committed evidence: the constancia names ``WATM640917J45`` while
the CFDI fixtures are addressed to ``AAA010101AAA``/``BBB010101BBB``, so the chain's client is
the constancia's contributor and every projected document carries a `PERSPECTIVE_MISMATCH`
review fact. That is deliberate, not a defect avoided: §7 says `NEEDS_REVIEW` is not a
failure, and this chain asserts exactly that end to end.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from fixtures.constancia_builder import build_constancia_bytes
from fixtures.package_builder import FIXTURES, build_package_bytes
from sat_descarga_masiva.application.policies.extraction import ExtractionPolicy
from sat_descarga_masiva.application.use_cases.onboard_contributor import (
    OnboardContributorUseCase,
    OnboardingOutcome,
)
from sat_descarga_masiva.application.use_cases.process_client import ExecuteProcessUseCase
from sat_descarga_masiva.application.use_cases.process_documents import ProcessDocumentsUseCase
from sat_descarga_masiva.domain.errors import CsfParseError
from sat_descarga_masiva.domain.model.contributor import (
    ContributorProfileRecord,
    ObligacionFiscal,
    PersonaTipo,
    RegimenFiscal,
)
from sat_descarga_masiva.domain.model.csf import CsfArtifact
from sat_descarga_masiva.domain.model.documents import DocumentRecord
from sat_descarga_masiva.domain.model.ledger import JobStatus
from sat_descarga_masiva.domain.model.perspective import Perspective
from sat_descarga_masiva.domain.model.pipeline_run import PipelineFlow, PipelineRun
from sat_descarga_masiva.domain.model.review import (
    ReviewFlagRecord,
    ReviewFlagType,
    ReviewSubjectKind,
)
from sat_descarga_masiva.domain.model.source import ExtractedXml, sha256_hex
from sat_descarga_masiva.domain.model.value_objects import PackageId, Rfc, Uuid
from sat_descarga_masiva.fiscal.onboarding import resolve_contributor_profile
from sat_descarga_masiva.fiscal.projection import project_document
from sat_descarga_masiva.infrastructure.csf.pdf_parser import PdfCsfParser
from sat_descarga_masiva.infrastructure.fiscal.satcfdi_fiscal_parser import SatcfdiFiscalParser
from sat_descarga_masiva.infrastructure.fiscal.satcfdi_signature_verifier import (
    SatcfdiSignatureVerifier,
)
from sat_descarga_masiva.infrastructure.persistence.sqlite import (
    SqliteContributorProfileRepository,
    SqliteCsfArtifactRepository,
    SqliteDocumentRepository,
    SqlitePipelineRunRepository,
    SqliteReviewFlagStore,
    init_schema,
)
from sat_descarga_masiva.infrastructure.persistence.transactions import SqliteUnitOfWork
from sat_descarga_masiva.infrastructure.sat.extract.extractor import SafeZipExtractor
from sat_descarga_masiva.infrastructure.source.csf_sink import FilesystemCsfArtifactSink
from sat_descarga_masiva.infrastructure.source.extracted_reader import (
    FilesystemExtractedXmlReader,
)

#: The RFC the committed constancia names — the client this chain onboards and processes.
CSF_RFC = Rfc("WATM640917J45")
#: A different RFC, for the mismatched-identity leg.
OTHER_RFC = Rfc("AAA010101AAA")
#: §7a's generic national identity, for the generic-RFC leg (M2-E).
GENERIC_NACIONAL_RFC = Rfc("XAXX010101000")
#: The TFD UUID the committed `cfdi_tfd_4_0.xml` fixture carries.
TFD_UUID = "123E4567-E89B-12D3-A456-426614174000"
PACKAGE_ID = PackageId("4e80345d-917f-40bb-a98f-4a73939353c5_01")
POLICY = ExtractionPolicy(max_total_bytes=1024 * 1024, max_entries=100)
PERIOD = "2026-01"
RUN_ID = "run-2026-01"
#: The instant onboarding observed the constancia (its own leg, its own clock).
T_ENROLL = datetime(2025, 12, 1, tzinfo=UTC)
#: The run's instants: started -> the stage persisted the document -> finished.
T0 = datetime(2026, 1, 1, tzinfo=UTC)
T1 = datetime(2026, 1, 1, 0, 30, tzinfo=UTC)
T2 = datetime(2026, 2, 1, tzinfo=UTC)
#: The instant the sink retained the original — deliberately not a use-case clock reading,
#: so the chain proves which clock the persisted provenance comes from.
CSF_RETAINED_AT = datetime(2025, 11, 30, tzinfo=UTC)

CSF_FIXTURE = FIXTURES / "constancia_situacion_fiscal.pdf"


class _Clock:
    """A fixed clock: the chain is reproducible, so time is an input, not a side effect."""

    def __init__(self, *times: datetime) -> None:
        self._times = times
        self.calls = 0

    def now(self) -> datetime:
        self.calls += 1
        return self._times[min(self.calls - 1, len(self._times) - 1)]


@dataclass(frozen=True)
class Chain:
    """The production chain over one file-backed database, plus read-back helpers."""

    root: Path
    db_path: Path
    conn: sqlite3.Connection
    onboard_clock: _Clock
    run_clock: _Clock
    onboard_use_case: OnboardContributorUseCase
    process_use_case: ExecuteProcessUseCase

    def csf_bytes(self) -> bytes:
        return CSF_FIXTURE.read_bytes()

    def onboard(
        self,
        *,
        configured: Rfc = CSF_RFC,
        cert: Rfc = CSF_RFC,
        csf_bytes: bytes | None = None,
    ) -> OnboardingOutcome:
        return self.onboard_use_case.onboard(
            self.csf_bytes() if csf_bytes is None else csf_bytes,
            client_rfc=configured,
            cert_rfc=cert,
        )

    def extract(self, fixture: str) -> ExtractedXml:
        """Run the real extractor over a one-member package and return its artifact."""
        content = build_package_bytes((fixture,))
        artifacts = SafeZipExtractor(self.root, POLICY).extract(PACKAGE_ID, content)
        if len(artifacts) != 1:  # pragma: no cover - a one-member package yields one artifact
            raise AssertionError(f"expected one artifact for {fixture}, got {len(artifacts)}")
        return artifacts[0]

    def run(self, artifacts: tuple[ExtractedXml, ...], *, client_rfc: Rfc = CSF_RFC) -> PipelineRun:
        return self.process_use_case.run(
            artifacts, client_rfc=client_rfc, period=PERIOD, run_id=RUN_ID
        )

    def profile(self, client_rfc: Rfc = CSF_RFC) -> ContributorProfileRecord | None:
        return SqliteContributorProfileRepository(self.conn).latest(client_rfc)

    def csf_artifact(self, sha256: str) -> CsfArtifact | None:
        return SqliteCsfArtifactRepository(self.conn).get(sha256)

    def stored_csf_path(self) -> Path:
        return self.root / "csf" / f"{sha256_hex(self.csf_bytes())}.pdf"

    def document(self, uuid: str = TFD_UUID) -> DocumentRecord | None:
        return SqliteDocumentRepository(self.conn).get(Uuid(uuid))

    def open_flags(self, uuid: str = TFD_UUID) -> tuple[ReviewFlagRecord, ...]:
        return SqliteReviewFlagStore(self.conn).open_flags(
            ReviewSubjectKind.DOCUMENT, Uuid(uuid).value
        )

    def run_row(self, run_id: str = RUN_ID) -> PipelineRun | None:
        return SqlitePipelineRunRepository(self.conn).get(run_id)

    def count(self, table: str) -> int:
        # `table` is a literal the test itself passes, never any external input.
        cursor = self.conn.execute(f"SELECT COUNT(*) FROM {table}")
        return int(cursor.fetchone()[0])


def _chain(
    tmp_path: Path,
    *,
    onboard_times: tuple[datetime, ...] = (T_ENROLL,),
    run_times: tuple[datetime, ...] = (T0, T1, T2),
) -> Chain:
    """Wire the production chain; the stage's stores commit through the real unit of work."""
    root = tmp_path / "source"
    db_path = tmp_path / "m2t.sqlite3"
    conn = sqlite3.connect(db_path)
    init_schema(conn)
    onboard_clock = _Clock(*onboard_times)
    run_clock = _Clock(*run_times)
    return Chain(
        root=root,
        db_path=db_path,
        conn=conn,
        onboard_clock=onboard_clock,
        run_clock=run_clock,
        onboard_use_case=OnboardContributorUseCase(
            sink=FilesystemCsfArtifactSink(root, lambda: CSF_RETAINED_AT),
            parser=PdfCsfParser(),
            resolver=resolve_contributor_profile,
            artifacts=SqliteCsfArtifactRepository(conn),
            profiles=SqliteContributorProfileRepository(conn),
            clock=onboard_clock,
        ),
        process_use_case=ExecuteProcessUseCase(
            profiles=SqliteContributorProfileRepository(conn),
            runs=SqlitePipelineRunRepository(conn),
            stage=ProcessDocumentsUseCase(
                reader=FilesystemExtractedXmlReader(root),
                parser=SatcfdiFiscalParser(),
                verifier=SatcfdiSignatureVerifier(),
                projector=project_document,
                documents=SqliteDocumentRepository(conn, commit=False),
                flags=SqliteReviewFlagStore(conn, commit=False),
                transactions=SqliteUnitOfWork(conn),
                clock=run_clock,
            ),
            clock=run_clock,
        ),
    )


# --- leg A: onboarding the contributor (§7a) -------------------------------


def test_the_committed_constancia_parses_to_the_client_identity(tmp_path: Path) -> None:
    """The fixture is the evidence: what the real parser reads is what the chain resolves."""
    chain = _chain(tmp_path)
    csf = PdfCsfParser().parse(chain.csf_bytes())
    assert csf.rfc == CSF_RFC
    assert csf.nombre == "PRUEBA PERSONA FISICA"
    assert csf.regimen_fiscal.code == "612"
    assert csf.fecha_inicio_operaciones is not None


def test_onboarding_retains_the_constancia_and_persists_profile_version_one(
    tmp_path: Path,
) -> None:
    """§7a's audit chain, on disk: profile -> csf_hash -> the original constancia."""
    chain = _chain(tmp_path)
    outcome = chain.onboard()
    digest = sha256_hex(chain.csf_bytes())

    assert outcome.onboarded is True
    assert outcome.identity.matches is True
    assert outcome.csf_artifact.sha256 == digest
    assert outcome.csf_artifact.recorded_at == CSF_RETAINED_AT
    assert chain.stored_csf_path().read_bytes() == chain.csf_bytes()
    assert chain.csf_artifact(digest) == outcome.csf_artifact
    assert outcome.csf_artifact.stored_path == str(chain.stored_csf_path())

    profile = chain.profile()
    assert profile is not None
    assert profile == outcome.profile_record
    assert profile.profile_version == 1
    assert profile.csf_hash == digest
    assert (profile.csf_obtained_at, profile.recorded_at) == (T_ENROLL, T_ENROLL)
    assert profile.profile.rfc == CSF_RFC
    assert profile.profile.regimen_fiscal.code == "612"
    assert chain.count("contributor_profiles") == 1
    assert chain.count("csf_artifacts") == 1


# --- leg A'': the M2-E identity facts through the real chain (§7a) ----------------


def test_the_golden_constancia_carries_its_obligations_into_the_stored_profile(
    tmp_path: Path,
) -> None:
    """M2-E on the committed evidence: real PDF -> real parser -> real sqlite row."""
    chain = _chain(tmp_path)
    chain.onboard()

    profile = chain.profile()
    assert profile is not None
    assert profile.profile.obligaciones == (
        ObligacionFiscal("3", "Declarar anualmente el ISR"),
        ObligacionFiscal("33", "Declarar mensualmente el ISR por actividades empresariales"),
        ObligacionFiscal("9", "Declarar mensualmente el IVA."),
    )
    assert profile.profile.codigo_postal is None  # the fixture states none
    assert chain.count("profile_obligaciones") == 3


def test_a_constancia_with_a_postal_code_and_obligations_reaches_the_stores(
    tmp_path: Path,
) -> None:
    """The facts are written as rows: the order is the source order, and a peer re-reads them."""
    chain = _chain(tmp_path)
    constancia = build_constancia_bytes(
        "Régimen Fiscal: 612 - Personas físicas con actividades empresariales",
        codigo_postal_lines=("Código Postal: 97000",),
        obligaciones_lines=(
            "Obligaciones: 3 Declarar anualmente el ISR; 9 Declarar mensualmente el IVA.",
        ),
    )

    outcome = chain.onboard(csf_bytes=constancia)

    assert outcome.onboarded is True
    profile = chain.profile()
    assert profile is not None
    assert profile.profile.codigo_postal == "97000"
    assert profile.profile.obligaciones == (
        ObligacionFiscal("3", "Declarar anualmente el ISR"),
        ObligacionFiscal("9", "Declarar mensualmente el IVA."),
    )
    rows = chain.conn.execute(
        "SELECT ordinal, code, description FROM profile_obligaciones ORDER BY ordinal"
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        (0, "3", "Declarar anualmente el ISR"),
        (1, "9", "Declarar mensualmente el IVA."),
    ]
    other = sqlite3.connect(chain.db_path)  # the row reconstructs from its own child rows
    assert SqliteContributorProfileRepository(other).latest(CSF_RFC) == profile


def test_a_generic_rfc_keeps_the_generic_identity_the_constancia_cannot_state(
    tmp_path: Path,
) -> None:
    """D8 at the real seams: the constancia says Física, the resolved profile does not."""
    chain = _chain(tmp_path)
    constancia = build_constancia_bytes(
        "Régimen Fiscal: 612 - Personas físicas con actividades empresariales",
        rfc=GENERIC_NACIONAL_RFC.value,
    )
    assert PdfCsfParser().parse(constancia).persona_tipo is PersonaTipo.FISICA

    outcome = chain.onboard(
        configured=GENERIC_NACIONAL_RFC, cert=GENERIC_NACIONAL_RFC, csf_bytes=constancia
    )

    assert outcome.onboarded is True
    assert outcome.profile_record is not None
    assert outcome.profile_record.profile.persona_tipo is PersonaTipo.GENERICO_NACIONAL
    stored = chain.profile(GENERIC_NACIONAL_RFC)
    assert stored is not None
    assert stored.profile.persona_tipo is PersonaTipo.GENERICO_NACIONAL


def test_re_onboarding_the_identical_constancia_writes_no_new_version(tmp_path: Path) -> None:
    """D4: the same evidence and the same facts are one confirmed version, not two."""
    chain = _chain(tmp_path)
    first = chain.onboard()
    second = chain.onboard()

    assert second.profile_record == first.profile_record
    assert chain.count("contributor_profiles") == 1  # no `latest + 1` copy
    assert chain.count("csf_artifacts") == 1
    assert len(list((chain.root / "csf").iterdir())) == 1  # one retained original


def test_a_mismatched_configured_rfc_keeps_the_csf_but_resolves_no_profile(
    tmp_path: Path,
) -> None:
    """§7a: retention is evidence, onboarding is a claim — only the claim is withheld."""
    chain = _chain(tmp_path)
    outcome = chain.onboard(configured=OTHER_RFC, cert=OTHER_RFC)

    assert outcome.onboarded is False
    assert outcome.profile_record is None
    assert outcome.identity.matches is False
    assert "configured-vs-csf" in outcome.identity.mismatched_pairs
    assert outcome.csf_artifact.sha256 == sha256_hex(chain.csf_bytes())
    assert chain.stored_csf_path().read_bytes() == chain.csf_bytes()
    assert chain.csf_artifact(outcome.csf_artifact.sha256) == outcome.csf_artifact
    assert chain.count("csf_artifacts") == 1
    assert chain.count("contributor_profiles") == 0
    assert chain.profile() is None
    assert chain.profile(OTHER_RFC) is None


# --- leg A': an undetermined régime is an onboarding error, not review work (§7a) --


def test_an_ambiguous_regimen_fails_onboarding_and_never_reaches_the_process_flow(
    tmp_path: Path,
) -> None:
    """§7a: an undetermined régime is a `CsfParseError`, not `NEEDS_REVIEW`.

    The constancia carries two `Régimen Fiscal` occurrences, so which one is current
    cannot be determined. The evidence is still retained (§7a: immutable source
    artifact), what is withheld is the profile — so the client cannot enter the Process
    flow at all: no profile, no documents, and the run fails at `onboarding`.
    """
    chain = _chain(tmp_path)
    ambiguous = build_constancia_bytes(
        "Régimen Fiscal: 601 - General de Ley Personas Morales",
        "Régimen Fiscal: 612 - Personas físicas con actividades empresariales",
    )

    with pytest.raises(CsfParseError):
        chain.onboard(csf_bytes=ambiguous)

    digest = sha256_hex(ambiguous)
    retained = chain.csf_artifact(digest)
    assert retained is not None
    assert retained.recorded_at == CSF_RETAINED_AT
    assert Path(retained.stored_path).read_bytes() == ambiguous
    assert chain.count("csf_artifacts") == 1
    assert chain.count("contributor_profiles") == 0
    assert chain.profile() is None

    with pytest.raises(CsfParseError):  # a repeated failure is still a parse failure
        chain.onboard(csf_bytes=ambiguous)
    assert chain.count("csf_artifacts") == 1  # the retained original is not re-dated
    assert chain.csf_artifact(digest) == retained

    returned = chain.run((chain.extract("cfdi_tfd_4_0.xml"),))
    assert returned.status is JobStatus.FAILED
    assert returned.failed_stage == "onboarding"
    assert chain.count("documents") == 0
    assert chain.count("review_flags") == 0  # no review vocabulary carries this


def test_an_unknown_but_determinate_regimen_onboards_and_persists_verbatim(
    tmp_path: Path,
) -> None:
    """§7a: recognition is not M2's job — `999 - Foo Bar Desconocido` is determinate."""
    chain = _chain(tmp_path)
    constancia = build_constancia_bytes("Régimen Fiscal: 999 - Foo Bar Desconocido")

    outcome = chain.onboard(csf_bytes=constancia)

    assert outcome.onboarded is True
    assert outcome.identity.matches is True
    profile = chain.profile()
    assert profile is not None
    assert profile.profile.regimen_fiscal == RegimenFiscal("999", "Foo Bar Desconocido")
    assert profile.csf_hash == sha256_hex(constancia)
    artifact = chain.csf_artifact(profile.csf_hash)
    assert artifact is not None
    assert Path(artifact.stored_path).read_bytes() == constancia  # the §7a audit chain
    assert chain.count("contributor_profiles") == 1


# --- leg B: the Process flow for the onboarded client (§7) -----------------


def test_the_process_flow_completes_a_run_for_the_onboarded_client(tmp_path: Path) -> None:
    chain = _chain(tmp_path)
    chain.onboard()
    artifact = chain.extract("cfdi_tfd_4_0.xml")

    returned = chain.run((artifact,))

    assert returned.flow is PipelineFlow.PROCESS
    assert returned.client_rfc == CSF_RFC
    assert returned.period == PERIOD
    assert (returned.status, returned.failed_stage) == (JobStatus.COMPLETED, None)
    assert (returned.started_at, returned.finished_at) == (T0, T2)
    assert [report.stage for report in returned.per_stage] == ["fiscal"]
    assert dict(returned.per_stage[0].counts)["parsed"] == 1
    assert chain.run_row() == returned  # the §7 run row, read back from the database
    assert chain.run_clock.calls == 3  # start, the persisted document, the finish


def test_the_run_persisted_the_document_the_extraction_derived(tmp_path: Path) -> None:
    chain = _chain(tmp_path)
    chain.onboard()
    artifact = chain.extract("cfdi_tfd_4_0.xml")
    chain.run((artifact,))

    record = chain.document()
    assert record is not None
    assert record.uuid == Uuid(TFD_UUID)
    assert record.contributor_rfc == CSF_RFC
    assert record.source_hash == artifact.sha256  # the bytes on disk, not a re-parse
    assert record.last_run_id == RUN_ID
    assert record.first_seen_at == T1  # stamped by the stage, mid-run, not by the run's start
    assert record.perspective is Perspective.UNDETERMINED


def test_a_perspective_mismatch_is_review_work_and_not_a_run_failure(tmp_path: Path) -> None:
    """§7: NEEDS_REVIEW is not a failure — the run completed and the facts are open."""
    chain = _chain(tmp_path)
    chain.onboard()
    chain.run((chain.extract("cfdi_tfd_4_0.xml"),))

    flags = chain.open_flags()
    types = [entry.flag.flag_type for entry in flags]
    assert set(types) == {ReviewFlagType.CFDI_SIGNATURE, ReviewFlagType.PERSPECTIVE_MISMATCH}
    assert chain.count("review_flags") == len(types)
    assert all(entry.occurred_at == T1 for entry in flags)
    assert all(entry.flag.reason for entry in flags)
    assert chain.run_row().status is JobStatus.COMPLETED


def test_a_batch_with_nothing_usable_fails_the_run_at_the_fiscal_stage(tmp_path: Path) -> None:
    """§7: "FAILED = nothing parsed ⇒ client error" — the stage's counts decide the run."""
    chain = _chain(tmp_path)
    chain.onboard()
    unusable = chain.extract("cfdi_ingreso_3_3.xml")  # unsupported version: quarantined

    returned = chain.run((unusable,))

    assert returned.status is JobStatus.FAILED
    assert returned.failed_stage == "fiscal"
    assert dict(returned.per_stage[0].counts) == {
        "parsed": 0,
        "quarantined": 1,
        "failed": 0,
        "conflicted": 0,
    }
    assert "3.3" in (returned.per_stage[0].message or "")
    assert chain.count("documents") == 0
    assert chain.count("review_flags") == 0
    assert chain.run_clock.calls == 2  # the run's two readings; nothing was timestamped


def test_the_process_flow_refuses_a_client_that_was_never_onboarded(tmp_path: Path) -> None:
    """§7:148 — the process flow requires a resolved `ContributorProfile`."""
    chain = _chain(tmp_path)  # no onboarding at all
    artifact = chain.extract("cfdi_tfd_4_0.xml")

    returned = chain.run((artifact,))

    assert returned.status is JobStatus.FAILED
    assert returned.failed_stage == "onboarding"
    assert [report.stage for report in returned.per_stage] == ["onboarding"]
    assert CSF_RFC.value in (returned.per_stage[0].message or "")
    assert chain.count("documents") == 0
    assert chain.count("pipeline_runs") == 1
    assert chain.run_row() == returned


def test_the_whole_chain_writes_only_the_tables_m2_owns(tmp_path: Path) -> None:
    """§4/D5: M2 appends no `fiscal_event` — the first rows come with M3's observations."""
    chain = _chain(tmp_path)
    chain.onboard()
    chain.run((chain.extract("cfdi_tfd_4_0.xml"),))

    assert chain.count("contributor_profiles") == 1
    assert chain.count("csf_artifacts") == 1
    assert chain.count("documents") == 1
    assert chain.count("pipeline_runs") == 1
    assert chain.count("fiscal_events") == 0


def test_the_audit_chain_reads_back_from_a_second_connection(tmp_path: Path) -> None:
    """§7a after the fact: an entry → `ContributorProfile` → `csf_hash` → the original CSF."""
    chain = _chain(tmp_path)
    chain.onboard()
    chain.run((chain.extract("cfdi_tfd_4_0.xml"),))

    other = sqlite3.connect(chain.db_path)  # a fresh reader of the same evidence
    profile = SqliteContributorProfileRepository(other).latest(CSF_RFC)
    assert profile is not None
    artifact = SqliteCsfArtifactRepository(other).get(profile.csf_hash)
    assert artifact is not None
    retained = Path(artifact.stored_path).read_bytes()
    assert retained == CSF_FIXTURE.read_bytes()
    assert sha256_hex(retained) == profile.csf_hash
    assert chain.document().contributor_rfc == profile.profile.rfc  # type: ignore[union-attr]
