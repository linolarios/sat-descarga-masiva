"""M2.8: the process stage end to end, on the evidence on disk (§6/§7/§11 M2).

Every collaborator here is the production one: the real extractor writes the XML, the
real reader re-reads those bytes, the real satcfdi parser and verifier consume them, the
real M2.6 projection composes them, and two real SQLite stores persist the result inside
the real unit of work. Only the clock is fixed, so the whole run is reproducible.

Tier-1 doubles (`test_m2_process_documents.py`) prove the *decisions*; this file proves
they hold once the chain is wired for real:

- a TFD-bearing CFDI 4.0 becomes exactly one `documents` row (plus its open review
  facts) whose `source_hash` is the hash of the bytes on disk (§4, §6a.2);
- two fixtures carrying the SAME TFD UUID with different bytes are one identity
  conflict, not two documents: the second is refused and nothing of it is written (§6);
- a CFDI 3.3 and a TFD-less CFDI are quarantined — counted, never persisted (§6);
- re-running the same artifact moves the projection and adds no duplicate fact.

The `fiscal_events` ledger and `pipeline_runs` stay empty throughout: this stage
observes no SAT state transition, so it writes no history (§4).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from fixtures.package_builder import build_package_bytes
from sat_descarga_masiva.application.policies.extraction import ExtractionPolicy
from sat_descarga_masiva.application.use_cases.process_documents import (
    ProcessDocumentsUseCase,
)
from sat_descarga_masiva.domain.model.documents import DocumentRecord
from sat_descarga_masiva.domain.model.perspective import Perspective
from sat_descarga_masiva.domain.model.pipeline_run import StageReport
from sat_descarga_masiva.domain.model.review import (
    ReviewFlagRecord,
    ReviewFlagType,
    ReviewSubjectKind,
)
from sat_descarga_masiva.domain.model.source import ExtractedXml, sha256_hex
from sat_descarga_masiva.domain.model.value_objects import PackageId, Rfc, Uuid
from sat_descarga_masiva.fiscal.projection import project_document
from sat_descarga_masiva.infrastructure.fiscal.satcfdi_fiscal_parser import SatcfdiFiscalParser
from sat_descarga_masiva.infrastructure.fiscal.satcfdi_signature_verifier import (
    SatcfdiSignatureVerifier,
)
from sat_descarga_masiva.infrastructure.persistence.sqlite import (
    SqliteDocumentRepository,
    SqliteReviewFlagStore,
    init_schema,
)
from sat_descarga_masiva.infrastructure.persistence.transactions import SqliteUnitOfWork
from sat_descarga_masiva.infrastructure.sat.extract.extractor import SafeZipExtractor
from sat_descarga_masiva.infrastructure.source.extracted_reader import (
    FilesystemExtractedXmlReader,
)

#: The contributor is the emisor of every committed CFDI fixture.
RFC = Rfc("AAA010101AAA")
REACTOR_RFC = Rfc("BBB010101BBB")
#: The TFD UUID both `cfdi_tfd_4_0.xml` and `cfdi_relacion_4_0.xml` carry.
TFD_UUID = "123E4567-E89B-12D3-A456-426614174000"
PACKAGE_ID = PackageId("4e80345d-917f-40bb-a98f-4a73939353c5_01")
POLICY = ExtractionPolicy(max_total_bytes=1024 * 1024, max_entries=100)
T0 = datetime(2026, 1, 1, tzinfo=UTC)
T1 = datetime(2026, 2, 1, tzinfo=UTC)


class _Clock:
    """A fixed clock: the run is reproducible, so time is an input, not a side effect."""

    def __init__(self, *times: datetime) -> None:
        self._times = times
        self.calls = 0

    def now(self) -> datetime:
        self.calls += 1
        return self._times[min(self.calls - 1, len(self._times) - 1)]


@dataclass(frozen=True)
class Pipeline:
    """The production chain over one in-memory database, plus read-back helpers."""

    use_case: ProcessDocumentsUseCase
    root: Path
    conn: sqlite3.Connection
    clock: _Clock

    def extract(self, fixture: str) -> ExtractedXml:
        """Run the real extractor over a one-member package and return its artifact."""
        content = build_package_bytes((fixture,))
        artifacts = SafeZipExtractor(self.root, POLICY).extract(PACKAGE_ID, content)
        if len(artifacts) != 1:  # pragma: no cover - a one-member package yields one artifact
            raise AssertionError(f"expected one artifact for {fixture}, got {len(artifacts)}")
        return artifacts[0]

    def extracted_bytes(self, artifact: ExtractedXml) -> bytes:
        """The file on disk the artifact record points at — what the reader must re-read."""
        return (self.root / "extracted" / artifact.tipo / f"{artifact.uuid}.xml").read_bytes()

    def process(
        self, artifacts: tuple[ExtractedXml, ...], *, run_id: str | None = None
    ) -> StageReport:
        return self.use_case.process(artifacts, contributor_rfc=RFC, run_id=run_id)

    def record(self, uuid: str) -> DocumentRecord | None:
        return SqliteDocumentRepository(self.conn).get(Uuid(uuid))

    def open_flags(self, uuid: str) -> tuple[ReviewFlagRecord, ...]:
        return SqliteReviewFlagStore(self.conn).open_flags(
            ReviewSubjectKind.DOCUMENT, Uuid(uuid).value
        )

    def count(self, table: str) -> int:
        # `table` is a literal the test itself passes, never any external input.
        cursor = self.conn.execute(f"SELECT COUNT(*) FROM {table}")
        return int(cursor.fetchone()[0])


def _pipeline(tmp_path: Path, clock: _Clock | None = None) -> Pipeline:
    """Wire the production chain; the stores let the unit of work commit the writes."""
    root = tmp_path / "source"
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    fixed_clock = clock if clock is not None else _Clock(T0)
    return Pipeline(
        use_case=ProcessDocumentsUseCase(
            reader=FilesystemExtractedXmlReader(root),
            parser=SatcfdiFiscalParser(),
            verifier=SatcfdiSignatureVerifier(),
            projector=project_document,
            documents=SqliteDocumentRepository(conn, commit=False),
            flags=SqliteReviewFlagStore(conn, commit=False),
            transactions=SqliteUnitOfWork(conn),
            clock=fixed_clock,
        ),
        root=root,
        conn=conn,
        clock=fixed_clock,
    )


def test_a_tfd_bearing_cfdi_is_projected_persisted_and_flagged(tmp_path: Path) -> None:
    pipeline = _pipeline(tmp_path)
    artifact = pipeline.extract("cfdi_tfd_4_0.xml")

    report = pipeline.process((artifact,), run_id="run-1")

    assert report.stage == "fiscal"
    assert dict(report.counts) == {"parsed": 1, "quarantined": 0, "failed": 0, "conflicted": 0}
    assert report.message is None
    record = pipeline.record(TFD_UUID)
    assert record is not None
    assert record.uuid == Uuid(TFD_UUID)
    assert record.contributor_rfc == RFC
    assert record.perspective is Perspective.EMITIDO
    assert (record.tipo, record.version, record.moneda) == ("I", "4.0", "MXN")
    assert (record.emisor_rfc, record.receptor_rfc) == (RFC, REACTOR_RFC)
    assert record.source_hash == sha256_hex(pipeline.extracted_bytes(artifact))
    assert record.source_hash == artifact.sha256
    assert (record.first_seen_at, record.last_seen_at) == (T0, T0)
    assert record.last_run_id == "run-1"

    flags = pipeline.open_flags(TFD_UUID)
    assert [entry.flag.flag_type for entry in flags] == [ReviewFlagType.CFDI_SIGNATURE]
    assert "absent" in flags[0].flag.reason  # the committed fixtures are unsigned
    assert flags[0].occurred_at == T0

    assert pipeline.count("documents") == 1
    assert pipeline.count("fiscal_events") == 0
    assert pipeline.count("pipeline_runs") == 0


def test_artifacts_sharing_one_tfd_uuid_conflict_instead_of_overwriting(tmp_path: Path) -> None:
    pipeline = _pipeline(tmp_path)
    ingreso = pipeline.extract("cfdi_tfd_4_0.xml")
    egreso = pipeline.extract("cfdi_relacion_4_0.xml")
    without_tfd = pipeline.extract("cfdi_eur_4_0.xml")
    assert (egreso.uuid, egreso.tipo) == (ingreso.uuid, "egreso")
    assert egreso.sha256 != ingreso.sha256  # same UUID, different bytes, both on disk

    report = pipeline.process((ingreso, egreso, without_tfd), run_id="run-1")

    assert dict(report.counts) == {"parsed": 1, "quarantined": 1, "failed": 0, "conflicted": 1}
    assert pipeline.count("documents") == 1  # one identity, so one row: never replaced
    record = pipeline.record(TFD_UUID)
    assert record is not None
    assert record.source_hash == ingreso.sha256
    assert record.tipo == "I"
    assert report.message is not None
    assert "different source hash" in report.message
    assert "no TFD UUID" in report.message


def test_a_cfdi_3_3_is_quarantined_and_nothing_is_persisted(tmp_path: Path) -> None:
    pipeline = _pipeline(tmp_path)
    artifact = pipeline.extract("cfdi_ingreso_3_3.xml")

    report = pipeline.process((artifact,), run_id="run-1")

    assert dict(report.counts) == {"parsed": 0, "quarantined": 1, "failed": 0, "conflicted": 0}
    assert report.message is not None
    assert "3.3" in report.message
    assert pipeline.count("documents") == 0
    assert pipeline.count("review_flags") == 0
    assert pipeline.clock.calls == 0  # nothing was timestamped


def test_re_running_the_same_artifact_moves_the_projection_and_adds_no_fact(
    tmp_path: Path,
) -> None:
    pipeline = _pipeline(tmp_path, _Clock(T0, T1))
    artifact = pipeline.extract("cfdi_tfd_4_0.xml")

    first = pipeline.process((artifact,), run_id="run-1")
    second = pipeline.process((artifact,), run_id="run-2")

    assert dict(first.counts)["parsed"] == 1
    assert dict(second.counts)["parsed"] == 1
    assert pipeline.count("documents") == 1
    record = pipeline.record(TFD_UUID)
    assert record is not None
    assert record.first_seen_at == T0  # identity fields are write-once
    assert record.last_seen_at == T1  # the projection moved with the re-run
    assert record.last_run_id == "run-2"
    assert pipeline.count("review_flags") == 1  # the open fact was not duplicated
    assert pipeline.clock.calls == 2  # one timestamp per persisted document
