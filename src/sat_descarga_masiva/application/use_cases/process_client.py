"""ExecuteProcessUseCase — the Process flow's run for one client and period (§7).

    validate the period -> record the attempt RUNNING -> run the fiscal stage
                        -> map the stage's report onto the run's status

Decisions, each traceable:

- **A run is an execution of a flow, not a second copy of the stage.** §7 gives the
  `fiscal` stage its own report (M2.8) and asks `PipelineRun` for the run-level
  facts — `flow`, `client_rfc`, `period`, `status`, `failed_stage`, `per_stage`.
  This use case supplies exactly those and delegates every per-document decision
  untouched: it counts nothing, quarantines nothing and writes no document.
- **The attempt is recorded before it happens.** §7: "a stage (infrastructure)
  failure prevents later stages for that client" — so the RUNNING row is saved
  first, and a failure still leaves a row naming the stage that failed.
- **§7's own status rule, no new vocabulary.** §7 says `FAILED` = "nothing parsed
  ⇒ client error" and that `PARTIAL` proceeds with a quarantine count, so the run
  is `COMPLETED` once the stage parsed at least one document and `FAILED` when it
  parsed none (all-quarantined and empty sets included — D7). `status` stays the
  existing `JobStatus`; `NEEDS_REVIEW` is not a failure and is not modelled here.
- **The process flow requires a resolved profile.** §7:148 — "requires a resolved
  `ContributorProfile`". With none stored, this client's run fails its
  pre-condition before any fiscal work: no stage runs, and the failure is reported
  on the client's own run row (that is §7's "per-client error") rather than raised
  as a fault that would abort a batch of other clients.
- **A stage fault is recorded, then propagates.** §7 wants `failed_stage`
  persisted; M2.8's rule is that a store-integrity failure is never swallowed.
  Catching the technical-failure hierarchy satisfies both: the row is closed as
  FAILED at that stage and the exception continues to the caller. Anything outside
  that hierarchy is a defect and surfaces untouched.
- **The period is validated metadata.** §7/§9 write periods as `YYYY-MM`, so it is
  validated up front (before any row is written) and stored verbatim. It selects
  nothing: the artifact set is the caller's, and no discovery, filtering or dedup
  happens here (D2/D3).
"""

from __future__ import annotations

from dataclasses import replace

from sat_descarga_masiva.application.ports.persistence import (
    ContributorProfileRepository,
    PipelineRunRepository,
)
from sat_descarga_masiva.application.ports.services import Clock
from sat_descarga_masiva.application.use_cases.process_documents import (
    STAGE as FISCAL_STAGE,
)
from sat_descarga_masiva.application.use_cases.process_documents import (
    ProcessDocumentsUseCase,
)
from sat_descarga_masiva.domain.errors import SatClientError
from sat_descarga_masiva.domain.model.ledger import JobStatus
from sat_descarga_masiva.domain.model.pipeline_run import PipelineFlow, PipelineRun, StageReport
from sat_descarga_masiva.domain.model.source import ExtractedXml
from sat_descarga_masiva.domain.model.value_objects import Period, Rfc

STAGE_ONBOARDING = "onboarding"
"""§7a's onboarding step, the process flow's pre-condition (it is not a §7 stage)."""

PARSED_COUNT = "parsed"
"""The `fiscal` stage's counter for "became a current fiscal projection" (§7's rule)."""


class ExecuteProcessUseCase:
    """Run §7's Process flow for one client and period."""

    def __init__(
        self,
        *,
        profiles: ContributorProfileRepository,
        runs: PipelineRunRepository,
        stage: ProcessDocumentsUseCase,
        clock: Clock,
    ) -> None:
        self._profiles = profiles
        self._runs = runs
        self._stage = stage
        self._clock = clock

    def run(
        self,
        artifacts: tuple[ExtractedXml, ...],
        *,
        client_rfc: Rfc,
        period: str,
        run_id: str,
    ) -> PipelineRun:
        """Execute the flow and return the run row as it was persisted."""
        validated_period = Period(period).value
        running = PipelineRun(
            run_id=run_id,
            flow=PipelineFlow.PROCESS,
            client_rfc=client_rfc,
            status=JobStatus.RUNNING,
            started_at=self._clock.now(),
            period=validated_period,
        )
        self._runs.save(running)
        if self._profiles.latest(client_rfc) is None:
            return self._close(
                running,
                StageReport(
                    stage=STAGE_ONBOARDING,
                    message=f"no resolved contributor profile for {client_rfc.value}",
                ),
                failed_stage=STAGE_ONBOARDING,
            )
        try:
            report = self._stage.process(artifacts, contributor_rfc=client_rfc, run_id=run_id)
        except SatClientError as exc:
            self._runs.save(
                replace(
                    running,
                    status=JobStatus.FAILED,
                    failed_stage=FISCAL_STAGE,
                    per_stage=(StageReport(stage=FISCAL_STAGE, message=str(exc)),),
                    finished_at=self._clock.now(),
                )
            )
            raise
        return self._close(running, report, failed_stage=_failure(report))

    def _close(
        self, running: PipelineRun, report: StageReport, *, failed_stage: str | None
    ) -> PipelineRun:
        """Persist the run's outcome (§7: status, `failed_stage`, `per_stage`)."""
        finished = replace(
            running,
            status=JobStatus.FAILED if failed_stage is not None else JobStatus.COMPLETED,
            failed_stage=failed_stage,
            per_stage=(report,),
            finished_at=self._clock.now(),
        )
        self._runs.save(finished)
        return finished


def _failure(report: StageReport) -> str | None:
    """§7's client error: the stage parsed nothing, so this run failed at that stage.

    The report speaks for its own stage, so the run does not have to know which
    stage ran — it only has to apply §7's rule to what the stage reported.
    """
    return None if dict(report.counts).get(PARSED_COUNT, 0) > 0 else report.stage
