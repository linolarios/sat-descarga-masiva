"""Ports for cross-cutting collaborators."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol

from sat_descarga_masiva.domain.model.credentials import SigningIdentity
from sat_descarga_masiva.domain.model.value_objects import Rfc


class Clock(Protocol):
    def now(self) -> datetime: ...


class BackoffStrategy(Protocol):
    def delay(self, attempt: int) -> timedelta: ...


class Sleeper(Protocol):
    """Abstracts the wait so the application never calls time.sleep (AGENT.md §3)."""

    def sleep(self, duration: timedelta) -> None: ...


class HttpResponse(Protocol):
    @property
    def status(self) -> int: ...
    @property
    def body(self) -> bytes: ...


class HttpClient(Protocol):
    def post(self, url: str, headers: dict[str, str], body: bytes) -> HttpResponse: ...


class FielLoader(Protocol):
    def load(self, cer: bytes, key: bytes, password: str) -> SigningIdentity: ...


class CredentialVault(Protocol):
    """Resolves the FIEL password for a client (§6/§7a). Never leaks in repr/logs."""

    def get(self, client_rfc: Rfc) -> str | None: ...
    def save(self, client_rfc: Rfc, password: str) -> None: ...
