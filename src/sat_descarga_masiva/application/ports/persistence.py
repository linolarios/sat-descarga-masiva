"""Ports for resumability and package storage."""

from __future__ import annotations

from typing import Protocol

from sat_descarga_masiva.domain.model.results import Package
from sat_descarga_masiva.domain.model.token import AccessToken
from sat_descarga_masiva.domain.model.value_objects import PackageId, RequestId


class TokenStore(Protocol):
    def get(self, key: str) -> AccessToken | None: ...
    def save(self, key: str, token: AccessToken) -> None: ...


class RequestRepository(Protocol):
    def save(self, request_id: RequestId) -> None: ...
    def get(self, request_id: RequestId) -> RequestId | None: ...


class PackageSink(Protocol):
    def store(self, package: Package) -> PackageId: ...
