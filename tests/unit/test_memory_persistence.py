"""In-memory TokenStore / RequestRepository adapters (offline, deterministic)."""

from datetime import datetime, timedelta

from sat_descarga_masiva.domain.model.token import AccessToken
from sat_descarga_masiva.domain.model.value_objects import RequestId
from sat_descarga_masiva.infrastructure.persistence.memory import (
    InMemoryRequestRepository,
    InMemoryTokenStore,
)

HOUR = timedelta(hours=1)


def _token(value: str = "t") -> AccessToken:
    now = datetime(2026, 1, 1)
    return AccessToken(value, now, now + HOUR)


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


def test_token_store_keys_are_independent() -> None:
    store = InMemoryTokenStore()
    store.save("a", _token("t1"))
    store.save("b", _token("t2"))
    assert store.get("a").value == "t1"
    assert store.get("b").value == "t2"


def test_token_store_overwrites_on_same_key() -> None:
    store = InMemoryTokenStore()
    store.save("auth", _token("t1"))
    store.save("auth", _token("t2"))
    assert store.get("auth").value == "t2"
    assert store.get("auth") == _token("t2")


def test_request_repository_save_is_idempotent() -> None:
    repo = InMemoryRequestRepository()
    rid = RequestId("4e80345d-917f-40bb-a98f-4a73939353c5")
    repo.save(rid)
    repo.save(rid)
    assert repo.get(rid) == rid


def test_request_repository_tracks_distinct_ids() -> None:
    repo = InMemoryRequestRepository()
    a = RequestId("4e80345d-917f-40bb-a98f-4a73939353c5")
    b = RequestId("4e80345d-917f-40bb-a98f-4a73939354d0")
    repo.save(a)
    repo.save(b)
    assert repo.get(a) == a
    assert repo.get(b) == b
    assert repo.get(RequestId("4e80345d-917f-40bb-a98f-4a73939396a1")) is None


def test_request_repository_returns_the_saved_instance() -> None:
    repo = InMemoryRequestRepository()
    rid = RequestId("4e80345d-917f-40bb-a98f-4a73939353c5")
    repo.save(rid)
    assert repo.get(rid) is rid
