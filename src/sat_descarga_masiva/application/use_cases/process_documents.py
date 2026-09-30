"""ProcessDocumentsUseCase — the per-document process stage (M2.8, §7/§11 M2).

One batch of extracted artifacts, one document at a time:

    read -> parse -> verify -> project -> persist (one transaction per document)

Decisions, each traceable:

- **Byte identity.** The reader returns the artifact's bytes once, and that same
  object is handed to the parser and to the verifier, so the fiscal facts and the
  authenticity verdict describe the same artifact and nothing is re-serialized
  between them (§6a.3).
- **The TFD UUID is the identity** (§6): it is what the projection decides on, what
  the `documents` row is keyed by and what review facts are attached to. The
  artifact's own name is a claim to cross-check, never the identity.
- **Per-document outcomes, none of them a status.** A document whose bytes are not
  a readable CFDI is counted `failed`; one the projection quarantines is counted
  `quarantined`; one whose UUID is already recorded with different bytes is counted
  `conflicted` (``SourceHashConflict``, §6). None of the three stops the batch, and
  none writes a status anywhere: §4 keeps those truths in their own tables.
- **Store integrity is not a document outcome.** An ``ExtractionError`` from the
  reader (missing artifact, hash mismatch, unsafe name) propagates and fails the
  stage: it says *our* copy is untrustworthy, not that this CFDI is bad, and
  absorbing it would hide exactly the condition §6a.2 exists to surface.
- **Only the established parser fault is absorbed.** ``XmlParseError`` means "these
  bytes are not a CFDI we can read"; any other exception from a collaborator is a
  defect and must surface.
- **Idempotent re-runs.** The same artifact re-projects to the same
  ``(uuid, source_hash)`` identity, and open review facts are appended only for
  flags that are not already open, so a second run adds no duplicate fact.
- **The fiscal history is untouched.** The single authoritative `fiscal_events`
  ledger records observed SAT-state transitions and this stage observes none, so
  `documents` and `review_flags` are the only tables involved (§4, D5).
"""

from __future__ import annotations

from datetime import datetime

from sat_descarga_masiva.application.ports.fiscal_parser import FiscalXmlParser
from sat_descarga_masiva.application.ports.persistence import (
    DocumentRepository,
    ReviewFlagStore,
)
from sat_descarga_masiva.application.ports.projection import DocumentProjector
from sat_descarga_masiva.application.ports.services import Clock
from sat_descarga_masiva.application.ports.signature import CfdiSignatureVerifier
from sat_descarga_masiva.application.ports.source import ExtractedXmlReader
from sat_descarga_masiva.application.ports.transactions import UnitOfWork
from sat_descarga_masiva.domain.errors import SourceHashConflict, XmlParseError
from sat_descarga_masiva.domain.model.documents import DocumentRecord
from sat_descarga_masiva.domain.model.pipeline_run import StageReport
from sat_descarga_masiva.domain.model.review import ReviewFlags, ReviewSubjectKind
from sat_descarga_masiva.domain.model.source import ExtractedXml
from sat_descarga_masiva.domain.model.value_objects import Rfc, Uuid
from sat_descarga_masiva.fiscal.projection import ProcessedDocument

STAGE = "fiscal"


class ProcessDocumentsUseCase:
    """Turn extracted artifacts into the current fiscal projection, one transaction each."""

    def __init__(  # noqa: PLR0913 (each collaborator is a distinct capability, none optional)
        self,
        *,
        reader: ExtractedXmlReader,
        parser: FiscalXmlParser,
        verifier: CfdiSignatureVerifier,
        projector: DocumentProjector,
        documents: DocumentRepository,
        flags: ReviewFlagStore,
        transactions: UnitOfWork,
        clock: Clock,
    ) -> None:
        self._reader = reader
        self._parser = parser
        self._verifier = verifier
        self._projector = projector
        self._documents = documents
        self._flags = flags
        self._transactions = transactions
        self._clock = clock

    def process(
        self,
        artifacts: tuple[ExtractedXml, ...],
        *,
        contributor_rfc: Rfc,
        run_id: str | None = None,
    ) -> StageReport:
        """Process a whole batch; the report counts what each document became."""
        parsed = 0
        quarantined = 0
        failed = 0
        conflicted = 0
        notes: list[str] = []
        for artifact in artifacts:
            data = self._reader.read(artifact)
            try:
                raw = self._parser.parse(data, artifact.sha256)
            except XmlParseError as exc:
                failed += 1
                notes.append(f"{artifact.uuid}: parser fault: {exc}")
                continue
            result = self._projector(
                raw,
                self._verifier.verify(data),
                source_hash=artifact.sha256,
                contributor_rfc=contributor_rfc,
                artifact_uuid=artifact.uuid,
            )
            if result.is_quarantined:
                quarantined += 1
                notes.append(f"{artifact.uuid}: {result.quarantine_reason}")
                continue
            uuid = result.uuid
            if uuid is None:  # pragma: no cover - only a quarantined result has no UUID
                raise RuntimeError(f"projected document {artifact.uuid} carries no TFD UUID")
            now = self._clock.now()
            try:
                with self._transactions.transaction():
                    self._documents.save(
                        _document_record(
                            result,
                            uuid,
                            contributor_rfc=contributor_rfc,
                            source_hash=artifact.sha256,
                            now=now,
                            run_id=run_id,
                        )
                    )
                    self._record_open_flags(result.review_flags, uuid, now)
            except SourceHashConflict as exc:
                # The rollback already happened on the way out of the `with`: this
                # document is the only thing that did not happen, batch continues.
                conflicted += 1
                notes.append(f"{uuid.value}: {exc}")
                continue
            parsed += 1
        return StageReport(
            stage=STAGE,
            message="; ".join(notes) if notes else None,
            counts=(
                ("parsed", parsed),
                ("quarantined", quarantined),
                ("failed", failed),
                ("conflicted", conflicted),
            ),
        )

    def _record_open_flags(self, review_flags: ReviewFlags, uuid: Uuid, now: datetime) -> None:
        """Append one fact per OPEN flag that is not already open for this document."""
        already_open = {
            record.flag.flag_type
            for record in self._flags.open_flags(ReviewSubjectKind.DOCUMENT, uuid.value)
        }
        for flag in review_flags.open_flags():
            if flag.flag_type in already_open:
                continue  # the open fact is already recorded: never duplicate it
            self._flags.open_flag(
                subject_kind=ReviewSubjectKind.DOCUMENT,
                subject_id=uuid.value,
                flag=flag,
                occurred_at=now,
            )


def _document_record(  # noqa: PLR0913 (these are §4's columns, not free choices)
    result: ProcessedDocument,
    uuid: Uuid,
    *,
    contributor_rfc: Rfc,
    source_hash: str,
    now: datetime,
    run_id: str | None,
) -> DocumentRecord:
    """The §4 projection row for one projectable document.

    Identity is ``(uuid, source_hash)``; the timestamps are *projection* fields, so
    this states "seen now" and lets the store keep ``first_seen_at`` write-once while
    ``last_seen_at``/``last_run_id`` move on a re-run.
    """
    document = result.document
    if document is None:  # pragma: no cover - quarantined results never reach persistence
        raise RuntimeError(f"document {uuid.value} has no fiscal projection to record")
    return DocumentRecord(
        uuid=uuid,
        contributor_rfc=contributor_rfc,
        perspective=result.perspective,
        tipo=document.tipo,
        version=document.version,
        source_hash=source_hash,
        emisor_rfc=document.emisor_rfc,
        receptor_rfc=document.receptor_rfc,
        moneda=document.moneda,
        first_seen_at=now,
        last_seen_at=now,
        last_run_id=run_id,
    )
