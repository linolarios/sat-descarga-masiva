"""In-memory TokenStore / RequestRepository adapters (offline, deterministic)."""

from datetime import datetime, timedelta

from sat_descarga_masiva.domain.model.token import AccessToken
from sat_descarga_masiva.domain.model.value_objects import RequestId
from sat_descarga_masiva.infrastructure.persistence.memory import (
    InMemoryRequestRepository,
    InMemoryTokenStore,
)

HOUR = timedelta(hours=1)


def _token() -> AccessToken:
    now = datetime(2026, 1, 1)
    return AccessToken("t", now, now + HOUR)


def test_token_store_round_trips() -> None:
    store = InMemoryTokenStore()
    assert store.get("auth") is None
    store.save("auth", _token())
    assert store.get("auth") == _token()


def test_request_repository_round_trips() -> None:
    repo = InMemoryRequestRepository()
    rid = RequestId("4e80345d-917f-40bb-a98f-4a73939353c5")
    assert repo.get(rid) is None
    repo.save(rid)
    assert repo.get(rid) == rid
