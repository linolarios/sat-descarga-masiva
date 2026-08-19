"""Opaque signing identity. The application sees only this Protocol — never a key."""

from __future__ import annotations

from typing import Protocol


class SigningIdentity(Protocol):
    @property
    def rfc(self) -> str: ...
    @property
    def cer_base64(self) -> str: ...
    def sign(self, data: bytes) -> bytes: ...
