"""In-memory adapters: TokenStore, RequestRepository, the M1 ledger repos, the M2
stores (`documents`, `fiscal_events`, `contributor_profiles`, `csf_artifacts`,
`review_flags`, `pipeline_runs`) and the M3 journal store (`journal_entries`).

They implement the same ports as the SQLite adapters and are held to the same
behavior by the shared contract suite (§12). Timestamps are normalized to
tz-aware UTC on write, exactly like the SQLite adapter, so a value read back
here cannot differ from the value read back from disk.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from sat_descarga_masiva.contabilidad.journal import JournalEntryRecord
from sat_descarga_masiva.domain.enums.catalog import Direction, ServiceType
from sat_descarga_masiva.domain.errors import (
    ImmutableRecordConflict,
    ReviewFlagNotFound,
    SourceHashConflict,
)
from sat_descarga_masiva.domain.model.contributor import ContributorProfileRecord
from sat_descarga_masiva.domain.model.csf import CsfArtifact
from sat_descarga_masiva.domain.model.cursor import DownloadCursor
from sat_descarga_masiva.domain.model.documents import DocumentRecord
from sat_descarga_masiva.domain.model.fiscal_event import FiscalEvent
from sat_descarga_masiva.domain.model.ledger import DownloadJob
from sat_descarga_masiva.domain.model.pipeline_run import PipelineRun
from sat_descarga_masiva.domain.model.review import (
    ReviewFlag,
    ReviewFlagRecord,
    ReviewSubjectKind,
)
from sat_descarga_masiva.domain.model.source import SourceIdentity
from sat_descarga_masiva.domain.model.token import AccessToken
from sat_descarga_masiva.domain.model.value_objects import RequestId, Rfc, Uuid


def _utc(value: datetime) -> datetime:
    """Normalize to tz-aware UTC (naive treated as UTC) — as the SQLite adapter does."""
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _event_key(event: FiscalEvent) -> tuple[str, str, str, datetime, str]:
    """The idempotency key of one fiscal fact (§4 append-only history)."""
    return (
        event.uuid.value,
        event.contributor_rfc.value,
        event.kind,
        event.effective_at,
        event.source_hash,
    )


class InMemoryTokenStore:
    """TokenStore port backed by an in-memory dict (nothing persists)."""

    def __init__(self) -> None:
        self._tokens: dict[str, AccessToken] = {}

    def get(self, key: str) -> AccessToken | None:
        return self._tokens.get(key)

    def save(self, key: str, token: AccessToken) -> None:
        self._tokens[key] = token


class InMemoryRequestRepository:
    """RequestRepository port: tracks request ids in memory for resume."""

    def __init__(self) -> None:
        self._request_ids: set[RequestId] = set()

    def save(self, request_id: RequestId) -> None:
        self._request_ids.add(request_id)

    def get(self, request_id: RequestId) -> RequestId | None:
        return request_id if request_id in self._request_ids else None


class InMemoryDownloadJobRepository:
    """DownloadJobRepository backed by a dict keyed by job_id."""

    def __init__(self) -> None:
        self._jobs: dict[str, DownloadJob] = {}

    def save(self, job: DownloadJob) -> None:
        self._jobs[job.job_id] = job

    def get(self, job_id: str) -> DownloadJob | None:
        return self._jobs.get(job_id)


class InMemoryDownloadCursorRepository:
    """DownloadCursorRepository backed by a dict keyed by (rfc, service, direction)."""

    def __init__(self) -> None:
        self._cursors: dict[tuple[str, str, str], DownloadCursor] = {}

    def get(
        self, client_rfc: Rfc, service: ServiceType, direction: Direction
    ) -> DownloadCursor | None:
        return self._cursors.get((client_rfc.value, service.value, direction.value))

    def save(self, cursor: DownloadCursor) -> None:
        self._cursors[(cursor.client_rfc.value, cursor.service.value, cursor.direction.value)] = (
            cursor
        )


class InMemorySourceIdentityIndex:
    """SourceIdentityIndex backed by a dict keyed by uuid."""

    def __init__(self) -> None:
        self._records: dict[str, SourceIdentity] = {}

    def record(self, identity: SourceIdentity) -> None:
        existing = self._records.get(identity.uuid)
        if existing is not None:
            if existing.sha256 == identity.sha256:
                return  # duplicate artifact: idempotent no-op
            raise SourceHashConflict(
                f"source uuid {identity.uuid} already recorded with a different SHA-256"
            )
        self._records[identity.uuid] = identity

    def get(self, uuid: str) -> SourceIdentity | None:
        return self._records.get(uuid)


class InMemoryDocumentRepository:
    """DocumentRepository over a dict keyed by uuid: projection fields only."""

    def __init__(self) -> None:
        self._records: dict[str, DocumentRecord] = {}

    def save(self, record: DocumentRecord) -> None:
        incoming = replace(
            record,
            first_seen_at=_utc(record.first_seen_at),
            last_seen_at=_utc(record.last_seen_at),
        )
        existing = self._records.get(incoming.uuid.value)
        if existing is None:
            self._records[incoming.uuid.value] = incoming
            return
        if existing.source_hash != incoming.source_hash:
            raise SourceHashConflict(
                f"document {incoming.uuid.value} already recorded with a different source hash"
            )
        self._records[incoming.uuid.value] = replace(
            existing,
            last_seen_at=max(existing.last_seen_at, incoming.last_seen_at),
            last_run_id=incoming.last_run_id,
        )

    def get(self, uuid: Uuid) -> DocumentRecord | None:
        return self._records.get(uuid.value)


class InMemoryFiscalEventStore:
    """FiscalEventStore over an append-only list keyed by the fact's identity."""

    def __init__(self) -> None:
        self._events: list[FiscalEvent] = []
        self._keys: set[tuple[str, str, str, datetime, str]] = set()

    def append(self, event: FiscalEvent) -> None:
        normalized = replace(
            event, effective_at=_utc(event.effective_at), recorded_at=_utc(event.recorded_at)
        )
        key = _event_key(normalized)
        if key in self._keys:
            return  # identical fact: append-only, so nothing is rewritten
        self._keys.add(key)
        self._events.append(normalized)

    def for_uuid(self, uuid: Uuid) -> tuple[FiscalEvent, ...]:
        return tuple(event for event in self._events if event.uuid.value == uuid.value)


class InMemoryContributorProfileRepository:
    """ContributorProfileRepository over a dict keyed by (rfc, version)."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, int], ContributorProfileRecord] = {}

    def save(self, record: ContributorProfileRecord) -> None:
        incoming = replace(
            record,
            csf_obtained_at=_utc(record.csf_obtained_at),
            recorded_at=_utc(record.recorded_at),
        )
        key = (incoming.profile.rfc.value, incoming.profile_version)
        existing = self._records.get(key)
        if existing is not None:
            if existing != incoming:
                raise ImmutableRecordConflict(
                    f"profile {key[0]} version {key[1]} already recorded with different content"
                )
            return  # identical re-save: idempotent no-op
        self._records[key] = incoming

    def get(self, client_rfc: Rfc, profile_version: int) -> ContributorProfileRecord | None:
        return self._records.get((client_rfc.value, profile_version))

    def latest(self, client_rfc: Rfc) -> ContributorProfileRecord | None:
        versions = [version for (rfc, version) in self._records if rfc == client_rfc.value]
        if not versions:
            return None
        return self._records[(client_rfc.value, max(versions))]


class InMemoryCsfArtifactRepository:
    """CsfArtifactRepository over a dict keyed by the CSF's SHA-256."""

    def __init__(self) -> None:
        self._artifacts: dict[str, CsfArtifact] = {}

    def record(self, artifact: CsfArtifact) -> None:
        incoming = replace(artifact, recorded_at=_utc(artifact.recorded_at))
        existing = self._artifacts.get(incoming.sha256)
        if existing is not None:
            if existing != incoming:
                raise ImmutableRecordConflict(
                    f"CSF artifact {incoming.sha256} already recorded with different content"
                )
            return  # identical re-record: idempotent no-op
        self._artifacts[incoming.sha256] = incoming

    def get(self, sha256: str) -> CsfArtifact | None:
        return self._artifacts.get(sha256)


class InMemoryReviewFlagStore:
    """ReviewFlagStore over an append-only list of open/close facts (§8)."""

    def __init__(self) -> None:
        self._rows: list[ReviewFlagRecord] = []
        self._next_id = 1

    def open_flag(
        self,
        *,
        subject_kind: ReviewSubjectKind,
        subject_id: str,
        flag: ReviewFlag,
        occurred_at: datetime,
    ) -> ReviewFlagRecord:
        record = ReviewFlagRecord(
            flag_id=self._next_id,
            subject_kind=subject_kind,
            subject_id=subject_id,
            flag=ReviewFlag(flag.flag_type, flag.reason),  # an opening fact is always OPEN
            occurred_at=_utc(occurred_at),
        )
        self._next_id += 1
        self._rows.append(record)
        return record

    def close_flag(self, *, flag_id: int, occurred_at: datetime) -> ReviewFlagRecord:
        opening = self._opening_row(flag_id)
        existing = self._closure_of(flag_id)
        if existing is not None:
            return existing  # the first closure is the fact: re-closing is a no-op
        closure = ReviewFlagRecord(
            flag_id=self._next_id,
            subject_kind=opening.subject_kind,
            subject_id=opening.subject_id,
            flag=opening.flag.closed(),
            occurred_at=_utc(occurred_at),
            closes_flag_id=flag_id,
        )
        self._next_id += 1
        self._rows.append(closure)
        return closure

    def open_flags(
        self, subject_kind: ReviewSubjectKind, subject_id: str
    ) -> tuple[ReviewFlagRecord, ...]:
        closed = {row.closes_flag_id for row in self._rows}
        return tuple(
            row
            for row in self._rows_for(subject_kind, subject_id)
            if row.closes_flag_id is None and row.flag_id not in closed
        )

    def history(
        self, subject_kind: ReviewSubjectKind, subject_id: str
    ) -> tuple[ReviewFlagRecord, ...]:
        return self._rows_for(subject_kind, subject_id)

    def _rows_for(
        self, subject_kind: ReviewSubjectKind, subject_id: str
    ) -> tuple[ReviewFlagRecord, ...]:
        return tuple(
            row
            for row in self._rows
            if row.subject_kind is subject_kind and row.subject_id == subject_id
        )

    def _opening_row(self, flag_id: int) -> ReviewFlagRecord:
        for row in self._rows:
            if row.flag_id == flag_id and row.closes_flag_id is None:
                return row
        raise ReviewFlagNotFound(f"no open review flag with flag_id {flag_id}")

    def _closure_of(self, flag_id: int) -> ReviewFlagRecord | None:
        for row in self._rows:
            if row.closes_flag_id == flag_id:
                return row
        return None


class InMemoryPipelineRunRepository:
    """PipelineRunRepository over a dict keyed by run_id (operational state)."""

    def __init__(self) -> None:
        self._runs: dict[str, PipelineRun] = {}

    def save(self, run: PipelineRun) -> None:
        self._runs[run.run_id] = replace(
            run,
            started_at=_utc(run.started_at),
            finished_at=_utc(run.finished_at) if run.finished_at is not None else None,
        )

    def get(self, run_id: str) -> PipelineRun | None:
        return self._runs.get(run_id)

    def for_client(self, client_rfc: Rfc) -> tuple[PipelineRun, ...]:
        return tuple(run for run in self._runs.values() if run.client_rfc.value == client_rfc.value)


class InMemoryJournalEntryStore:
    """JournalEntryStore over an append-only dict keyed by §8:194's ``entry_key``.

    One record *is* the whole posting (entry, legs and evidence together), so the
    in-memory adapter needs no partial write: a record is either stored or not, which is
    the same guarantee ``SqliteJournalEntryStore`` buys with its transaction.
    ``for_source`` sorts by ``(recorded_at, entry_key)`` — the total order the SQLite
    adapter reads in — so the two adapters cannot disagree about the order they report.
    """

    def __init__(self) -> None:
        self._records: dict[str, JournalEntryRecord] = {}

    def append(self, record: JournalEntryRecord) -> None:
        incoming = replace(record, recorded_at=_utc(record.recorded_at))
        existing = self._records.get(incoming.entry_key)
        if existing is not None:
            if existing != incoming:
                raise ImmutableRecordConflict(
                    f"journal entry {incoming.entry_key} is already recorded with different"
                    " content: a posting state is assigned once (§8:166)"
                )
            return  # identical re-append: idempotent no-op
        self._records[incoming.entry_key] = incoming

    def get(self, entry_key: str) -> JournalEntryRecord | None:
        return self._records.get(entry_key)

    def for_source(self, contributor_rfc: Rfc, source_uuid: Uuid) -> tuple[JournalEntryRecord, ...]:
        matching = (
            record
            for record in self._records.values()
            if record.contributor_rfc.value == contributor_rfc.value
            and record.source_uuid.value == source_uuid.value
        )
        return tuple(sorted(matching, key=lambda record: (record.recorded_at, record.entry_key)))
