"""M2.7: M2 persistence contract — in-memory and SQLite stores both pass (§12).

Behavioral assertions are shared so the two adapters cannot diverge. Scope is
the §11 M2 schema — `documents`, `fiscal_events`, `contributor_profiles` — plus
the §7a CSF audit chain (`csf_artifacts`), §8 review state (`review_flags`) and
§7 runs (`pipeline_runs`). `metadata_snapshots` is M3 (AGENT.md §11).

Two truths (§4): `documents` is a current projection (only its projection fields
move, never an authoritative status), while `fiscal_events`, review-flag facts,
CSF artifacts and profile versions are append-only.
"""

import json
import sqlite3
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast

import pytest

from sat_descarga_masiva.application.ports.persistence import (
    ContributorProfileRepository,
    CsfArtifactRepository,
    DocumentRepository,
    DownloadJobRepository,
    FiscalEventStore,
    PipelineRunRepository,
    ReviewFlagStore,
)
from sat_descarga_masiva.domain.enums.catalog import Direction, ServiceType
from sat_descarga_masiva.domain.errors import (
    ImmutableRecordConflict,
    ReviewFlagNotFound,
    SourceHashConflict,
)
from sat_descarga_masiva.domain.model.contributor import (
    ContributorProfile,
    ContributorProfileRecord,
    PersonaTipo,
    RegimenFiscal,
    SituacionFiscal,
)
from sat_descarga_masiva.domain.model.csf import CsfArtifact
from sat_descarga_masiva.domain.model.documents import DocumentRecord
from sat_descarga_masiva.domain.model.fiscal_event import FiscalEvent
from sat_descarga_masiva.domain.model.ledger import DownloadJob, JobStatus
from sat_descarga_masiva.domain.model.perspective import Perspective
from sat_descarga_masiva.domain.model.pipeline_run import PipelineFlow, PipelineRun, StageReport
from sat_descarga_masiva.domain.model.review import (
    ReviewFlag,
    ReviewFlagRecord,
    ReviewFlagState,
    ReviewFlagType,
    ReviewSubjectKind,
)
from sat_descarga_masiva.domain.model.source import sha256_hex
from sat_descarga_masiva.domain.model.value_objects import RequestId, Rfc, Uuid
from sat_descarga_masiva.domain.policy.money import (
    NormalizedAmount,
    amount_as_text,
    amount_from_text,
)
from sat_descarga_masiva.infrastructure.persistence.memory import (
    InMemoryContributorProfileRepository,
    InMemoryCsfArtifactRepository,
    InMemoryDocumentRepository,
    InMemoryDownloadJobRepository,
    InMemoryFiscalEventStore,
    InMemoryPipelineRunRepository,
    InMemoryReviewFlagStore,
)
from sat_descarga_masiva.infrastructure.persistence.sqlite import (
    SqliteContributorProfileRepository,
    SqliteCsfArtifactRepository,
    SqliteDocumentRepository,
    SqliteDownloadJobRepository,
    SqliteFiscalEventStore,
    SqlitePipelineRunRepository,
    SqliteReviewFlagStore,
)

RFC = Rfc("AAA010101AAA")
OTHER_RFC = Rfc("BBB010101BBB")
UID = Uuid("4e80345d-917f-40bb-a98f-4a73939353c5")
OTHER_UID = Uuid("5e80345d-917f-40bb-a98f-4a73939353c5")
RID = RequestId("4e80345d-917f-40bb-a98f-4a73939353c6")
HASH_A = sha256_hex(b"cfdi-a")
HASH_B = sha256_hex(b"cfdi-b")
T0 = datetime(2026, 1, 1, tzinfo=UTC)
T1 = datetime(2026, 1, 2, tzinfo=UTC)
T2 = datetime(2026, 1, 3, tzinfo=UTC)


@dataclass(frozen=True)
class Stores:
    """Every M2 store, so one test proves a behavior on both adapters."""

    documents: DocumentRepository
    events: FiscalEventStore
    profiles: ContributorProfileRepository
    csf: CsfArtifactRepository
    flags: ReviewFlagStore
    runs: PipelineRunRepository
    jobs: DownloadJobRepository


def _memory_stores() -> Stores:
    return Stores(
        documents=InMemoryDocumentRepository(),
        events=InMemoryFiscalEventStore(),
        profiles=InMemoryContributorProfileRepository(),
        csf=InMemoryCsfArtifactRepository(),
        flags=InMemoryReviewFlagStore(),
        runs=InMemoryPipelineRunRepository(),
        jobs=InMemoryDownloadJobRepository(),
    )


def _sqlite_stores() -> Stores:
    conn = sqlite3.connect(":memory:")
    return Stores(
        documents=SqliteDocumentRepository(conn),
        events=SqliteFiscalEventStore(conn),
        profiles=SqliteContributorProfileRepository(conn),
        csf=SqliteCsfArtifactRepository(conn),
        flags=SqliteReviewFlagStore(conn),
        runs=SqlitePipelineRunRepository(conn),
        jobs=SqliteDownloadJobRepository(conn),
    )


@pytest.fixture(
    params=[
        pytest.param(_memory_stores, id="in-memory"),
        pytest.param(_sqlite_stores, id="sqlite"),
    ]
)
def stores(request: pytest.FixtureRequest) -> Stores:
    return cast(Stores, request.param())


def _normalized(value: Decimal) -> NormalizedAmount:
    return NormalizedAmount(value)


def _document(**overrides: object) -> DocumentRecord:
    base: dict[str, object] = dict(
        uuid=UID,
        contributor_rfc=RFC,
        perspective=Perspective.RECIBIDO,
        tipo="I",
        version="4.0",
        source_hash=HASH_A,
        emisor_rfc=OTHER_RFC,
        first_seen_at=T0,
        last_seen_at=T0,
        receptor_rfc=RFC,
        moneda="MXN",
        last_run_id="run-1",
    )
    base.update(overrides)
    return DocumentRecord(**base)  # type: ignore[arg-type]


def _event(**overrides: object) -> FiscalEvent:
    base: dict[str, object] = dict(
        uuid=UID,
        contributor_rfc=RFC,
        kind="document_observed",
        effective_at=T0,
        recorded_at=T0,
        source_hash=HASH_A,
    )
    base.update(overrides)
    return FiscalEvent(**base)  # type: ignore[arg-type]


def _profile(profile_version: int = 1, nombre: str = "ACME SA DE CV") -> ContributorProfileRecord:
    return ContributorProfileRecord(
        profile=ContributorProfile(
            rfc=RFC,
            nombre=nombre,
            persona_tipo=PersonaTipo.MORAL,
            regimen_fiscal=RegimenFiscal(code="601", description="General de Ley Personas Morales"),
            situacion_fiscal=SituacionFiscal.ACTIVO,
        ),
        csf_hash=HASH_B,
        csf_obtained_at=T0,
        profile_version=profile_version,
        recorded_at=T1,
    )


def _artifact() -> CsfArtifact:
    return CsfArtifact(sha256=HASH_B, stored_path=f"csf/{HASH_B}.pdf", recorded_at=T0)


def _run(status: JobStatus = JobStatus.RUNNING, **overrides: object) -> PipelineRun:
    base: dict[str, object] = dict(
        run_id="run-1",
        flow=PipelineFlow.PROCESS,
        client_rfc=RFC,
        status=status,
        started_at=T0,
        period="2026-01",
    )
    base.update(overrides)
    return PipelineRun(**base)  # type: ignore[arg-type]


def _job(**overrides: object) -> DownloadJob:
    base: dict[str, object] = dict(
        job_id="job-1",
        client_rfc=RFC,
        service=ServiceType.CFDI,
        direction=Direction.RECIBIDOS,
        request_id=RID,
        query_start=T0,
        query_end=T1,
        policy_version=1,
        status=JobStatus.RUNNING,
        created_at=T0,
    )
    base.update(overrides)
    return DownloadJob(**base)  # type: ignore[arg-type]


# --- documents: current projection, never the authoritative history (§4) -------


def test_documents_round_trip(stores: Stores) -> None:
    stores.documents.save(_document())
    assert stores.documents.get(UID) == _document()


def test_documents_unknown_returns_none(stores: Stores) -> None:
    assert stores.documents.get(OTHER_UID) is None


def test_documents_resave_same_hash_updates_projection_fields_only(stores: Stores) -> None:
    stores.documents.save(_document())
    stores.documents.save(replace(_document(), last_seen_at=T1, last_run_id="run-2"))
    got = stores.documents.get(UID)
    assert got is not None
    assert got.last_seen_at == T1
    assert got.last_run_id == "run-2"
    assert got.first_seen_at == T0  # the first observation is identity, preserved


def test_documents_identity_fields_are_never_overwritten(stores: Stores) -> None:
    stores.documents.save(_document())
    stores.documents.save(
        replace(
            _document(),
            tipo="E",
            version="3.3",
            moneda="USD",
            perspective=Perspective.EMITIDO,
            first_seen_at=T2,
        )
    )
    got = stores.documents.get(UID)
    assert got is not None
    assert (got.tipo, got.version, got.moneda) == ("I", "4.0", "MXN")
    assert got.perspective is Perspective.RECIBIDO
    assert got.first_seen_at == T0


def test_documents_last_seen_never_moves_backwards(stores: Stores) -> None:
    stores.documents.save(_document(last_seen_at=T1))
    stores.documents.save(replace(_document(), last_seen_at=T0, last_run_id="run-0"))
    got = stores.documents.get(UID)
    assert got is not None
    assert got.last_seen_at == T1


def test_documents_optional_fields_round_trip_as_none(stores: Stores) -> None:
    record = replace(_document(), receptor_rfc=None, moneda=None, last_run_id=None)
    stores.documents.save(record)
    assert stores.documents.get(UID) == record


def test_documents_same_uuid_different_hash_raises_and_preserves_row(stores: Stores) -> None:
    stores.documents.save(_document())
    with pytest.raises(SourceHashConflict):
        stores.documents.save(replace(_document(), source_hash=HASH_B, last_seen_at=T2))
    assert stores.documents.get(UID) == _document()


# --- fiscal_events: the single authoritative append-only history (§4) ----------


def test_fiscal_event_append_is_readable_by_uuid(stores: Stores) -> None:
    stores.events.append(_event())
    assert stores.events.for_uuid(UID) == (_event(),)


def test_fiscal_event_unknown_uuid_returns_empty(stores: Stores) -> None:
    assert stores.events.for_uuid(OTHER_UID) == ()


def test_fiscal_event_history_is_ordered_by_append_not_by_fact_time(stores: Stores) -> None:
    first = _event(effective_at=T1, recorded_at=T1)
    second = _event(effective_at=T0, recorded_at=T2, kind="vigente_to_cancelado")
    stores.events.append(first)
    stores.events.append(second)
    assert stores.events.for_uuid(UID) == (first, second)


def test_fiscal_event_exact_duplicate_is_idempotent(stores: Stores) -> None:
    stores.events.append(_event())
    stores.events.append(_event())
    assert stores.events.for_uuid(UID) == (_event(),)


def test_fiscal_event_same_uuid_kind_and_other_hash_is_not_discarded(stores: Stores) -> None:
    stores.events.append(_event())
    stores.events.append(_event(source_hash=HASH_B))
    assert len(stores.events.for_uuid(UID)) == 2


def test_fiscal_event_same_kind_other_fact_time_is_a_new_event(stores: Stores) -> None:
    stores.events.append(_event())
    stores.events.append(_event(effective_at=T1))
    assert len(stores.events.for_uuid(UID)) == 2


def test_fiscal_event_later_recording_never_rewrites_the_fact_time(stores: Stores) -> None:
    stores.events.append(_event(effective_at=T0, recorded_at=T0))
    stores.events.append(_event(effective_at=T0, recorded_at=T2))  # refreshed observation
    assert stores.events.for_uuid(UID) == (_event(effective_at=T0, recorded_at=T0),)


def test_fiscal_event_kind_and_detail_stay_separate(stores: Stores) -> None:
    detail = json.dumps({"from": "vigente", "to": "cancelado"})
    event = _event(detail_json=detail)
    stores.events.append(event)
    got = stores.events.for_uuid(UID)[0]
    assert got.kind == "document_observed"
    assert got.detail_json == detail


def test_fiscal_event_amounts_round_trip_as_exact_text(stores: Stores) -> None:
    amount = _normalized(Decimal("123456789.01"))
    detail = json.dumps({"total": amount_as_text(amount)})
    stores.events.append(_event(detail_json=detail))
    got = stores.events.for_uuid(UID)[0]
    assert got.detail_json == detail
    payload = json.loads(got.detail_json or "{}")
    assert amount_from_text(payload["total"]) == amount
    assert "e" not in payload["total"]


def test_fiscal_event_history_is_scoped_per_uuid(stores: Stores) -> None:
    stores.events.append(_event())
    stores.events.append(_event(uuid=OTHER_UID, source_hash=HASH_B))
    assert stores.events.for_uuid(UID) == (_event(),)
    assert len(stores.events.for_uuid(OTHER_UID)) == 1


def test_fiscal_event_timestamps_are_utc_aware(stores: Stores) -> None:
    stores.events.append(_event(effective_at=datetime(2026, 1, 1, 12, 30), recorded_at=T2))
    got = stores.events.for_uuid(UID)[0]
    assert got.effective_at.utcoffset() == timedelta(0)
    assert got.recorded_at.utcoffset() == timedelta(0)


def test_fiscal_event_contributor_scope_round_trips(stores: Stores) -> None:
    stores.events.append(_event(contributor_rfc=OTHER_RFC))
    assert stores.events.for_uuid(UID)[0].contributor_rfc == OTHER_RFC


# --- contributor_profiles: immutable versions, CSF audit chain (§7a) -----------


def test_profile_versions_coexist(stores: Stores) -> None:
    stores.profiles.save(_profile(1))
    stores.profiles.save(_profile(2, nombre="ACME SA DE CV (v2)"))
    assert stores.profiles.get(RFC, 1) == _profile(1)
    assert stores.profiles.get(RFC, 2) == _profile(2, nombre="ACME SA DE CV (v2)")


def test_profile_latest_returns_the_highest_version(stores: Stores) -> None:
    stores.profiles.save(_profile(1))
    stores.profiles.save(_profile(2, nombre="ACME SA DE CV (v2)"))
    latest = stores.profiles.latest(RFC)
    assert latest is not None
    assert latest.profile_version == 2
    assert latest.profile.nombre == "ACME SA DE CV (v2)"


def test_profile_unknown_returns_none(stores: Stores) -> None:
    assert stores.profiles.get(RFC, 1) is None
    assert stores.profiles.latest(RFC) is None


def test_profile_version_resave_with_same_content_is_idempotent(stores: Stores) -> None:
    stores.profiles.save(_profile(1))
    stores.profiles.save(_profile(1))
    assert stores.profiles.get(RFC, 1) == _profile(1)


def test_profile_version_is_never_updated_in_place(stores: Stores) -> None:
    stores.profiles.save(_profile(1))
    with pytest.raises(ImmutableRecordConflict):
        stores.profiles.save(_profile(1, nombre="OTHER NAME"))
    assert stores.profiles.get(RFC, 1) == _profile(1)


def test_profile_preserves_the_csf_audit_chain(stores: Stores) -> None:
    stores.csf.record(_artifact())
    stores.profiles.save(_profile(1))
    stored = stores.profiles.get(RFC, 1)
    assert stored is not None
    assert stored.csf_hash == _artifact().sha256
    assert stores.csf.get(stored.csf_hash) == _artifact()
    assert stored.csf_obtained_at.utcoffset() == timedelta(0)


def test_profile_regimen_and_situacion_round_trip(stores: Stores) -> None:
    stores.profiles.save(_profile(1))
    stored = stores.profiles.get(RFC, 1)
    assert stored is not None
    assert stored.profile.persona_tipo is PersonaTipo.MORAL
    assert stored.profile.situacion_fiscal is SituacionFiscal.ACTIVO
    assert stored.profile.regimen_fiscal.code == "601"


# --- csf_artifacts: immutable, hash-named source artifact (§7a) ---------------


def test_csf_artifact_round_trip(stores: Stores) -> None:
    stores.csf.record(_artifact())
    assert stores.csf.get(HASH_B) == _artifact()


def test_csf_artifact_unknown_returns_none(stores: Stores) -> None:
    assert stores.csf.get(HASH_A) is None


def test_csf_artifact_same_hash_is_idempotent(stores: Stores) -> None:
    stores.csf.record(_artifact())
    stores.csf.record(_artifact())
    assert stores.csf.get(HASH_B) == _artifact()


def test_csf_artifact_is_never_repointed(stores: Stores) -> None:
    stores.csf.record(_artifact())
    with pytest.raises(ImmutableRecordConflict):
        stores.csf.record(replace(_artifact(), stored_path="csf/elsewhere.pdf"))
    assert stores.csf.get(HASH_B) == _artifact()


# --- review_flags: additive open/close facts, never a mutated row (§8) ---------


def _open_flag(stores: Stores, reason: str = "sello ausente") -> ReviewFlagRecord:
    return stores.flags.open_flag(
        subject_kind=ReviewSubjectKind.DOCUMENT,
        subject_id=UID.value,
        flag=ReviewFlag(ReviewFlagType.CFDI_SIGNATURE, reason),
        occurred_at=T0,
    )


def test_review_flag_open_flag_is_stored_as_an_open_fact(stores: Stores) -> None:
    record = _open_flag(stores)
    assert record.flag.state is ReviewFlagState.OPEN
    assert record.closes_flag_id is None
    assert record.subject_id == UID.value
    assert stores.flags.open_flags(ReviewSubjectKind.DOCUMENT, UID.value) == (record,)


def test_review_flag_unknown_subject_has_no_flags(stores: Stores) -> None:
    assert stores.flags.open_flags(ReviewSubjectKind.DOCUMENT, UID.value) == ()
    assert stores.flags.history(ReviewSubjectKind.DOCUMENT, UID.value) == ()


def test_review_flag_closure_appends_a_row_and_leaves_the_open_row_unchanged(
    stores: Stores,
) -> None:
    opened = _open_flag(stores)
    closure = stores.flags.close_flag(flag_id=opened.flag_id, occurred_at=T1)
    assert closure.flag_id != opened.flag_id
    assert closure.closes_flag_id == opened.flag_id
    assert closure.flag.state is ReviewFlagState.CLOSED
    assert closure.flag.flag_type is opened.flag.flag_type
    assert closure.flag.reason == opened.flag.reason
    # The opening row is never mutated: it is still an OPEN fact in the history.
    history = stores.flags.history(ReviewSubjectKind.DOCUMENT, UID.value)
    assert history[0] == opened
    assert history == (opened, closure)


def test_review_flag_current_state_is_reconstructed_deterministically(stores: Stores) -> None:
    first = _open_flag(stores, "sello ausente")
    second = stores.flags.open_flag(
        subject_kind=ReviewSubjectKind.DOCUMENT,
        subject_id=UID.value,
        flag=ReviewFlag(ReviewFlagType.PERSPECTIVE_UNDETERMINED, "perspectiva indeterminada"),
        occurred_at=T1,
    )
    stores.flags.close_flag(flag_id=first.flag_id, occurred_at=T2)
    assert stores.flags.open_flags(ReviewSubjectKind.DOCUMENT, UID.value) == (second,)
    assert len(stores.flags.history(ReviewSubjectKind.DOCUMENT, UID.value)) == 3


def test_review_flag_repeated_closure_creates_no_duplicate(stores: Stores) -> None:
    opened = _open_flag(stores)
    first = stores.flags.close_flag(flag_id=opened.flag_id, occurred_at=T1)
    again = stores.flags.close_flag(flag_id=opened.flag_id, occurred_at=T1)
    assert again == first
    later = stores.flags.close_flag(flag_id=opened.flag_id, occurred_at=T2)
    assert later == first  # the first closure is the fact; re-closing is a no-op
    assert len(stores.flags.history(ReviewSubjectKind.DOCUMENT, UID.value)) == 2


def test_review_flag_close_unknown_flag_raises(stores: Stores) -> None:
    with pytest.raises(ReviewFlagNotFound):
        stores.flags.close_flag(flag_id=999, occurred_at=T1)


def test_review_flag_subjects_are_isolated(stores: Stores) -> None:
    other = stores.flags.open_flag(
        subject_kind=ReviewSubjectKind.DOCUMENT,
        subject_id=OTHER_UID.value,
        flag=ReviewFlag(ReviewFlagType.CFDI_SIGNATURE, "sello ausente"),
        occurred_at=T0,
    )
    mine = _open_flag(stores)
    assert stores.flags.open_flags(ReviewSubjectKind.DOCUMENT, UID.value) == (mine,)
    assert stores.flags.open_flags(ReviewSubjectKind.DOCUMENT, OTHER_UID.value) == (other,)


def test_review_flag_timestamps_are_utc_aware(stores: Stores) -> None:
    record = stores.flags.open_flag(
        subject_kind=ReviewSubjectKind.DOCUMENT,
        subject_id=UID.value,
        flag=ReviewFlag(ReviewFlagType.UNSUPPORTED_CURRENCY, "EUR"),
        occurred_at=datetime(2026, 1, 1, 12, 30),
    )
    assert record.occurred_at.utcoffset() == timedelta(0)


# --- pipeline_runs: mutable operational record (§7) ---------------------------


def test_pipeline_run_create_and_retrieve(stores: Stores) -> None:
    stores.runs.save(_run())
    assert stores.runs.get("run-1") == _run()


def test_pipeline_run_unknown_returns_none(stores: Stores) -> None:
    assert stores.runs.get("nope") is None


def test_pipeline_run_operational_state_is_mutable(stores: Stores) -> None:
    stores.runs.save(_run())
    finished = _run(
        status=JobStatus.FAILED,
        failed_stage="fiscal",
        per_stage=(StageReport(stage="fiscal", message="stage failed", counts=(("failed", 3),)),),
        finished_at=T2,
    )
    stores.runs.save(finished)
    assert stores.runs.get("run-1") == finished


def test_pipeline_run_per_stage_counts_round_trip(stores: Stores) -> None:
    run = _run(
        per_stage=(
            StageReport(stage="source", counts=(("new", 4), ("duplicate", 2))),
            StageReport(stage="fiscal", message="needs review"),
        )
    )
    stores.runs.save(run)
    assert stores.runs.get("run-1") == run


def test_pipeline_run_correlates_with_download_job(stores: Stores) -> None:
    run = _run()
    stores.runs.save(run)
    stores.jobs.save(_job(pipeline_run_id=run.run_id))
    job = stores.jobs.get("job-1")
    assert job is not None
    assert job.pipeline_run_id == run.run_id


def test_pipeline_run_for_client_lists_runs(stores: Stores) -> None:
    stores.runs.save(_run(run_id="run-1"))
    stores.runs.save(_run(run_id="run-2", flow=PipelineFlow.DOWNLOAD, period=None))
    ids = {run.run_id for run in stores.runs.for_client(RFC)}
    assert ids == {"run-1", "run-2"}
    assert stores.runs.for_client(OTHER_RFC) == ()
