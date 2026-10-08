"""Tier-2: the M3 accounting chain — a projected CFDI becomes a durable posting (§8/§11 M3).

Tier-1 (`tests/unit/test_m3_accounting_use_case.py`) proves the stage against recording doubles.
This tier crosses the seams with production code only: the real M2.6 projection composes the
document, its perspective and its review state; the real `YamlMappingProvider` reads the
committed chart of accounts and its ``ClaveProdServ → AccountingCategory`` table; the real
`propose` selects §8's row; the real `PostingEligibilityValidator` assigns the posting state;
and the real in-memory `JournalEntryStore` is read back through its own reader. Only the clock is
fixed, so every key, account and timestamp below is reproducible.

What this tier is *for* is the join no unit test can see: that the version recorded on a posting
is the version the committed YAML actually declares, that the accounts on the legs are the ones a
human wrote in that file, and that §8:194's ``entry_key`` recomputes from the record's own
columns — i.e. that the ledger row explains itself without today's mapping, today's policy or
today's code.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from sat_descarga_masiva.application.ports.persistence import MetadataSnapshotStore
from sat_descarga_masiva.application.use_cases.execute_accounting import (
    AccountingResult,
    ExecuteAccountingUseCase,
)
from sat_descarga_masiva.contabilidad.journal import PostingFingerprint, PostingState
from sat_descarga_masiva.contabilidad.roles import AccountRole
from sat_descarga_masiva.contabilidad.rules.posting import SUPPORTED_RULES
from sat_descarga_masiva.contabilidad.validator import PostingEligibilityValidator
from sat_descarga_masiva.domain.errors import MappingNotConfigured
from sat_descarga_masiva.domain.model.fiscal_document import FiscalDocumentStatus
from sat_descarga_masiva.domain.model.metadata_snapshot import MetadataSnapshot
from sat_descarga_masiva.domain.model.raw_cfd import (
    RawCfd,
    RawConcepto,
    RawDoctoRelacionado,
    RawImpuestos,
    RawImpuestosDR,
    RawPago,
    RawPagos20,
    RawTraslado,
    RawTrasladoDR,
)
from sat_descarga_masiva.domain.model.review import ReviewFlagType
from sat_descarga_masiva.domain.model.signature import SignatureOutcome, SignatureVerdict
from sat_descarga_masiva.domain.model.value_objects import Rfc, Uuid
from sat_descarga_masiva.fiscal.projection import ProcessedDocument, project_document
from sat_descarga_masiva.infrastructure.mapping.yaml_mapping import YamlMappingProvider
from sat_descarga_masiva.infrastructure.persistence.memory import (
    InMemoryJournalEntryStore,
    InMemoryMetadataSnapshotStore,
)

#: The committed charts of accounts, one YAML per client (§8a:207).
MAPPINGS = Path(__file__).parent.parent / "fixtures" / "mappings"
#: The client whose committed chart this chain books against.
CLIENT = Rfc("AAA010101AAA")
#: A second RFC: the same CFDI would land in another client's books under another chart (§8:194).
OTHER = Rfc("BBB010101BBB")
TFD_UUID = Uuid("333E4567-E89B-12D3-A456-426614174000")
SOURCE_HASH = "c" * 64
FECHA = "2024-05-15T10:30:00"
#: §6a.3's authenticity fact for a document whose sello was established, so M2.5 opens no flag.
VERIFIED = SignatureVerdict(SignatureOutcome.VALID, "the emisor sello and the TFD SelloSAT verify")
#: The one fixed instant: tier-2 evidence is deterministic, never "now" (§8:166).
RECORDED_AT = datetime(2026, 2, 1, 9, 30, tzinfo=UTC)
#: A ClaveProdServ the committed chart classifies as `gasto` (§8a:207).
GASTO = "84111506"
#: The CFDI a REP settles — a UUID of its own, distinct from the REP's TFD UUID (§8:194).
SETTLED = Uuid("111E4567-E89B-12D3-A456-426614174000")
#: §8:159's condition 1 as this tier supplies it: a *resolved* VIGENTE source state. The metadata
#: join that resolves it (§6:123) is wired in by ``_chain(snapshots=...)``; absent that store the
#: projection's own value is the fail-closed `UNKNOWN`, which §8:159 refuses.
VIGENTE = FiscalDocumentStatus.VIGENTE


@dataclass(frozen=True)
class _FixedClock:
    """The single instant every record in this tier is dated by (§8:166)."""

    moment: datetime

    def now(self) -> datetime:
        return self.moment


@dataclass(frozen=True)
class _Chain:
    """One wired run: the production use case plus the store it writes through."""

    use_case: ExecuteAccountingUseCase
    store: InMemoryJournalEntryStore

    def execute(self, processed: ProcessedDocument, *, client: Rfc = CLIENT) -> AccountingResult:
        return self.use_case.execute(processed, contributor_rfc=client)

    def appended(self) -> tuple[object, ...]:
        """Journal records read back through the port's reader, not the adapter's dict."""
        return self.store.for_source(CLIENT, TFD_UUID)


def _chain(root: Path = MAPPINGS, *, snapshots: MetadataSnapshotStore | None = None) -> _Chain:
    """The production chain, wired the way the application layer wires it (§8a:207).

    ``snapshots`` is the metadata join (§6:123): without it the chain still books a document the
    projection already resolved, but a document handed over ``UNKNOWN`` has nothing to resolve it.
    """
    provider = YamlMappingProvider(root)
    store = InMemoryJournalEntryStore()
    return _Chain(
        use_case=ExecuteAccountingUseCase(
            mapping=provider,
            # The registry is the engine's own: a rule id or version outside it cannot post.
            validator=PostingEligibilityValidator(provider, supported_rules=SUPPORTED_RULES),
            store=store,
            clock=_FixedClock(RECORDED_AT),
            snapshots=snapshots,
        ),
        store=store,
    )


def _observation(status: FiscalDocumentStatus) -> MetadataSnapshot:
    """The SAT's last word on this chain's CFDI, as a metadata fact (§6)."""
    return MetadataSnapshot(
        uuid=TFD_UUID,
        contributor_rfc=CLIENT,
        status=status,
        retrieved_at=RECORDED_AT,
        source_hash=SOURCE_HASH,
    )


def _raw(**overrides: object) -> RawCfd:
    """A coherent MXN PUE purchase as *source* facts, so a test can aim it at one situation."""
    base: dict[str, object] = {
        "tipo": "I",
        "version": "4.0",
        "moneda": "MXN",
        "tipo_cambio": None,
        "emisor_rfc": OTHER.value,
        "receptor_rfc": CLIENT.value,
        "conceptos": (RawConcepto(GASTO, "1", "100.00", "100.00", None),),
        "impuestos": RawImpuestos(
            traslados=(RawTraslado("002", "Tasa", "0.16", "16.00"),), total_traslados="16.00"
        ),
        "total": "116.00",
        "uuid": TFD_UUID.value,
        "subtotal": "100.00",
        "fecha": FECHA,
        "metodo_pago": "PUE",
    }
    base.update(overrides)
    return RawCfd(**base)  # type: ignore[arg-type]


def _rep_raw(**overrides: object) -> RawCfd:
    """A coherent MXN REP as *source* facts: one payment collecting one issued invoice (§8:192)."""
    base: dict[str, object] = {
        "tipo": "P",
        "version": "4.0",
        "moneda": "MXN",
        "tipo_cambio": None,
        "emisor_rfc": CLIENT.value,
        "receptor_rfc": OTHER.value,
        "conceptos": (RawConcepto(GASTO, "1", "0.00", "0.00", None),),
        "impuestos": RawImpuestos(),
        "total": "0.00",
        "subtotal": "0.00",
        "uuid": TFD_UUID.value,
        "fecha": FECHA,
        "metodo_pago": None,
        "pagos": RawPagos20(
            version="2.0",
            pagos=(
                RawPago(
                    fecha_pago="2024-06-01T09:00:00",
                    forma_de_pago_p="03",
                    moneda_p="MXN",
                    monto="116.00",
                    doctos_relacionados=(
                        RawDoctoRelacionado(
                            id_documento=SETTLED.value,
                            moneda_dr="MXN",
                            num_parcialidad="1",
                            imp_saldo_ant="116.00",
                            imp_pagado="116.00",
                            objeto_imp_dr="01",
                            imp_saldo_insoluto="0.00",
                            impuestos_dr=RawImpuestosDR(
                                traslados_dr=(
                                    RawTrasladoDR(
                                        base_dr="100.00",
                                        impuesto_dr="002",
                                        tipo_factor_dr="Tasa",
                                        importe_dr="16.00",
                                    ),
                                )
                            ),
                        ),
                    ),
                ),
            ),
        ),
    }
    base.update(overrides)
    return RawCfd(**base)  # type: ignore[arg-type]


def _projected(
    raw: RawCfd, *, client: Rfc = CLIENT, status: FiscalDocumentStatus = VIGENTE
) -> ProcessedDocument:
    """The M2.6 projection over production code: parse + perspective + authenticity (§6).

    ``status`` is set explicitly because the projection never resolves it: the fiscal status is
    a *metadata* fact (§6:123) that the join in ``_chain(snapshots=...)`` — or, absent that store,
    the caller — supplies. §8:159 refuses the fail-closed ``UNKNOWN`` (``UNKNOWN_SOURCE_STATE``),
    so every leg below that asserts a posting states the eligibility it would have been handed —
    instead of the refusal it would otherwise get, which would make these tests pass for the wrong
    reason. Tests that need the join itself pass a store to ``_chain`` and let it resolve.
    """
    projected = project_document(
        raw,
        VERIFIED,
        source_hash=SOURCE_HASH,
        contributor_rfc=client,
        artifact_uuid=raw.uuid or "",
    )
    if projected.document is None:  # pragma: no cover - every raw below is a readable CFDI 4.0
        raise AssertionError(f"the fixture did not parse: {projected.quarantine_reason}")
    return replace(projected, document=replace(projected.document, status=status))


def _declared_mapping_version(client: Rfc = CLIENT) -> str:
    """The version the committed chart *file* declares — read from the YAML, not from code."""
    file = MAPPINGS / f"{client.value}.yaml"
    document = yaml.safe_load(file.read_text(encoding="utf-8"))
    return str(document["mapping_version"])


# --- the chain, end to end --------------------------------------------------------------


def test_a_received_purchase_posts_the_accounts_the_committed_chart_names() -> None:
    """§8:181/§8a:207: the legs carry the accounts a human wrote in `<RFC>.yaml`."""
    chain = _chain()
    result = chain.execute(_projected(_raw()))

    assert result.decision.posting_state is PostingState.POSTED
    record = result.record
    assert record is not None
    assert record.mapping_version == _declared_mapping_version()
    assert [
        (line.account_role, line.side.value, str(line.amount.amount), line.resolved_account)
        for line in record.lines
    ] == [
        (AccountRole.GASTO, "debe", "100.00", "601-001"),
        (AccountRole.IVA_ACRED_PAGADO, "debe", "16.00", "118-001"),
        (AccountRole.CLEARING, "haber", "116.00", "102-099"),
    ]
    assert record.recorded_at == RECORDED_AT
    assert chain.appended() == (record,)


def test_an_issued_income_comprobante_posts_against_the_clearing_account() -> None:
    """§8:179: the same chart, the other perspective — money in through `clearing`."""
    chain = _chain()
    result = chain.execute(_projected(_raw(emisor_rfc=CLIENT.value, receptor_rfc=OTHER.value)))

    assert result.decision.posting_state is PostingState.POSTED
    assert result.record is not None
    assert [
        (line.account_role, line.side.value, line.resolved_account) for line in result.record.lines
    ] == [
        (AccountRole.CLEARING, "debe", "102-099"),
        (AccountRole.INGRESOS, "haber", "401-001"),
        (AccountRole.IVA_TRASLADADO_COBRADO, "haber", "208-001"),
    ]


def test_a_traslado_is_skipped_with_no_legs_and_a_recomputable_key() -> None:
    """§8:161: a skip is a decision, so it is persisted with §8:194's identity and nothing else."""
    chain = _chain()
    result = chain.execute(_projected(_raw(tipo="T")))

    assert result.decision.posting_state is PostingState.SKIPPED
    assert result.decision.review_flags == ()
    record = result.record
    assert record is not None
    assert record.rule_id == "4.12"
    assert record.lines == ()
    assert chain.appended() == (record,)


def test_a_foreign_currency_document_is_recorded_as_proposed_never_posted() -> None:
    """§8:193: without a resolved rate there is no deterministic valuation, so a human decides."""
    chain = _chain()
    result = chain.execute(_projected(_raw(moneda="USD", tipo_cambio="17.5")))

    assert result.decision.posting_state is PostingState.PROPOSED
    assert ReviewFlagType.AMBIGUOUS_FX in [flag.flag_type for flag in result.decision.review_flags]
    assert result.record is not None
    assert result.record.posting_state is PostingState.PROPOSED
    assert chain.appended() == (result.record,)


def test_the_metadata_join_resolves_the_source_state_and_the_document_posts() -> None:
    """§6:123: the join closes the gap — the SAT's observation resolves the projection's `UNKNOWN`.

    M2.6 resolves no fiscal status; §8:159 refuses an unresolved one. The metadata join is what
    completes the chain: handed the *same* `UNKNOWN` document, the chain books it once a VIGENTE
    observation for the CFDI is present.
    """
    snapshots = InMemoryMetadataSnapshotStore()
    snapshots.append(_observation(FiscalDocumentStatus.VIGENTE))
    chain = _chain(snapshots=snapshots)
    result = chain.execute(_projected(_raw(), status=FiscalDocumentStatus.UNKNOWN))

    assert result.decision.posting_state is PostingState.POSTED
    assert result.record is not None
    assert result.record.posting_state is PostingState.POSTED
    assert chain.appended() == (result.record,)


def test_the_projection_alone_cannot_post_before_the_metadata_join_resolves_it() -> None:
    """§8:159 + §6:123: with no observation, M2.6's `UNKNOWN` is refused — nothing uncertain posts.

    The honest fail-closed half of the join: a document handed straight from the projection, with
    no metadata store to resolve its source state, is refused with a reason a human can read. The
    refusal is recorded, so the fact that nothing was posted is itself auditable.
    """
    chain = _chain()  # no metadata store wired: there is nothing to resolve the status with
    result = chain.execute(_projected(_raw(), status=FiscalDocumentStatus.UNKNOWN))

    assert result.decision.posting_state is PostingState.PROPOSED
    assert ReviewFlagType.UNKNOWN_SOURCE_STATE in [
        flag.flag_type for flag in result.decision.review_flags
    ]
    assert result.record is not None
    assert result.record.posting_state is not PostingState.POSTED
    assert chain.appended() == (result.record,)


def test_a_client_without_a_committed_chart_stops_the_run(tmp_path: Path) -> None:
    """§8a:207: a configuration failure raises — it is not one flag per document."""
    chain = _chain(tmp_path)  # an empty mapping directory: no chart for anybody

    with pytest.raises(MappingNotConfigured):
        chain.execute(_projected(_raw()))

    assert chain.appended() == ()


def test_the_ledger_row_explains_itself_without_todays_mapping() -> None:
    """§8:194: `entry_key` recomputes from the record's own columns, not from today's code.

    This is the audit property the whole stage exists to preserve: a row read back years later —
    with a different mapping, a different policy version and a different rule version in force —
    still names the decision it was written under, and the key it was written under is derivable
    from the row itself.
    """
    chain = _chain()
    result = chain.execute(_projected(_raw()))
    record = result.record
    assert record is not None

    recomputed = PostingFingerprint(
        contributor_rfc=record.contributor_rfc,
        source_uuid=record.source_uuid,
        rule_id=record.rule_id,
        rule_version=record.rule_version,
        line_key="",
        mapping_version=record.mapping_version,
    )
    assert record.entry_key == recomputed.canonical()

    entry = result.decision.entry
    assert entry is not None
    assert record.entry_key == entry.fingerprint(record.mapping_version).canonical()
    assert record.policy_version  # §8:159 records it with every posting
    assert record.entry_date.isoformat() == "2024-05-15"  # §8a:209: the document's own Fecha


def test_a_rep_reclassifies_a_collection_the_ledger_already_holds() -> None:
    """§8:184/§8:192: a REP adds no role — it settles the receivable an issued invoice created.

    The chain is exercised twice, production code all the way. First the REP is refused, because
    the client's books hold no `POSTED` original for the CFDI it settles and §8:192's presence is a
    *ledger* fact the rule cannot read for itself. Then the issued PPD invoice is posted by the very
    same chain, so the original booking now exists, and the REP that collects it reclassifies
    `CLIENTES` and its IVA — the accounts, and only the accounts, the committed chart names.
    """
    refused = _chain()
    unbooked = refused.execute(_projected(_rep_raw()))
    assert unbooked.decision.posting_state is PostingState.PROPOSED
    assert ReviewFlagType.MISSING_REP_ORIGINAL in [
        flag.flag_type for flag in unbooked.decision.review_flags
    ]

    chain = _chain()
    invoice = chain.execute(
        _projected(
            _raw(
                emisor_rfc=CLIENT.value,
                receptor_rfc=OTHER.value,
                metodo_pago="PPD",
                uuid=SETTLED.value,
            )
        )
    )
    assert invoice.decision.posting_state is PostingState.POSTED  # the receivable now exists

    result = chain.execute(_projected(_rep_raw()))
    assert result.decision.posting_state is PostingState.POSTED
    record = result.record
    assert record is not None
    assert record.rule_id == "4.5a"
    assert record.mapping_version == _declared_mapping_version()
    assert [
        (line.account_role, line.side.value, str(line.amount.amount), line.resolved_account)
        for line in record.lines
    ] == [
        (AccountRole.CLEARING, "debe", "116.00", "102-099"),
        (AccountRole.CLIENTES, "haber", "116.00", "105-001"),
        (AccountRole.IVA_TRASLADADO_NO_COBRADO, "debe", "16.00", "209-001"),
        (AccountRole.IVA_TRASLADADO_COBRADO, "haber", "16.00", "208-001"),
    ]
    assert record.recorded_at == RECORDED_AT
    assert chain.appended() == (record,)
