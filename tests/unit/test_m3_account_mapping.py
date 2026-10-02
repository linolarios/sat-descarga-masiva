"""M3 step 4a: the account mapping — its port, its value object, and its YAML adapter (§8a:207).

A rule names an `AccountRole`; the client's chart of accounts decides which account that is,
*under a version* (§8:194/196). These tests pin the two properties the engine relies on:

- **versioned or nothing**: a mapping without a `mapping_version` cannot exist, because that
  version is part of every posting's identity;
- **resolved or flagged**: a role the mapping does not name resolves to ``None`` — never a
  default, and never a guess — while a *broken* mapping (unknown role name, empty account,
  malformed YAML, no file for the client) fails loudly instead of silently unmapping.

The YAML layout is the shape proposed for approval in §8a:207, and the fixture the posting
tests use is asserted here to cover exactly the roles the issuing rules need.
"""

from pathlib import Path

import pytest

from sat_descarga_masiva.application.ports.accounting import AccountMapping, MappingProvider
from sat_descarga_masiva.contabilidad.roles import AccountRole
from sat_descarga_masiva.domain.errors import InvalidMapping, MappingNotConfigured
from sat_descarga_masiva.domain.model.value_objects import Rfc
from sat_descarga_masiva.infrastructure.mapping.yaml_mapping import YamlMappingProvider

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "mappings"
_CLIENT = Rfc("AAA010101AAA")
_OTHER_CLIENT = Rfc("CCC010101CCC")
_VERSION = "2026.01"

#: The roles the emitter-side income rules (4.1/4.2) resolve; the fixture must cover them.
_EMITTER_ROLES = frozenset(
    {
        AccountRole.INGRESOS,
        AccountRole.CLIENTES,
        AccountRole.CLEARING,
        AccountRole.IVA_TRASLADADO_COBRADO,
        AccountRole.IVA_TRASLADADO_NO_COBRADO,
    }
)


def _provider(root: Path | str = _FIXTURES) -> YamlMappingProvider:
    return YamlMappingProvider(root)


def _write(tmp_path: Path, text: str, name: str = f"{_CLIENT.value}.yaml") -> Path:
    (tmp_path / name).write_text(text, encoding="utf-8")
    return tmp_path


def _resolved(provider: MappingProvider, client: Rfc) -> AccountMapping:
    """Compiled against the *port*, so the adapter is judged by the protocol it implements."""
    return provider.mapping_for(client)


# --- the value object ----------------------------------------------------------------


def test_a_mapping_carries_the_version_it_resolves_under() -> None:
    """§8:194: the mapping version is part of the posting's identity, so it is mandatory."""
    mapping = AccountMapping(mapping_version=_VERSION, accounts={AccountRole.INGRESOS: "401-001"})
    assert mapping.mapping_version == _VERSION
    assert mapping.resolve(AccountRole.INGRESOS) == "401-001"


@pytest.mark.parametrize("version", ["", "   "])
def test_a_mapping_refuses_to_exist_without_a_version(version: str) -> None:
    """An unversioned mapping could not say under which decision a line was booked."""
    with pytest.raises(ValueError, match="needs its version"):
        AccountMapping(mapping_version=version, accounts={})


def test_a_mapping_refuses_an_empty_account() -> None:
    """A blank account is not a resolution; it would post to nothing under a resolved role."""
    with pytest.raises(ValueError, match="empty account"):
        AccountMapping(mapping_version=_VERSION, accounts={AccountRole.INGRESOS: "  "})


def test_an_unmapped_role_resolves_to_nothing_never_to_a_default() -> None:
    """§8a:207: the only alternative to an account is "no account", which the caller flags."""
    mapping = AccountMapping(mapping_version=_VERSION, accounts={AccountRole.INGRESOS: "401-001"})
    assert mapping.resolve(AccountRole.CLEARING) is None
    assert mapping.unresolved([AccountRole.CLEARING, AccountRole.INGRESOS]) == (
        AccountRole.CLEARING,
    )


def test_the_unresolved_roles_keep_first_seen_order_and_do_not_repeat() -> None:
    """The reason a human reads lists the legs in the order the rule proposed them."""
    mapping = AccountMapping(mapping_version=_VERSION, accounts={AccountRole.INGRESOS: "401-001"})
    roles = [AccountRole.CLEARING, AccountRole.CLIENTES, AccountRole.CLEARING]
    assert mapping.unresolved(roles) == (AccountRole.CLEARING, AccountRole.CLIENTES)


# --- the YAML adapter ----------------------------------------------------------------


def test_the_fixture_mapping_loads_with_its_version_and_roles() -> None:
    """The client's YAML is the versioned artifact §8a:207 asks for."""
    mapping = _resolved(_provider(), _CLIENT)
    assert mapping.mapping_version == _VERSION
    assert mapping.resolve(AccountRole.INGRESOS) == "401-001"
    assert mapping.unresolved(_EMITTER_ROLES) == ()


def test_the_fixture_names_the_roles_a_client_needs_and_nothing_invented() -> None:
    """A typo in the YAML would resolve to nothing: every key must be a real role."""
    mapping = _resolved(_provider(), _CLIENT)
    assert set(mapping.accounts) == _EMITTER_ROLES


def test_a_client_without_a_mapping_file_is_a_configuration_failure(tmp_path: Path) -> None:
    """Failing loudly beats booking the whole client's books through a guessed chart."""
    with pytest.raises(MappingNotConfigured, match="no account mapping"):
        _provider(tmp_path).mapping_for(_CLIENT)


def test_a_mapping_without_a_version_is_refused(tmp_path: Path) -> None:
    """§8:194: the version cannot be defaulted — it is what the posting is keyed by."""
    root = _write(tmp_path, "accounts:\n  ingresos: '401-001'\n")
    with pytest.raises(InvalidMapping, match="mapping_version"):
        _provider(root).mapping_for(_CLIENT)


def test_a_role_outside_the_vocabulary_is_refused(tmp_path: Path) -> None:
    """A mistyped role must not silently become an unmapped one (§8a:207)."""
    root = _write(tmp_path, "mapping_version: '2026.01'\naccounts:\n  ingreso: '401-001'\n")
    with pytest.raises(InvalidMapping, match="not an AccountRole"):
        _provider(root).mapping_for(_CLIENT)


def test_an_empty_account_is_refused(tmp_path: Path) -> None:
    root = _write(tmp_path, "mapping_version: '2026.01'\naccounts:\n  ingresos: ''\n")
    with pytest.raises(InvalidMapping, match="empty account"):
        _provider(root).mapping_for(_CLIENT)


def test_a_mapping_without_accounts_is_refused(tmp_path: Path) -> None:
    root = _write(tmp_path, "mapping_version: '2026.01'\n")
    with pytest.raises(InvalidMapping, match="'accounts'"):
        _provider(root).mapping_for(_CLIENT)


def test_a_document_that_is_not_a_mapping_is_refused(tmp_path: Path) -> None:
    root = _write(tmp_path, "- ingresos\n- clientes\n")
    with pytest.raises(InvalidMapping, match="expected a mapping"):
        _provider(root).mapping_for(_CLIENT)


def test_unreadable_yaml_is_refused(tmp_path: Path) -> None:
    """A half-written config is an error a human must fix, never a partial mapping."""
    root = _write(tmp_path, "mapping_version: '2026.01'\naccounts: [unclosed\n")
    with pytest.raises(InvalidMapping, match="not readable as YAML"):
        _provider(root).mapping_for(_CLIENT)


def test_the_mapping_is_the_clients_own_file(tmp_path: Path) -> None:
    """§8:194: two clients' books never resolve a role through one another's accounts."""
    _write(tmp_path, "mapping_version: '2026.01'\naccounts:\n  ingresos: '401-001'\n")
    _write(
        tmp_path,
        "mapping_version: '2026.02'\naccounts:\n  ingresos: '499-999'\n",
        name=f"{_OTHER_CLIENT.value}.yaml",
    )
    provider = _provider(tmp_path)
    assert provider.mapping_for(_CLIENT).resolve(AccountRole.INGRESOS) == "401-001"
    assert provider.mapping_for(_OTHER_CLIENT).resolve(AccountRole.INGRESOS) == "499-999"
