"""Ports for cross-cutting collaborators."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol

from sat_descarga_masiva.domain.model.credentials import SigningIdentity


class Clock(Protocol):
    def now(self) -> datetime: ...


class BackoffStrategy(Protocol):
    def delay(self, attempt: int) -> timedelta: ...


class HttpResponse(Protocol):
    @property
    def status(self) -> int: ...
    @property
    def body(self) -> bytes: ...


class HttpClient(Protocol):
    def post(self, url: str, headers: dict[str, str], body: bytes) -> HttpResponse: ...


class FielLoader(Protocol):
    def load(self, cer: bytes, key: bytes, password: str) -> SigningIdentity: ...
