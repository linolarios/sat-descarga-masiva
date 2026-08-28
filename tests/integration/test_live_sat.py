"""M1 live SAT acceptance test (opt-in, never CI).

Runs the REAL M1 pipeline against the SAT *test* environment with a FIEL de
pruebas provided via environment variables:

    SAT_FIEL_CER       path to the .cer (DER)
    SAT_FIEL_KEY       path to the .key (PKCS#8 DER)
    SAT_FIEL_PASSWORD  password for the .key

Usage:  pytest -m integration tests/integration/test_live_sat.py

- Missing credentials -> SKIPPED.
- Credentials present  -> the test really talks to SAT and FAILS if the
  pipeline cannot complete. An empty-but-COMPLETED window is a valid pass;
  a degraded run (timeout/error) is a failure, never skipped.
- Never run by CI / `make check` (they invoke `pytest -m "not integration"`).
- All live artifacts (ZIPs/XMLs/manifests) are written under tmp_path only;
  nothing live is committed.
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from pathlib import Path

import pytest
from satcfdi.models.signer import Signer
from satcfdi.pacs.sat import SAT, Environment

from sat_descarga_masiva.application.policies.backoff import ExponentialBackoff, PollingPolicy
from sat_descarga_masiva.application.policies.extraction import ExtractionPolicy
from sat_descarga_masiva.application.use_cases.execute_download import ExecuteDownloadUseCase
from sat_descarga_masiva.application.use_cases.ingest_downloaded_package import (
    IngestDownloadedPackageUseCase,
)
from sat_descarga_masiva.domain.enums.catalog import (
    Direction,
    DocumentStatus,
    RequestType,
    ServiceType,
)
from sat_descarga_masiva.domain.enums.request_state import RequestState
from sat_descarga_masiva.domain.model.manifest import Manifest
from sat_descarga_masiva.domain.model.query import DownloadQuery
from sat_descarga_masiva.domain.model.results import DownloadOutcome, Package
from sat_descarga_masiva.domain.model.source import ExtractedXml, sha256_hex
from sat_descarga_masiva.domain.model.value_objects import DateRange, Rfc
from sat_descarga_masiva.facade.client import SatDownloadClient, VersionInfo
from sat_descarga_masiva.infrastructure.credentials.fiel import CryptographyFielLoader
from sat_descarga_masiva.infrastructure.persistence.memory import InMemoryRequestRepository
from sat_descarga_masiva.infrastructure.sat.extract.extractor import SafeZipExtractor
from sat_descarga_masiva.infrastructure.sat.gateways.satcfdi_gateway import SatcfdiGateway
from sat_descarga_masiva.infrastructure.source.filesystem_sink import FilesystemSourceArtifactSink

pytestmark = pytest.mark.integration

_ENV_CER = "SAT_FIEL_CER"
_ENV_KEY = "SAT_FIEL_KEY"
_ENV_PASSWORD = "SAT_FIEL_PASSWORD"


class RealClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class _Sleeper:
    def sleep(self, duration: timedelta) -> None:
        time.sleep(duration.total_seconds())


class RecordingExecutor:
    """Observation seam around the real ExecuteDownloadUseCase (delegates + records)."""

    def __init__(self, inner: ExecuteDownloadUseCase) -> None:
        self._inner = inner
        self.outcomes: list[DownloadOutcome] = []

    def execute(self, query: DownloadQuery) -> DownloadOutcome:
        outcome = self._inner.execute(query)
        self.outcomes.append(outcome)
        return outcome


class RecordingIngester:
    """Observation seam around the real IngestDownloadedPackageUseCase."""

    def __init__(self, inner: IngestDownloadedPackageUseCase) -> None:
        self._inner = inner
        self.calls: list[tuple[Package, Manifest]] = []

    def ingest(self, package: Package, manifest: Manifest) -> tuple[ExtractedXml, ...]:
        self.calls.append((package, manifest))
        return self._inner.ingest(package, manifest)


def _requires_credentials() -> None:
    missing = [v for v in (_ENV_CER, _ENV_KEY, _ENV_PASSWORD) if not os.getenv(v)]
    if missing:
        pytest.skip(f"SAT FIEL test credentials are not configured (missing: {', '.join(missing)})")


def _credentials() -> tuple[Path, Path, str]:
    return (
        Path(os.environ[_ENV_CER]),
        Path(os.environ[_ENV_KEY]),
        os.environ[_ENV_PASSWORD],
    )


def _wire(root: Path) -> tuple[SatDownloadClient, RecordingExecutor, RecordingIngester, Rfc]:
    cer_path, key_path, password = _credentials()
    cer = cer_path.read_bytes()
    key = key_path.read_bytes()
    fiel = CryptographyFielLoader().load(cer, key, password)
    # Seam (intended): satcfdi's own Signer (below) signs SOAP, while `fiel` is
    # our SigningIdentity used by the domain/use case. Same FIEL material, two
    # loads on purpose — not accidental divergence.
    rfc = Rfc(fiel.rfc)
    clock = RealClock()

    signer = Signer.load(cer, key, password)
    sat = SAT(signer=signer, environment=Environment.TEST)
    gateway = SatcfdiGateway(sat=sat, signer=fiel, clock=clock)

    polling = PollingPolicy(
        initial_delay=timedelta(seconds=5),
        max_delay=timedelta(seconds=30),
        timeout=timedelta(minutes=5),
    )
    executor_uc = ExecuteDownloadUseCase(
        identity=fiel,
        auth=gateway,
        requests_gw=gateway,
        verifier=gateway,
        downloader=gateway,
        repository=InMemoryRequestRepository(),
        clock=clock,
        backoff=ExponentialBackoff(polling),
        polling=polling,
        sleeper=_Sleeper(),
    )
    executor = RecordingExecutor(executor_uc)

    sink = FilesystemSourceArtifactSink(root)
    extractor = SafeZipExtractor(
        root, ExtractionPolicy(max_total_bytes=50 * 1024 * 1024, max_entries=500)
    )
    ingester = RecordingIngester(IngestDownloadedPackageUseCase(sink=sink, extractor=extractor))

    client = SatDownloadClient(
        executor=executor,
        ingester=ingester,
        clock=clock,
        versions=VersionInfo(
            satcfdi_version=version("satcfdi"),
            application_version=version("sat-descarga-masiva"),
        ),
        policy_version=1,
    )
    return client, executor, ingester, rfc, clock


def _query(direction: Direction, rfc: Rfc, now: datetime) -> DownloadQuery:
    start = now - timedelta(days=3)
    return DownloadQuery(
        service=ServiceType.CFDI,
        direction=direction,
        request_type=RequestType.CFDI,
        date_range=DateRange(start, now),
        rfc_solicitante=rfc,
        document_status=DocumentStatus.VIGENTE,
    )


@pytest.mark.parametrize("direction", [Direction.EMITIDOS, Direction.RECIBIDOS])
def test_live_m1_facade_download_and_ingest(direction: Direction, tmp_path: Path) -> None:
    _requires_credentials()
    client, executor, ingester, rfc, clock = _wire(tmp_path)

    result = client.download_and_ingest(_query(direction, rfc, clock.now()))

    # Positive completion first: an empty-but-COMPLETED window is a valid pass;
    # a degraded run (timeout/error -> no packages) FAILS here, not after.
    assert len(executor.outcomes) == 1
    outcome = executor.outcomes[0]
    assert outcome.state is RequestState.COMPLETED
    assert outcome.request_id is not None
    # One ingest call per downloaded package; correlation chain holds.
    assert len(ingester.calls) == len(outcome.packages)
    for package, manifest in ingester.calls:
        assert manifest.request_id == outcome.request_id
        assert manifest.package_id == package.package_id
        assert manifest.sha256 == sha256_hex(package.content)  # ZIP bytes
    # When packages were downloaded, prove extraction produced domain output.
    if outcome.packages:
        assert result, f"{direction.value}: expected extracted XML from a non-empty package set"
