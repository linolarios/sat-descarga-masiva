"""Orchestrates the async workflow through ports only (no network/sleep/FIEL here)."""

from __future__ import annotations

from sat_descarga_masiva.application.policies.backoff import PollingPolicy
from sat_descarga_masiva.application.ports.gateways import (
    AuthenticationGateway,
    DownloadGateway,
    RequestGateway,
    VerificationGateway,
)
from sat_descarga_masiva.application.ports.persistence import RequestRepository
from sat_descarga_masiva.application.ports.services import BackoffStrategy, Clock
from sat_descarga_masiva.domain.model.credentials import SigningIdentity
from sat_descarga_masiva.domain.model.query import DownloadQuery
from sat_descarga_masiva.domain.model.results import Package


class ExecuteDownloadUseCase:
    def __init__(  # noqa: PLR0913 (each collaborator is a real boundary)
        self,
        *,
        identity: SigningIdentity,
        auth: AuthenticationGateway,
        requests_gw: RequestGateway,
        verifier: VerificationGateway,
        downloader: DownloadGateway,
        repository: RequestRepository,
        clock: Clock,
        backoff: BackoffStrategy,
        polling: PollingPolicy,
    ) -> None:
        self._identity = identity
        self._auth = auth
        self._requests = requests_gw
        self._verifier = verifier
        self._downloader = downloader
        self._repository = repository
        self._clock = clock
        self._backoff = backoff
        self._polling = polling

    def execute(self, query: DownloadQuery) -> list[Package]:
        # TODO (TDD milestone 10): authenticate -> submit -> persist -> poll -> download.
        raise NotImplementedError
