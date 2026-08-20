"""`requests`-backed adapters for the HttpClient/HttpResponse ports.

Transport-level failures are surfaced as the domain ``TransportError``;
HTTP status policy (404 vs retry) is NOT decided here — that belongs to the
gateway policies, per AGENT.md.
"""

from __future__ import annotations

import requests

from sat_descarga_masiva.application.ports.services import HttpResponse
from sat_descarga_masiva.domain.errors import TransportError


class RequestsHttpResponse(HttpResponse):
    """HTTP response as `status` + raw `body` bytes."""

    def __init__(self, status: int, body: bytes) -> None:
        self._status = status
        self._body = body

    @property
    def status(self) -> int:
        return self._status

    @property
    def body(self) -> bytes:
        return self._body


class RequestsHttpClient:
    """Implements the HttpClient port (structural). No status policy."""

    def post(self, url: str, headers: dict[str, str], body: bytes) -> HttpResponse:
        try:
            response = requests.post(url, headers=headers, data=body)
        except requests.RequestException as exc:
            raise TransportError(f"transport failure posting to {url!r}") from exc
        return RequestsHttpResponse(status=response.status_code, body=response.content)
