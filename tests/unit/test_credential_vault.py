"""CredentialVault: repr-safe FIEL password holder (§6/§7a/§10)."""

from sat_descarga_masiva.domain.model.value_objects import Rfc
from sat_descarga_masiva.infrastructure.credentials.vault import InMemoryCredentialVault

RFC = Rfc("AAA010101AAA")


def test_save_and_get_round_trips() -> None:
    vault = InMemoryCredentialVault()
    assert vault.get(RFC) is None
    vault.save(RFC, "s3cret")
    assert vault.get(RFC) == "s3cret"


def test_get_missing_rfc_returns_none() -> None:
    vault = InMemoryCredentialVault()
    other = Rfc("BBB020202BBB")
    assert vault.get(other) is None


def test_vault_is_repr_safe() -> None:
    vault = InMemoryCredentialVault()
    vault.save(RFC, "supersecretvalue")
    assert "supersecretvalue" not in repr(vault)
    assert "supersecretvalue" not in str(vault)


def test_save_overwrites_same_rfc() -> None:
    vault = InMemoryCredentialVault()
    vault.save(RFC, "first")
    vault.save(RFC, "second")
    assert vault.get(RFC) == "second"
