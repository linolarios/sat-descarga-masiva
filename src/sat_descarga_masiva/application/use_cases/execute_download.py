"""Orchestrates the async workflow through ports only (no auth, sleep, network here)."""

from __future__ import annotations

from sat_descarga_masiva.application.policies.backoff import PollingPolicy
from sat_descarga_masiva.application.ports.gateways import (
    AuthenticationGateway,
    DownloadGateway,
    RequestGateway,
    VerificationGateway,
)
from sat_descarga_masiva.application.ports.persistence import RequestRepository
from sat_descarga_masiva.application.ports.services import (
    BackoffStrategy,
    Clock,
    Sleeper,
)
from sat_descarga_masiva.domain.enums.request_state import RequestState
from sat_descarga_masiva.domain.errors import SatTimeoutError, UnexpectedSatResponseError
from sat_descarga_masiva.domain.model.credentials import SigningIdentity
from sat_descarga_masiva.domain.model.query import DownloadQuery
from sat_descarga_masiva.domain.model.results import Package, VerificationResult
from sat_descarga_masiva.domain.model.token import AccessToken
from sat_descarga_masiva.domain.model.value_objects import RequestId, Rfc

# Terminal states produce no further packages; ACCEPTED/PROCESSING keep polling.
_TERMINAL_STATES = (
    RequestState.COMPLETED,
    RequestState.ERROR,
    RequestState.REJECTED,
    RequestState.EXPIRED,
)


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
        sleeper: Sleeper,
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
        self._sleeper = sleeper

    def execute(self, query: DownloadQuery) -> list[Package]:
        """Run the full auth -> submit -> verify -> download workflow."""
        token = self._auth.authenticate(self._identity)
        submitted = self._requests.request(query, token)

        request_id = submitted.request_id
        if request_id is None:
            # Technical failure, not an expected SAT outcome:
            # the request must carry an id or the workflow cannot resume/track it.
            raise UnexpectedSatResponseError("SAT request returned no request id")
        self._repository.save(request_id)

        verification = self._wait_for_resolution(request_id, query.rfc_solicitante, token)
        if verification.state is not RequestState.COMPLETED:
            # Resolved but nothing to deliver: an expected outcome, not an error.
            return []
        return [
            self._downloader.download(package_id, query.rfc_solicitante, token)
            for package_id in verification.ids_paquetes
        ]

    def _wait_for_resolution(
        self, request_id: RequestId, rfc: Rfc, token: AccessToken
    ) -> VerificationResult:
        """Poll until a terminal state, honoring backoff and the poll timeout."""
        attempt = 0
        deadline = self._clock.now() + self._polling.timeout
        while True:
            result = self._verifier.verify(request_id, rfc, token)
            if result.state in _TERMINAL_STATES:
                return result
            if self._clock.now() >= deadline:
                raise SatTimeoutError("verification did not resolve before the deadline")
            self._sleeper.sleep(self._backoff.delay(attempt))
            attempt += 1
