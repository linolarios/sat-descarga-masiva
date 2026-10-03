"""``YamlMappingProvider`` — the versioned chart of accounts, one YAML file per client (§8a:207).

The file layout this adapter reads, and the shape proposed for approval:

    <root>/<RFC>.yaml
    ---
    mapping_version: "2026.01"     # mandatory: §8:194 keys every posting by it
    accounts:                      # AccountRole value -> this client's account
      ingresos: "401-01"
      clientes: "105-01"
      clearing: "102-99"
      iva_trasladado_cobrado: "208-01"
      iva_trasladado_no_cobrado: "209-01"
    classification:                # optional: ClaveProdServ -> AccountingCategory (§8a:207)
      "84111506": gasto            # a received purchase has no role in the document; the
      "53131600": inventario       # client's chart says which role its products classify to
      "43211500": activo_fijo

Deliberate boundaries:

- a role the file **omits** is not an error — it is exactly the ``UNMAPPED_ACCOUNT`` case the
  validator flags (§8a:207's "never a default"); likewise a ClaveProdServ the
  ``classification`` block omits classifies nothing, which the receiving rules review;
- the ``classification`` block itself is optional: a client whose books never receive a
  purchase carries none. An empty block is the same as an absent one;
- everything else is a **configuration failure** and raises: no file for the client, no
  ``mapping_version``, a role name outside ``AccountRole`` or a category outside
  ``AccountingCategory`` (a typo must not vanish), an empty account, malformed YAML. Failing
  loudly is the point: a guessed account would be a silently wrong ledger, which is the one
  outcome §8a:207 exists to prevent;
- the file is read on demand and never cached here. Caching is a run-level decision (the
  caller resolves once per client per run), not a property of the port.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import yaml  # type: ignore[import-untyped]

from sat_descarga_masiva.application.ports.accounting import AccountMapping
from sat_descarga_masiva.contabilidad.classification import AccountingCategory
from sat_descarga_masiva.contabilidad.roles import AccountRole
from sat_descarga_masiva.domain.errors import InvalidMapping, MappingNotConfigured
from sat_descarga_masiva.domain.model.value_objects import Rfc

_MAPPING_SUFFIX = ".yaml"


class YamlMappingProvider:
    """Implements `application.ports.accounting.MappingProvider` over a directory of YAML files.

    ``root`` holds one file per managed client, named after the client's RFC, so a client's
    chart of accounts is a versioned, reviewable artifact and two clients can never resolve the
    same role through one another's accounts.
    """

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root)

    def mapping_for(self, client_rfc: Rfc) -> AccountMapping:
        """The client's current mapping, read from ``<root>/<RFC>.yaml`` (§8a:207)."""
        path = self._root / f"{client_rfc.value}{_MAPPING_SUFFIX}"
        if not path.is_file():
            raise MappingNotConfigured(
                f"no account mapping for {client_rfc.value}: expected {path}"
                " — accounting cannot start without the client's chart of accounts (§8a:207)"
            )
        return _read_mapping(path)


def _read_mapping(path: Path) -> AccountMapping:
    """Parse one mapping file, or raise `InvalidMapping` naming what is wrong with it."""
    try:
        raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise InvalidMapping(f"{path.name}: not readable as YAML: {error}") from error
    if not isinstance(raw, Mapping):
        raise InvalidMapping(
            f"{path.name}: expected a mapping with 'mapping_version' and 'accounts'"
        )

    version = raw.get("mapping_version")
    if not isinstance(version, str) or not version.strip():
        raise InvalidMapping(
            f"{path.name}: 'mapping_version' is mandatory: §8:194 keys every posting by it"
        )

    accounts_node = raw.get("accounts")
    if not isinstance(accounts_node, Mapping):
        raise InvalidMapping(f"{path.name}: 'accounts' must map AccountRole names to accounts")

    accounts: dict[AccountRole, str] = {}
    for name, account in accounts_node.items():
        if not isinstance(name, str) or not isinstance(account, str):
            raise InvalidMapping(f"{path.name}: every entry must be a role name and an account")
        if not account.strip():
            raise InvalidMapping(f"{path.name}: {name}: an empty account is not a resolution")
        try:
            role = AccountRole(name)
        except ValueError as error:
            raise InvalidMapping(
                f"{path.name}: {name!r} is not an AccountRole — a mistyped role must not"
                " silently become an unmapped one"
            ) from error
        accounts[role] = account.strip()

    return AccountMapping(
        mapping_version=version.strip(),
        accounts=accounts,
        classification=_read_classification(path, raw),
    )


def _read_classification(path: Path, raw: Mapping[str, object]) -> dict[str, AccountingCategory]:
    """Parse the optional ``classification`` block, or raise `InvalidMapping` naming the fault."""
    node = raw.get("classification", {})
    if not isinstance(node, Mapping):
        raise InvalidMapping(
            f"{path.name}: 'classification' must map ClaveProdServ codes to AccountingCategory"
            " names"
        )
    classification: dict[str, AccountingCategory] = {}
    for clave, name in node.items():
        if not isinstance(clave, str) or not clave.strip() or not isinstance(name, str):
            raise InvalidMapping(
                f"{path.name}: every classification entry must be a ClaveProdServ code and a"
                " category name"
            )
        try:
            category = AccountingCategory(name)
        except ValueError as error:
            raise InvalidMapping(
                f"{path.name}: {name!r} is not an AccountingCategory — a mistyped category must"
                " not silently leave a product unclassified"
            ) from error
        classification[clave.strip()] = category
    return classification
