"""Requests-backed HttpClient adapter tested offline (no SAT)."""

import pytest
import requests
import responses

from sat_descarga_masiva.domain.errors import TransportError
from sat_descarga_masiva.infrastructure.http.requests_client import RequestsHttpClient

URL = "https://example.test/soap"


@responses.activate
def test_post_returns_status_and_body() -> None:
    responses.post(URL, body=b"<responses/>", status=200)

    client = RequestsHttpClient()
    result = client.post(URL, headers={"SOAPAction": "Autenticacion"}, body=b"<req/>")

    assert result.status == 200
    assert result.body == b"<responses/>"


def test_transport_failure_raises_typed_error(mocker) -> None:
    client = RequestsHttpClient()
    mocker.patch("requests.post", side_effect=requests.ConnectionError("boom"))

    with pytest.raises(TransportError):
        client.post(URL, {}, b"")
