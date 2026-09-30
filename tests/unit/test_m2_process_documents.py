"""M2.8: ProcessDocumentsUseCase — the per-document process stage, with doubles.

The use case is orchestration, so every collaborator here is a recording double.
What M2.8 owns is the *sequence* and the *decisions* — read the artifact, parse
it, verify the same bytes, project, persist one document inside one transaction —
never the fiscal logic (M2.3/M2.5/M2.6) and never the storage semantics (M2.7 and
the SQLite UoW, proved in test_m2_sqlite_transactions.py).

Two notes about the doubles:

- Values are real domain objects (`ExtractedXml`, `FiscalDocument`,
  `ProcessedDocument`, `RawCfd`); only collaborators with I/O or library
  dependencies are faked, so a change in any domain shape shows up here.
- `FakeUnitOfWork` records begin/commit/rollback per document. That proves the
  *scope* (one transaction per document, rolled back when its own save conflicts,
  never spanning the batch); the *effect* of a rollback on stored rows belongs to
  the SQLite UoW and is proved there.
"""

from __future__ import annotations

import ast
import inspect
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sat_descarga_masiva.application.use_cases import process_documents as process_documents_module
from sat_descarga_masiva.application.use_cases.process_documents import (
    ProcessDocumentsUseCase,
)
from sat_descarga_masiva.domain.errors import (
    ExtractionError,
    SourceHashConflict,
    XmlParseError,
)
from sat_descarga_masiva.domain.model.documents import DocumentRecord
from sat_descarga_masiva.domain.model.fiscal_document import (
    FiscalDocument,
    Impuestos,
    ParseOutcome,
)
from sat_descarga_masiva.domain.model.perspective import Perspective
from sat_descarga_masiva.domain.model.pipeline_run import StageReport
from sat_descarga_masiva.domain.model.raw_cfd import RawCfd, RawImpuestos
from sat_descarga_masiva.domain.model.review import (
    ReviewFlag,
    ReviewFlagRecord,
    ReviewFlags,
    ReviewFlagState,
    ReviewFlagType,
    ReviewSubjectKind,
)
from sat_descarga_masiva.domain.model.signature import SignatureOutcome, SignatureVerdict
from sat_descarga_masiva.domain.model.source import ExtractedXml, sha256_hex
from sat_descarga_masiva.domain.model.value_objects import Rfc, Uuid
from sat_descarga_masiva.fiscal.projection import ProcessedDocument

RFC = Rfc("AAA010101AAA")
ISSUER = Rfc("BBB010101BBB")
# Uppercase: `Uuid` normalizes case, and the identity M2.8 records is that normalized form.
UUID1 = "123E4567-E89B-12D3-A456-426614174000"
UUID2 = "4E80345D-917F-40BB-A98F-4A73939353C5"
UUID3 = "5E80345D-917F-40BB-A98F-4A73939353C5"
XML = b"<cfdi:Comprobante TipoDeComprobante='I'/>"
XML2 = b"<cfdi:Comprobante TipoDeComprobante='E'/>"
XML3 = b"<cfdi:Comprobante TipoDeComprobante='T'/>"
UNREADABLE = b"<cfdi:Comprobante TipoDeComprobante='I'"  # truncated: distinct bytes, bad XML
HASH = sha256_hex(XML)
T0 = datetime(2026, 1, 1, tzinfo=UTC)
T1 = datetime(2026, 1, 2, tzinfo=UTC)
STAGE = "fiscal"


def _artifact(uuid: str = UUID1, *, tipo: str = "ingreso", data: bytes = XML) -> ExtractedXml:
    return ExtractedXml(uuid=uuid, tipo=tipo, sha256=sha256_hex(data))


def _document(
    uuid: str = UUID1, *, moneda: str = "MXN", flags: ReviewFlags | None = None
) -> FiscalDocument:
    return FiscalDocument(
        tipo="I",
        version="4.0",
        moneda=moneda,
        tipo_cambio=None,
        emisor_rfc=ISSUER,
        receptor_rfc=RFC,
        conceptos=(),
        impuestos=Impuestos(),
        total=None,
        source_hash=HASH,
        source_uuid=Uuid(uuid),
        review_flags=flags if flags is not None else ReviewFlags(),
    )


def _projected(
    uuid: str = UUID1,
    *,
    flags: ReviewFlags | None = None,
    outcome: ParseOutcome = ParseOutcome.PARSED,
    perspective: Perspective = Perspective.RECIBIDO,
    quarantine_reason: str | None = None,
    moneda: str = "MXN",
) -> ProcessedDocument:
    """A real ProcessedDocument: what M2.6's project_document would have handed over."""
    review_flags = flags if flags is not None else ReviewFlags()
    document = (
        None
        if outcome is ParseOutcome.FAILED
        else _document(uuid, moneda=moneda, flags=review_flags)
    )
    return ProcessedDocument(
        outcome=outcome,
        document=document,
        perspective=perspective,
        review_flags=review_flags,
        quarantine_reason=quarantine_reason,
    )


def _raw(uuid: str | None = UUID1) -> RawCfd:
    """A real RawCfd: the parser's output type, built without touching satcfdi."""
    return RawCfd(
        tipo="I",
        version="4.0",
        moneda="MXN",
        tipo_cambio=None,
        emisor_rfc=ISSUER.value,
        receptor_rfc=RFC.value,
        conceptos=(),
        impuestos=RawImpuestos(),
        total="0.00",
        uuid=uuid,
    )


class FakeReader:
    """Hands back the bytes recorded for that artifact — or fails, like the real reader."""

    def __init__(
        self, files: dict[str, bytes] | None = None, error: ExtractionError | None = None
    ) -> None:
        self._files = dict(files) if files is not None else {UUID1: XML}
        self._error = error
        self.calls: list[ExtractedXml] = []

    def read(self, artifact: ExtractedXml) -> bytes:
        self.calls.append(artifact)
        if self._error is not None:
            raise self._error
        data = self._files.get(artifact.uuid)
        if data is None:
            raise ExtractionError(f"extracted artifact {artifact.uuid} is missing")
        return data


class FakeParser:
    """Records the exact bytes object it was handed; faults are keyed by source hash."""

    def __init__(self, faults: dict[str, Exception] | None = None) -> None:
        self._faults = dict(faults) if faults is not None else {}
        self.calls: list[tuple[bytes, str]] = []

    def parse(self, xml_bytes: bytes, source_hash: str) -> RawCfd:
        self.calls.append((xml_bytes, source_hash))
        fault = self._faults.get(source_hash)
        if fault is not None:
            raise fault
        return _raw()


class FakeVerifier:
    """Records the exact bytes object it was handed, so byte-identity is provable."""

    def __init__(self, outcome: SignatureOutcome = SignatureOutcome.VALID) -> None:
        self._verdict = SignatureVerdict(outcome)
        self.calls: list[bytes] = []

    def verify(self, xml_bytes: bytes) -> SignatureVerdict:
        self.calls.append(xml_bytes)
        return self._verdict


class FakeProjector:
    """A stand-in for the pure M2.6 projection, returning real ProcessedDocuments."""

    def __init__(self, results: dict[str, ProcessedDocument] | None = None) -> None:
        self._results = dict(results) if results is not None else {}
        self.calls: list[tuple[RawCfd, SignatureVerdict, str, Rfc, str]] = []

    def __call__(
        self,
        raw: RawCfd,
        verdict: SignatureVerdict,
        *,
        source_hash: str,
        contributor_rfc: Rfc,
        artifact_uuid: str,
        money: object = None,
    ) -> ProcessedDocument:
        self.calls.append((raw, verdict, source_hash, contributor_rfc, artifact_uuid))
        return self._results.get(artifact_uuid) or _projected(artifact_uuid)


class FakeDocuments:
    """Records every save; a uuid in `conflicts` behaves like a stored other hash (§6)."""

    def __init__(
        self,
        conflicts: frozenset[str] = frozenset(),
        gate: FakeUnitOfWork | None = None,
    ) -> None:
        self._conflicts = conflicts
        self._gate = gate
        self.saved: list[DocumentRecord] = []

    def save(self, record: DocumentRecord) -> None:
        if self._gate is not None and not self._gate.is_open:
            raise AssertionError("documents.save was called outside a transaction")
        if record.uuid.value in self._conflicts:
            raise SourceHashConflict(f"document {record.uuid.value} already recorded")
        self.saved.append(record)

    def get(self, uuid: Uuid) -> DocumentRecord | None:
        raise NotImplementedError("M2.8 never reads the projection back")


class FakeFlags:
    """Opens flags as facts and answers `open_flags` from what it already holds."""

    def __init__(
        self,
        already_open: tuple[ReviewFlagRecord, ...] = (),
        gate: FakeUnitOfWork | None = None,
    ) -> None:
        self._gate = gate
        self._already_open = list(already_open)
        self.opened: list[ReviewFlagRecord] = []
        self.queries: list[tuple[ReviewSubjectKind, str]] = []

    def open_flag(
        self,
        *,
        subject_kind: ReviewSubjectKind,
        subject_id: str,
        flag: ReviewFlag,
        occurred_at: datetime,
    ) -> ReviewFlagRecord:
        if self._gate is not None and not self._gate.is_open:
            raise AssertionError("open_flag was called outside a transaction")
        record = ReviewFlagRecord(
            flag_id=len(self._already_open) + len(self.opened) + 1,
            subject_kind=subject_kind,
            subject_id=subject_id,
            flag=flag,
            occurred_at=occurred_at,
        )
        self.opened.append(record)
        return record

    def open_flags(
        self, subject_kind: ReviewSubjectKind, subject_id: str
    ) -> tuple[ReviewFlagRecord, ...]:
        self.queries.append((subject_kind, subject_id))
        known = (*self._already_open, *self.opened)
        return tuple(
            record
            for record in known
            if record.subject_kind is subject_kind and record.subject_id == subject_id
        )

    def close_flag(self, *, flag_id: int, occurred_at: datetime) -> ReviewFlagRecord:
        raise NotImplementedError("M2.8 never closes a flag")

    def history(
        self, subject_kind: ReviewSubjectKind, subject_id: str
    ) -> tuple[ReviewFlagRecord, ...]:
        raise NotImplementedError("M2.8 asks for open state, not history")


class FakeUnitOfWork:
    """Counts transaction boundaries: one per document, rolled back on its own failure."""

    def __init__(self) -> None:
        self.entries = 0
        self.commits = 0
        self.rollbacks = 0

    @property
    def is_open(self) -> bool:
        """True between a transaction's entry and its exit, whatever the outcome."""
        return self.entries > self.commits + self.rollbacks

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self.entries += 1
        try:
            yield
        except BaseException:
            self.rollbacks += 1
            raise
        self.commits += 1


class FakeClock:
    def __init__(self, now: datetime = T0) -> None:
        self._now = now
        self.calls = 0

    def now(self) -> datetime:
        self.calls += 1
        return self._now


@dataclass(frozen=True)
class Harness:
    """The use case plus every double it was built from, for assertion."""

    use_case: ProcessDocumentsUseCase
    reader: FakeReader
    parser: FakeParser
    verifier: FakeVerifier
    projector: FakeProjector
    documents: FakeDocuments
    flags: FakeFlags
    uow: FakeUnitOfWork
    clock: FakeClock


def _harness(
    *,
    files: dict[str, bytes] | None = None,
    results: dict[str, ProcessedDocument] | None = None,
    parser_faults: dict[str, Exception] | None = None,
    reader_error: ExtractionError | None = None,
    conflicts: frozenset[str] = frozenset(),
    already_open: tuple[ReviewFlagRecord, ...] = (),
    verifier_outcome: SignatureOutcome = SignatureOutcome.VALID,
    gate: bool = False,
) -> Harness:
    uow = FakeUnitOfWork()
    reader = FakeReader(files, reader_error)
    parser = FakeParser(parser_faults)
    verifier = FakeVerifier(verifier_outcome)
    projector = FakeProjector(results)
    documents = FakeDocuments(conflicts, uow if gate else None)
    flags = FakeFlags(already_open, uow if gate else None)
    clock = FakeClock()
    use_case = ProcessDocumentsUseCase(
        reader=reader,
        parser=parser,
        verifier=verifier,
        projector=projector,
        documents=documents,
        flags=flags,
        transactions=uow,
        clock=clock,
    )
    return Harness(
        use_case=use_case,
        reader=reader,
        parser=parser,
        verifier=verifier,
        projector=projector,
        documents=documents,
        flags=flags,
        uow=uow,
        clock=clock,
    )


def _process(
    harness: Harness, artifacts: tuple[ExtractedXml, ...], *, run_id: str | None = None
) -> StageReport:
    return harness.use_case.process(artifacts, contributor_rfc=RFC, run_id=run_id)


def _counts(report: StageReport) -> dict[str, int]:
    return dict(report.counts)


def test_verifier_and_parser_receive_the_very_same_original_bytes() -> None:
    """Byte identity, not equality: nothing is re-serialized between the two (§6a.3)."""
    data = bytes(XML)  # a distinct object from every other use of XML
    harness = _harness(files={UUID1: data})
    _process(harness, (_artifact(data=data),))
    assert harness.parser.calls[0][0] is data
    assert harness.verifier.calls[0] is data


def test_parser_receives_the_artifact_source_hash() -> None:
    artifact = _artifact()
    harness = _harness()
    _process(harness, (artifact,))
    assert harness.parser.calls[0][1] == artifact.sha256


def test_artifacts_are_processed_in_the_order_given() -> None:
    artifacts = (_artifact(UUID1, data=XML), _artifact(UUID2, data=XML2))
    harness = _harness(files={UUID1: XML, UUID2: XML2})
    _process(harness, artifacts)
    assert harness.reader.calls == list(artifacts)
    assert [record.uuid.value for record in harness.documents.saved] == [UUID1, UUID2]


def test_report_names_the_fiscal_stage_with_the_ordered_counters() -> None:
    harness = _harness()
    report = _process(harness, (_artifact(),))
    assert report.stage == STAGE
    assert report.counts == (
        ("parsed", 1),
        ("quarantined", 0),
        ("failed", 0),
        ("conflicted", 0),
    )
    assert report.message is None  # a clean batch has nothing to report


def test_document_and_flag_identity_is_the_tfd_uuid() -> None:
    flags = ReviewFlags().with_flag(ReviewFlag(ReviewFlagType.CFDI_SIGNATURE, "absent"))
    harness = _harness(files={UUID2: XML}, results={UUID2: _projected(UUID2, flags=flags)})
    _process(harness, (_artifact(UUID2),))
    assert harness.documents.saved[0].uuid == Uuid(UUID2)
    assert harness.flags.opened[0].subject_id == UUID2
    assert harness.flags.queries == [(ReviewSubjectKind.DOCUMENT, UUID2)]


def test_a_tfd_uuid_that_contradicts_the_artifact_name_is_quarantined() -> None:
    """The projection's identity rule (§6) is enforced by M2.6, honoured here."""
    reason = f"TFD UUID {UUID2} differs from the artifact UUID {UUID1}"
    harness = _harness(results={UUID1: _projected(UUID2, quarantine_reason=reason)})
    report = _process(harness, (_artifact(UUID1),))
    assert _counts(report)["quarantined"] == 1
    assert harness.documents.saved == []
    assert harness.flags.opened == []
    assert harness.uow.entries == 0


def test_a_quarantined_document_is_counted_and_nothing_is_persisted() -> None:
    reason = "no TFD UUID: no trustworthy document identity"
    harness = _harness(results={UUID1: _projected(UUID1, quarantine_reason=reason)})
    report = _process(harness, (_artifact(),))
    assert _counts(report) == {"parsed": 0, "quarantined": 1, "failed": 0, "conflicted": 0}
    assert harness.documents.saved == []
    assert harness.flags.opened == []
    assert harness.uow.entries == 0
    assert harness.clock.calls == 0  # nothing to timestamp
    assert report.message is not None
    assert reason in report.message


def test_a_parser_fault_is_counted_and_the_rest_of_the_batch_continues() -> None:
    artifacts = (_artifact(UUID1, data=UNREADABLE), _artifact(UUID2, data=XML2))
    harness = _harness(
        files={UUID1: UNREADABLE, UUID2: XML2},
        parser_faults={sha256_hex(UNREADABLE): XmlParseError("CFDI missing required attribute")},
        results={UUID2: _projected(UUID2)},
    )
    report = _process(harness, artifacts)
    assert _counts(report) == {"parsed": 1, "quarantined": 0, "failed": 1, "conflicted": 0}
    assert [record.uuid.value for record in harness.documents.saved] == [UUID2]
    assert len(harness.projector.calls) == 1  # a failed parse is never projected
    assert harness.uow.entries == 1
    assert report.message is not None
    assert UUID1 in report.message
    assert "parser fault" in report.message


def test_a_reader_failure_propagates_and_is_not_a_document_outcome() -> None:
    """§6a.2 store integrity is a stage failure, not a per-document verdict (D7)."""
    harness = _harness(reader_error=ExtractionError("extracted artifact 1 hash mismatch"))
    with pytest.raises(ExtractionError, match="hash mismatch"):
        _process(harness, (_artifact(),))
    assert harness.documents.saved == []
    assert harness.flags.opened == []
    assert harness.uow.entries == 0


def test_an_unexpected_parser_exception_is_never_swallowed() -> None:
    """Only the established per-document fault is caught; a bug must not look like data."""
    harness = _harness(parser_faults={HASH: ValueError("boom")})
    with pytest.raises(ValueError, match="boom"):
        _process(harness, (_artifact(),))
    assert harness.documents.saved == []


def test_a_hash_conflict_is_counted_and_rolls_back_only_its_own_transaction() -> None:
    artifacts = (
        _artifact(UUID1, data=XML),
        _artifact(UUID2, data=XML2),
        _artifact(UUID3, data=XML3),
    )
    harness = _harness(
        files={UUID1: XML, UUID2: XML2, UUID3: XML3},
        conflicts=frozenset({UUID2}),
    )
    report = _process(harness, artifacts)
    assert _counts(report) == {"parsed": 2, "quarantined": 0, "failed": 0, "conflicted": 1}
    assert [record.uuid.value for record in harness.documents.saved] == [UUID1, UUID3]
    assert (harness.uow.entries, harness.uow.commits, harness.uow.rollbacks) == (3, 2, 1)
    assert report.message is not None
    assert UUID2 in report.message


def test_every_persisted_write_happens_inside_a_transaction() -> None:
    """The projection write and its review facts are one unit of work, never loose."""
    flags = ReviewFlags().with_flag(ReviewFlag(ReviewFlagType.CFDI_SIGNATURE, "absent"))
    harness = _harness(gate=True, results={UUID1: _projected(UUID1, flags=flags)})
    report = _process(harness, (_artifact(),))
    assert _counts(report)["parsed"] == 1
    assert len(harness.documents.saved) == 1
    assert len(harness.flags.opened) == 1
    assert (harness.uow.entries, harness.uow.commits) == (1, 1)


def test_an_empty_batch_touches_no_collaborator() -> None:
    harness = _harness()
    report = _process(harness, ())
    assert report.counts == (
        ("parsed", 0),
        ("quarantined", 0),
        ("failed", 0),
        ("conflicted", 0),
    )
    assert report.message is None
    assert harness.reader.calls == []
    assert harness.clock.calls == 0
    assert harness.uow.entries == 0


def test_the_clock_is_read_once_per_projected_document() -> None:
    """One timestamp per *projectable* document: quarantine and failure consume none."""
    artifacts = (
        _artifact(UUID1, data=XML),
        _artifact(UUID2, data=UNREADABLE),
        _artifact(UUID3, data=XML3),
    )
    harness = _harness(
        files={UUID1: XML, UUID2: UNREADABLE, UUID3: XML3},
        parser_faults={sha256_hex(UNREADABLE): XmlParseError("bad")},
        results={UUID3: _projected(UUID3, quarantine_reason="no TFD UUID")},
    )
    report = _process(harness, artifacts)
    assert _counts(report) == {"parsed": 1, "quarantined": 1, "failed": 1, "conflicted": 0}
    assert harness.clock.calls == 1
    assert harness.documents.saved[0].first_seen_at == T0
    assert harness.documents.saved[0].last_seen_at == T0


def test_the_recorded_projection_carries_the_parsed_facts_and_the_artifact_hash() -> None:
    harness = _harness(
        results={UUID1: _projected(UUID1, moneda="USD", perspective=Perspective.EMITIDO)}
    )
    _process(harness, (_artifact(),))
    record = harness.documents.saved[0]
    assert record.uuid == Uuid(UUID1)
    assert record.contributor_rfc == RFC
    assert record.perspective is Perspective.EMITIDO
    assert (record.tipo, record.version, record.moneda) == ("I", "4.0", "USD")
    assert (record.emisor_rfc, record.receptor_rfc) == (ISSUER, RFC)
    assert record.source_hash == HASH  # the artifact identity the reader just verified
    assert (record.first_seen_at, record.last_seen_at) == (T0, T0)


def test_run_id_is_recorded_on_the_projection_and_absent_by_default() -> None:
    harness = _harness()
    _process(harness, (_artifact(),), run_id="run-7")
    _process(harness, (_artifact(),))
    assert [record.last_run_id for record in harness.documents.saved] == ["run-7", None]


def test_the_projector_receives_the_boundary_facts_it_needs() -> None:
    harness = _harness(verifier_outcome=SignatureOutcome.ABSENT, results={UUID1: _projected(UUID1)})
    _process(harness, (_artifact(),))
    raw, verdict, source_hash, contributor_rfc, artifact_uuid = harness.projector.calls[0]
    assert isinstance(raw, RawCfd)
    assert verdict == SignatureVerdict(SignatureOutcome.ABSENT)
    assert source_hash == HASH
    assert contributor_rfc == RFC
    assert artifact_uuid == UUID1


def test_each_open_flag_of_the_projection_is_persisted_once() -> None:
    flags = (
        ReviewFlags()
        .with_flag(ReviewFlag(ReviewFlagType.UNSUPPORTED_CURRENCY, "EUR is unsupported"))
        .with_flag(ReviewFlag(ReviewFlagType.CFDI_SIGNATURE, "cfdi signature absent"))
    )
    harness = _harness(results={UUID1: _projected(UUID1, flags=flags)})
    _process(harness, (_artifact(),))
    assert [
        (
            record.subject_kind,
            record.subject_id,
            record.flag.flag_type,
            record.flag.state,
            record.flag.reason,
            record.occurred_at,
        )
        for record in harness.flags.opened
    ] == [
        (
            ReviewSubjectKind.DOCUMENT,
            UUID1,
            ReviewFlagType.UNSUPPORTED_CURRENCY,
            ReviewFlagState.OPEN,
            "EUR is unsupported",
            T0,
        ),
        (
            ReviewSubjectKind.DOCUMENT,
            UUID1,
            ReviewFlagType.CFDI_SIGNATURE,
            ReviewFlagState.OPEN,
            "cfdi signature absent",
            T0,
        ),
    ]


def test_an_already_open_flag_is_not_opened_again() -> None:
    existing = ReviewFlagRecord(
        flag_id=7,
        subject_kind=ReviewSubjectKind.DOCUMENT,
        subject_id=UUID1,
        flag=ReviewFlag(ReviewFlagType.CFDI_SIGNATURE, "cfdi signature absent"),
        occurred_at=T1,
    )
    flags = ReviewFlags().with_flag(ReviewFlag(ReviewFlagType.CFDI_SIGNATURE, "absent again"))
    harness = _harness(already_open=(existing,), results={UUID1: _projected(UUID1, flags=flags)})
    _process(harness, (_artifact(),))
    assert harness.flags.opened == []  # the open fact is already there: nothing to add
    assert [
        record.flag_id for record in harness.flags.open_flags(ReviewSubjectKind.DOCUMENT, UUID1)
    ] == [7]


def test_closed_flags_are_history_and_are_not_persisted_as_state() -> None:
    flags = (
        ReviewFlags()
        .with_flag(ReviewFlag(ReviewFlagType.UNSUPPORTED_CURRENCY, "EUR"))
        .with_flag(ReviewFlag(ReviewFlagType.CFDI_SIGNATURE, "absent"))
        .close_open(ReviewFlagType.UNSUPPORTED_CURRENCY)
    )
    harness = _harness(results={UUID1: _projected(UUID1, flags=flags)})
    _process(harness, (_artifact(),))
    assert [record.flag.flag_type for record in harness.flags.opened] == [
        ReviewFlagType.CFDI_SIGNATURE
    ]


def test_a_valid_signature_adds_no_review_fact() -> None:
    harness = _harness()
    report = _process(harness, (_artifact(),))
    assert _counts(report)["parsed"] == 1
    assert harness.flags.opened == []
    assert harness.flags.queries == [(ReviewSubjectKind.DOCUMENT, UUID1)]


def test_a_second_run_re_saves_the_same_identity_and_opens_no_new_flag() -> None:
    """Idempotency belongs to the use case: same (uuid, source_hash), same open facts (D4)."""
    flags = ReviewFlags().with_flag(ReviewFlag(ReviewFlagType.CFDI_SIGNATURE, "absent"))
    harness = _harness(results={UUID1: _projected(UUID1, flags=flags)})
    _process(harness, (_artifact(),), run_id="run-1")
    report = _process(harness, (_artifact(),), run_id="run-2")
    assert _counts(report)["parsed"] == 1
    assert [(record.uuid.value, record.source_hash) for record in harness.documents.saved] == [
        (UUID1, HASH),
        (UUID1, HASH),
    ]
    assert [record.last_run_id for record in harness.documents.saved] == ["run-1", "run-2"]
    assert len(harness.flags.opened) == 1  # the second run added no duplicate fact


def test_the_use_case_takes_no_fiscal_event_collaborator() -> None:
    """M2.8 writes the current projection; the fiscal history has no writer here (§4)."""
    parameters = inspect.signature(ProcessDocumentsUseCase.__init__).parameters
    collaborators = sorted(name for name in parameters if name != "self")
    assert collaborators == [
        "clock",
        "documents",
        "flags",
        "parser",
        "projector",
        "reader",
        "transactions",
        "verifier",
    ]
    assert not [name for name in collaborators if "event" in name]


def test_the_process_module_imports_nothing_from_infrastructure_or_libraries() -> None:
    """Purity check: the use case must not reach into adapters or XML/DB libraries."""
    source = Path(process_documents_module.__file__ or "").read_text(encoding="utf-8")
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
