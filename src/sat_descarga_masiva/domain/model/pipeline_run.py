"""PipelineRun — one flow execution for one client (§7). Domain-only.

§7: "Both flows log to `/logs/<run-timestamp>.log` and persist
`PipelineRun{run_id, flow, client_rfc, period, status, failed_stage,
per_stage{counts,message}}`".

Representation choices, each traced to §7:
- ``flow`` names exactly the two flows §7 specifies ("**Download flow:**",
  "**Process flow …:**"); no extra flows are invented.
- ``status`` reuses the existing M1 processing-state vocabulary
  (:class:`JobStatus` — RUNNING/COMPLETED/FAILED) instead of minting a second
  one for the same lifecycle.
- ``period`` is the ``YYYY-MM`` period §7/§9 use ("don't let the agent invent
  period semantics"); it stays a plain string, absent for a Download flow.
- ``started_at``/``finished_at`` are the timestamps the run log is named after
  (§7: ``/logs/<run-timestamp>.log``) and the operational fields §7's
  "status/failed_stage/per_stage" update during a run.

Unlike the ledger's append-only facts, a `PipelineRun` is a *processing-state*
record: its own run row legitimately moves (RUNNING -> COMPLETED/FAILED).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from sat_descarga_masiva.domain.model.ledger import JobStatus
from sat_descarga_masiva.domain.model.value_objects import Rfc


class PipelineFlow(StrEnum):
    """The two flows of §7 — Download (SAT acquisition) and Process (period)."""

    DOWNLOAD = "download"
    PROCESS = "process"


@dataclass(frozen=True)
class StageReport:
    """Per-stage outcome for one run: §7's ``per_stage{counts,message}``.

    ``counts`` is an ordered ``(key, count)`` tuple so a stage can report its own
    counters without this module enumerating stage-specific counter *names*
    (which AGENT.md does not specify).
    """

    stage: str
    message: str | None = None
    counts: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class PipelineRun:
    """One Download or Process run for one client."""

    run_id: str
    flow: PipelineFlow
    client_rfc: Rfc
    status: JobStatus
    started_at: datetime
    period: str | None = None
    failed_stage: str | None = None
    per_stage: tuple[StageReport, ...] = ()
    finished_at: datetime | None = None
