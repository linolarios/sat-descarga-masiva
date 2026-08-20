"""In-memory adapters for TokenStore and RequestRepository (deterministic, resumes)."""

from __future__ import annotations

from sat_descarga_masiva.domain.model.token import AccessToken
from sat_descarga_masiva.domain.model.value_objects import RequestId


class InMemoryTokenStore:
    """TokenStore port backed by an in-memory dict (nothing persists)."""

    def __init__(self) -> None:
        self._tokens: dict[str, AccessToken] = {}

    def get(self, key: str) -> AccessToken | None:
        return self._tokens.get(key)

    def save(self, key: str, token: AccessToken) -> None:
        self._tokens[key] = token


class InMemoryRequestRepository:
    """RequestRepository port: tracks request ids in memory for resume."""

    def __init__(self) -> None:
        self._request_ids: set[RequestId] = set()

    def save(self, request_id: RequestId) -> None:
        self._request_ids.add(request_id)

    def get(self, request_id: RequestId) -> RequestId | None:
        return request_id if request_id in self._request_ids else None
