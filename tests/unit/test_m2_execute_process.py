"""M2-T W3: ExecuteProcessUseCase — the Process flow's run, stage and status (§7).

One invocation of §7's Process flow for one client:

    validate the period -> record the attempt RUNNING -> run the fiscal stage
                        -> map the stage's counts onto the run's status

Decisions, each traceable:

- **A run is an execution of a flow, not a second copy of the stage.** §7 keeps
  `fiscal` as its own stage with its own report (M2.8); this use case adds the
  run-level facts §7 asks for — `flow`, `client_rfc`, `period`, `status`,
  `failed_stage`, `per_stage` — and delegates every document decision untouched.
- **The attempt is recorded before it happens.** The RUNNING row is written first,
  so a stage failure still leaves a run row that says which stage failed (§7:
  "a stage (infrastructure) failure prevents later stages for that client").
- **§7's own status rule.** `FAILED` is "nothing parsed ⇒ client error" and
  `PARTIAL` proceeds: so a run is `COMPLETED` as soon as the stage parsed at least
  one document, and `FAILED` when it parsed none — including the all-quarantined
  and empty-set cases (D7). No new status vocabulary is minted: `JobStatus` already
  is RUNNING/COMPLETED/FAILED.
- **The process flow requires a resolved profile.** §7:148 — "requires a resolved
  `ContributorProfile`"; with none stored, the run fails its pre-condition before
  any fiscal work and no stage runs. That is §7's "per-client error": it is
  reported on this client's run row, not raised as a batch-aborting fault.
- **A stage fault is recorded and then propagates.** §7 wants `failed_stage`
  persisted; M2.8's rule is that a store-integrity failure must never be
  swallowed. Both hold: the row is saved as FAILED at that stage and the exception
  continues to the caller.
- **`period` is orchestration metadata.** §7/§9 use `YYYY-MM`, so it is validated
  as that shape and stored verbatim; it selects nothing — the artifact set is the
  caller's, and no filtering, discovery or dedup happens here (D2/D3).
"""

from __future__ import annotations

import ast
import inspect
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sat_descarga_masiva.application.use_cases import process_client as process_client_module
from sat_descarga_masiva.application.use_cases.process_client import ExecuteProcessUseCase
from sat_descarga_masiva.domain.errors import ExtractionError
from sat_descarga_masiva.domain.model.contributor import (
    ContributorProfile,
    ContributorProfileRecord,
    PersonaTipo,
    RegimenFiscal,
    SituacionFiscal,
)
from sat_descarga_masiva.domain.model.ledger import JobStatus
from sat_descarga_masiva.domain.model.pipeline_run import PipelineFlow, PipelineRun, StageReport
from sat_descarga_masiva.domain.model.source import ExtractedXml
from sat_descarga_masiva.domain.model.value_objects import Period, Rfc

RFC = Rfc("WATM640917J45")
RUN_ID = "run-2026-01"
PERIOD = "2026-01"
T0 = datetime(2026, 1, 1, tzinfo=UTC)
T1 = datetime(2026, 1, 2, tzinfo=UTC)
FISCAL = "fiscal"
ONBOARDING = "onboarding"
XML_A = ExtractedXml(uuid="123E4567-E89B-12D3-A456-426614174000", tipo="ingreso", sha256="a" * 64)
XML_B = ExtractedXml(uuid="4E80345D-917F-40BB-A98F-4A73939353C5", tipo="egreso", sha256="b" * 64)


def _report(**counts: int) -> StageReport:
    return StageReport(
        stage=FISCAL, message=None, counts=tuple((name, count) for name, count in counts.items())
    )


def _parsed(count: int = 1) -> StageReport:
    return _report(parsed=count, quarantined=0, failed=0, conflicted=0)


def _profile_record() -> ContributorProfileRecord:
    return ContributorProfileRecord(
        profile=ContributorProfile(
            rfc=RFC,
            nombre="PERSONA FISICA DE PRUEBA",
            persona_tipo=PersonaTipo.FISICA,
            regimen_fiscal=RegimenFiscal("612", "Personas fisicas con actividades empresariales"),
            situacion_fiscal=SituacionFiscal.ACTIVO,
        ),
        csf_hash="c" * 64,
        csf_obtained_at=T0,
        profile_version=1,
        recorded_at=T0,
    )


@dataclass
class _Clock:
    instants: list[datetime]
    calls: int = 0

    def now(self) -> datetime:
        instant = self.instants[self.calls]
        self.calls += 1
        return instant


class _Runs:
    """`PipelineRunRepository`: an upsert of the run's operational fields."""

    def __init__(self) -> None:
        self.saved: list[PipelineRun] = []
        self.rows: dict[str, PipelineRun] = {}

    def save(self, run: PipelineRun) -> None:
        self.saved.append(run)
        self.rows[run.run_id] = run

    def get(self, run_id: str) -> PipelineRun | None:
        return self.rows.get(run_id)

    def for_client(self, client_rfc: Rfc) -> tuple[PipelineRun, ...]:
        return tuple(run for run in self.rows.values() if run.client_rfc == client_rfc)


class _Profiles:
    """Only the read the pre-condition needs: `latest`."""

    def __init__(self, latest: ContributorProfileRecord | None = None) -> None:
        self._latest = latest
        self.reads: list[Rfc] = []

    def latest(self, client_rfc: Rfc) -> ContributorProfileRecord | None:
        self.reads.append(client_rfc)
        return self._latest


@dataclass
class _Stage:
    """Stands in for M2.8's `ProcessDocumentsUseCase`."""

    report: StageReport
    events: list[str]
    fault: Exception | None = None
    calls: list[tuple[tuple[ExtractedXml, ...], Rfc, str | None]] = field(default_factory=list)

    def process(
        self,
        artifacts: tuple[ExtractedXml, ...],
        *,
        contributor_rfc: Rfc,
        run_id: str | None = None,
    ) -> StageReport:
        self.calls.append((artifacts, contributor_rfc, run_id))
        self.events.append("fiscal_stage")
        if self.fault is not None:
            raise self.fault
        return self.report


@dataclass
class _Harness:
    """The use case plus every double it was wired to, with one shared event log."""

    use_case: ExecuteProcessUseCase
    runs: _Runs
    profiles: _Profiles
    stage: _Stage
    clock: _Clock
    events: list[str]

    def run(self, artifacts: tuple[ExtractedXml, ...] = (XML_A,)) -> PipelineRun:
        return self.use_case.run(artifacts, client_rfc=RFC, period=PERIOD, run_id=RUN_ID)


def _harness(
    *,
    report: StageReport | None = None,
    onboarded: bool = True,
    stage_fault: Exception | None = None,
) -> _Harness:
    events: list[str] = []
    runs = _Runs()
    profiles = _Profiles(latest=_profile_record() if onboarded else None)
    clock = _Clock(instants=[T0, T1])
    stage = _Stage(
        report=report if report is not None else _parsed(), events=events, fault=stage_fault
    )
    return _Harness(
        use_case=ExecuteProcessUseCase(profiles=profiles, runs=runs, stage=stage, clock=clock),
        runs=runs,
        profiles=profiles,
        stage=stage,
        clock=clock,
        events=events,
    )


def test_the_attempt_is_recorded_running_before_the_stage_runs() -> None:
    harness = _harness()
    returned = harness.run()
    running = harness.runs.saved[0]
    assert running.status is JobStatus.RUNNING
    assert running.flow is PipelineFlow.PROCESS
    assert running.client_rfc == RFC
    assert running.period == PERIOD
    assert running.run_id == RUN_ID
    assert running.started_at == T0
    assert (running.finished_at, running.failed_stage) == (None, None)
    assert returned.status is JobStatus.COMPLETED


def test_the_stage_receives_the_artifacts_and_the_client_identity() -> None:
    """D2/D3: the caller's artifact set goes to the stage as-is — nothing is selected."""
    harness = _harness()
    harness.run((XML_A, XML_B))
    assert harness.stage.calls == [((XML_A, XML_B), RFC, RUN_ID)]


def test_a_run_that_parsed_documents_completes() -> None:
    harness = _harness(report=_parsed(2))
    returned = harness.run()
    assert returned.status is JobStatus.COMPLETED
    assert returned.failed_stage is None
    assert returned.per_stage == (_parsed(2),)
    assert returned.finished_at == T1
    assert harness.runs.get(RUN_ID) == returned
    assert [run.status for run in harness.runs.saved] == [JobStatus.RUNNING, JobStatus.COMPLETED]


def test_a_run_that_parsed_nothing_fails_at_the_fiscal_stage() -> None:
    """§7: FAILED = nothing parsed (client error), including all-quarantined (D7)."""
    report = _report(parsed=0, quarantined=3, failed=0, conflicted=0)
    harness = _harness(report=report)
    returned = harness.run()
    assert returned.status is JobStatus.FAILED
    assert returned.failed_stage == FISCAL
    assert returned.per_stage == (report,)
    assert returned.finished_at == T1


def test_an_empty_artifact_set_fails_the_run_after_still_recording_the_attempt() -> None:
    """§7: "an empty/absent extracted set ⇒ per-client error" — the stage still runs."""
    harness = _harness(report=_report(parsed=0, quarantined=0, failed=0, conflicted=0))
    returned = harness.run(())
    assert harness.stage.calls == [((), RFC, RUN_ID)]
    assert returned.status is JobStatus.FAILED
    assert returned.failed_stage == FISCAL
    assert [run.status for run in harness.runs.saved] == [JobStatus.RUNNING, JobStatus.FAILED]


def test_a_client_without_a_resolved_profile_fails_before_any_fiscal_work() -> None:
    """§7:148 — the process flow "requires a resolved ContributorProfile"."""
    harness = _harness(onboarded=False)
    returned = harness.run()
    assert harness.profiles.reads == [RFC]
    assert harness.stage.calls == []  # no stage ran
    assert returned.status is JobStatus.FAILED
    assert returned.failed_stage == ONBOARDING
    assert [report.stage for report in returned.per_stage] == [ONBOARDING]
    assert [run.status for run in harness.runs.saved] == [JobStatus.RUNNING, JobStatus.FAILED]
    assert harness.runs.get(RUN_ID) == returned


def test_an_infrastructure_stage_failure_is_recorded_and_then_propagates() -> None:
    """§7 wants `failed_stage` persisted; M2.8's rule is never to swallow the fault."""
    harness = _harness(stage_fault=ExtractionError("extracted artifact 1 hash mismatch"))
    with pytest.raises(ExtractionError, match="hash mismatch"):
        harness.run()
    failed = harness.runs.saved[-1]
    assert failed.status is JobStatus.FAILED
    assert failed.failed_stage == FISCAL
    assert failed.finished_at == T1
    assert harness.runs.get(RUN_ID) == failed


def test_a_malformed_period_is_rejected_before_any_row_is_written() -> None:
    """D3: the period is orchestration `YYYY-MM` metadata — validated, never guessed."""
    for period in ("2026-13", "2026-1", "2026-00", "enero", "", "2026-01-01"):
        harness = _harness()
        with pytest.raises(ValueError, match="period"):
            harness.use_case.run((XML_A,), client_rfc=RFC, period=period, run_id=RUN_ID)
        assert harness.runs.saved == []
        assert harness.stage.calls == []
        assert harness.clock.calls == 0


def test_the_period_value_object_accepts_only_iso_year_month() -> None:
    assert Period("2026-01").value == "2026-01"
    assert Period("2026-12").value == "2026-12"
    with pytest.raises(ValueError, match="period"):
        Period("2026-13")


def test_the_run_is_closed_with_the_next_clock_reading() -> None:
    harness = _harness()
    harness.run()
    assert harness.clock.calls == 2
    assert [run.started_at for run in harness.runs.saved] == [T0, T0]
    assert harness.runs.saved[-1].finished_at == T1


def test_the_use_case_takes_only_its_own_collaborators() -> None:
    """No fiscal-event writer and no document repository: those belong to M2.8 (§4, D8)."""
    parameters = inspect.signature(ExecuteProcessUseCase.__init__).parameters
    collaborators = sorted(name for name in parameters if name != "self")
    assert collaborators == ["clock", "profiles", "runs", "stage"]


def test_the_process_client_module_imports_nothing_from_infrastructure_or_libraries() -> None:
    """Purity check: the run row is written through ports, never through an adapter."""
    source = Path(process_client_module.__file__ or "").read_text(encoding="utf-8")
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
        if module.startswith(("sat_descarga_masiva.infrastructure", "sqlite3", "lxml", "satcfdi"))
    ]
