"""Ports for resumability and package storage."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from sat_descarga_masiva.contabilidad.journal import JournalEntryRecord
from sat_descarga_masiva.domain.enums.catalog import Direction, ServiceType
from sat_descarga_masiva.domain.model.contributor import ContributorProfileRecord
from sat_descarga_masiva.domain.model.csf import CsfArtifact
from sat_descarga_masiva.domain.model.cursor import DownloadCursor
from sat_descarga_masiva.domain.model.documents import DocumentRecord
from sat_descarga_masiva.domain.model.fiscal_event import FiscalEvent
from sat_descarga_masiva.domain.model.ledger import DownloadJob
from sat_descarga_masiva.domain.model.pipeline_run import PipelineRun
from sat_descarga_masiva.domain.model.results import Package
from sat_descarga_masiva.domain.model.review import (
    ReviewFlag,
    ReviewFlagRecord,
    ReviewSubjectKind,
)
from sat_descarga_masiva.domain.model.source import SourceIdentity
from sat_descarga_masiva.domain.model.token import AccessToken
from sat_descarga_masiva.domain.model.value_objects import PackageId, RequestId, Rfc, Uuid


class TokenStore(Protocol):
    def get(self, key: str) -> AccessToken | None: ...
    def save(self, key: str, token: AccessToken) -> None: ...


class RequestRepository(Protocol):
    def save(self, request_id: RequestId) -> None: ...
    def get(self, request_id: RequestId) -> RequestId | None: ...


class PackageSink(Protocol):
    def store(self, package: Package) -> PackageId: ...


class DownloadJobRepository(Protocol):
    """M1-owned `download_jobs` table: one row per download run (resumable)."""

    def save(self, job: DownloadJob) -> None: ...
    def get(self, job_id: str) -> DownloadJob | None: ...


class DownloadCursorRepository(Protocol):
    """M1-owned `download_cursors` table: resume marker per (rfc, service, direction)."""

    def get(
        self, client_rfc: Rfc, service: ServiceType, direction: Direction
    ) -> DownloadCursor | None: ...
    def save(self, cursor: DownloadCursor) -> None: ...


class SourceIdentityIndex(Protocol):
    """M1 UUID -> extracted-XML SHA-256 dedup index (§6, NOT the manifest sidecar).

    Recording an identity whose uuid already exists with a DIFFERENT sha256
    raises ``SourceHashConflict`` (caller routes to NEEDS_REVIEW); the stored
    identity is never overwritten. Re-recording the same (uuid, sha256) is a
    silent no-op; a new uuid is inserted.
    """

    def record(self, identity: SourceIdentity) -> None: ...
    def get(self, uuid: str) -> SourceIdentity | None: ...


class DocumentRepository(Protocol):
    """M2-owned `documents` table: the current projection per UUID (§4).

    Two truths (§4): this is *not* the history and *not* a status. A re-save of
    the same ``(uuid, source_hash)`` identity updates only the projection fields
    (``last_seen_at``, which never moves backwards, and ``last_run_id``);
    identity fields are write-once. A same-UUID/different-hash save raises
    ``SourceHashConflict`` and leaves the stored row untouched (§6).
    """

    def save(self, record: DocumentRecord) -> None: ...
    def get(self, uuid: Uuid) -> DocumentRecord | None: ...


class FiscalEventStore(Protocol):
    """M2-owned `fiscal_events` table: the single authoritative history (§4).

    Append-only. The idempotency key is
    ``(uuid, contributor_rfc, kind, effective_at, source_hash)``: appending the
    identical fact twice stores one row and never rewrites it, so a later
    observation cannot revise a recorded ``effective_at``. Nothing is ever
    updated or deleted. Ordering is insertion order (``recorded_at`` order), not
    ``effective_at`` order — the fiscal history is what was *observed*, in the
    order it was observed.
    """

    def append(self, event: FiscalEvent) -> None: ...
    def for_uuid(self, uuid: Uuid) -> tuple[FiscalEvent, ...]: ...


class JournalEntryStore(Protocol):
    """M3-owned `journal_entries`/`journal_lines`/`posting_snapshot`: the durable posting.

    The three tables are one fact — the entry, its legs, and the evidence recorded with it
    (§8:159's rule/policy/mapping versions) — so ``append`` writes all three or none and a
    reader never sees half a posting. Append-only, like the ledger it belongs to: the
    identity is §8:194's ``entry_key`` (the entry's ``PostingFingerprint``), so re-appending
    the identical record is an idempotent no-op while a *different* record under the same
    key raises ``ImmutableRecordConflict`` — a posting state is assigned once (§8:166) and
    never rewritten, not even by a correction (that is a new, compensating record).

    The POSTED-resolves-every-leg invariant (§8:159) belongs to ``JournalEntryRecord``
    itself, so no adapter is ever handed a contradictory record and none of them has to
    re-state the rule; ``record`` is therefore accepted by every implementation as-is.

    ``recorded_at`` is the moment the decision became durable, and is stored with it: the
    posting is what was decided *then*, not what today's mapping would decide.
    """

    def append(self, record: JournalEntryRecord) -> None: ...
    def get(self, entry_key: str) -> JournalEntryRecord | None: ...
    def for_source(
        self, contributor_rfc: Rfc, source_uuid: Uuid
    ) -> tuple[JournalEntryRecord, ...]: ...


class ContributorProfileRepository(Protocol):
    """M2-owned `contributor_profiles`: versioned profiles + CSF provenance (§7a).

    Keyed by ``(client_rfc, profile_version)`` and immutable: re-saving an
    existing version with identical content is a no-op, and with any other
    content raises ``ImmutableRecordConflict`` (corrected extraction becomes a
    new ``profile_version`` — §7a's "require human confirmation (versioned
    config, `profile_version`)"). ``latest`` returns the highest version.
    """

    def save(self, record: ContributorProfileRecord) -> None: ...
    def get(self, client_rfc: Rfc, profile_version: int) -> ContributorProfileRecord | None: ...
    def latest(self, client_rfc: Rfc) -> ContributorProfileRecord | None: ...


class CsfArtifactRepository(Protocol):
    """M2-owned `csf_artifacts`: retained CSF originals keyed by SHA-256 (§7a).

    Append-only: the same hash is an idempotent no-op, and a record for the same
    hash with a different ``stored_path`` raises ``ImmutableRecordConflict``
    (the retained original is never repointed).
    """

    def record(self, artifact: CsfArtifact) -> None: ...
    def get(self, sha256: str) -> CsfArtifact | None: ...


class ReviewFlagStore(Protocol):
    """M2-owned `review_flags`: additive open/close facts (§8).

    Opening appends an OPEN fact; closing appends a CLOSED fact referencing the
    row it closes (``closes_flag_id``) and never mutates the opening row. The
    first closure is the fact: re-closing is a no-op that returns the existing
    closure, and closing an unknown id raises ``ReviewFlagNotFound``. Current
    state is reconstructed from the facts, so §11 M3 can ask "does this document
    have unresolved review flags?" from the table alone.
    """

    def open_flag(
        self,
        *,
        subject_kind: ReviewSubjectKind,
        subject_id: str,
        flag: ReviewFlag,
        occurred_at: datetime,
    ) -> ReviewFlagRecord: ...
    def close_flag(self, *, flag_id: int, occurred_at: datetime) -> ReviewFlagRecord: ...
    def open_flags(
        self, subject_kind: ReviewSubjectKind, subject_id: str
    ) -> tuple[ReviewFlagRecord, ...]: ...
    def history(
        self, subject_kind: ReviewSubjectKind, subject_id: str
    ) -> tuple[ReviewFlagRecord, ...]: ...


class PipelineRunRepository(Protocol):
    """M2-owned `pipeline_runs`: one processing-state row per run (§7).

    Unlike the ledger's append-only facts, a run row legitimately moves
    (RUNNING -> COMPLETED/FAILED with ``failed_stage``/``per_stage``), so ``save``
    is an upsert of the run's operational fields. ``DownloadJob.pipeline_run_id``
    links a download job to the run that drove it.
    """

    def save(self, run: PipelineRun) -> None: ...
    def get(self, run_id: str) -> PipelineRun | None: ...
    def for_client(self, client_rfc: Rfc) -> tuple[PipelineRun, ...]: ...
