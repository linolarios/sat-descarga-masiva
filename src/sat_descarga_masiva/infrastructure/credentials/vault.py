"""CredentialVault adapters — in-memory for tests/dev, repr-safe (never leaks the password)."""

from __future__ import annotations

from sat_descarga_masiva.domain.model.value_objects import Rfc


class InMemoryCredentialVault:
    """Implements application.ports.services.CredentialVault; keeps passwords out of repr."""

    def __init__(self) -> None:
        self._passwords: dict[str, str] = {}

    def save(self, client_rfc: Rfc, password: str) -> None:
        self._passwords[client_rfc.value] = password

    def get(self, client_rfc: Rfc) -> str | None:
        return self._passwords.get(client_rfc.value)

    def __repr__(self) -> str:
        return "InMemoryCredentialVault()"
