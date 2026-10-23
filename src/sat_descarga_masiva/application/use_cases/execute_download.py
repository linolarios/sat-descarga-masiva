"""Orchestrates the async workflow through ports only (no auth, sleep, network here)."""

from __future__ import annotations

from sat_descarga_masiva.application.policies.backoff import PollingPolicy
from sat_descarga_masiva.application.policies.sat_status import classify_verification
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
from sat_descarga_masiva.domain.enums.sat_status import ErrorClassification
from sat_descarga_masiva.domain.errors import (
    AuthenticationError,
    SatRequestFailedError,
    SatTimeoutError,
    UnexpectedSatResponseError,
)
from sat_descarga_masiva.domain.model.credentials import SigningIdentity
from sat_descarga_masiva.domain.model.query import DownloadQuery
from sat_descarga_masiva.domain.model.results import DownloadOutcome, VerificationResult
from sat_descarga_masiva.domain.model.token import AccessToken
from sat_descarga_masiva.domain.model.value_objects import RequestId, Rfc

# Actions that end the poll loop without an error: proceed, or a clean terminal
# (nothing found / limit reached) that yields no packages.
_CLEAN_TERMINALS = frozenset(
    {
        ErrorClassification.OK,
        ErrorClassification.NOT_FOUND,
        ErrorClassification.PARTITION,
        ErrorClassification.QUOTA_WAIT,
    }
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

    def execute(self, query: DownloadQuery) -> DownloadOutcome:
        """Run the full auth -> submit -> verify -> download workflow."""
        token = self._auth.authenticate(self._identity)
        submitted = self._requests.request(query, token)

        request_id = submitted.request_id
        if request_id is None:
            # Technical failure, not an expected SAT outcome:
            # the request must carry an id or the workflow cannot resume/track it.
            raise UnexpectedSatResponseError(
                "SAT request returned no request id",
                cod_estatus=submitted.cod_estatus.value,
                mensaje=submitted.mensaje,
            )
        self._repository.save(request_id)

        verification = self._wait_for_resolution(request_id, query.rfc_solicitante, token)
        if classify_verification(verification) is not ErrorClassification.OK:
            # Clean terminal (no results / limit reached): nothing to download.
            # The request_id is still surfaced for correlation/tracking.
            return DownloadOutcome(request_id=request_id, state=verification.state, packages=())
        return DownloadOutcome(
            request_id=request_id,
            state=verification.state,
            packages=tuple(
                self._downloader.download(package_id, query.rfc_solicitante, token)
                for package_id in verification.ids_paquetes
            ),
        )

    def _wait_for_resolution(
        self, request_id: RequestId, rfc: Rfc, token: AccessToken
    ) -> VerificationResult:
        """Poll until classification resolves the request, honoring backoff and the timeout."""
        attempt = 0
        deadline = self._clock.now() + self._polling.timeout
        while True:
            result = self._verifier.verify(request_id, rfc, token)
            action = classify_verification(result)
            if action is ErrorClassification.RETRY:
                if self._clock.now() >= deadline:
                    raise SatTimeoutError("verification did not resolve before the deadline")
                self._sleeper.sleep(self._backoff.delay(attempt))
                attempt += 1
                continue
            if action in _CLEAN_TERMINALS:
                return result
            raise _terminal_failure(action, result)


def _terminal_failure(action: ErrorClassification, result: VerificationResult) -> Exception:
    """Build the secret-free exception for a permanent SAT outcome (AGENT.md 6a)."""
    cod = result.cod_estatus.value
    if action is ErrorClassification.AUTHENTICATION:
        return AuthenticationError(
            f"SAT rejected the credentials or certificate "
            f"(EstadoSolicitud {int(result.state)}, CodEstatus {cod})"
        )
    return SatRequestFailedError(
        f"SAT reported a terminal failure (EstadoSolicitud {int(result.state)}, CodEstatus {cod})",
        estado_solicitud=int(result.state),
        cod_estatus=cod,
        mensaje=result.mensaje,
    )
