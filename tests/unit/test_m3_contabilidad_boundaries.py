"""M3 W0: boundary guards for the accounting engine (``contabilidad/``).

Step 0 ships the package as a skeleton — `AccountRole`, the journal model, the
`PostingEligibilityValidator` and the rules arrive in the steps that follow. These
guards encode what §4/§8/§8a/§12 require of the engine *before* there is behaviour
to protect:

- **No adapter, no library.** The engine consumes typed domain models; it never
  imports `infrastructure`, `sqlite3`, `lxml`, `satcfdi`, `openpyxl`, `requests`
  or `cryptography` (§4's dependency rule, §8).
- **Domain and application only.** `fiscal/` is a *sibling*, not a dependency: the
  document a rule reads is `domain.model.fiscal_document.FiscalDocument` (§4).
- **Roles, never account numbers.** Rules target `AccountRole`s; turning a role
  into an account is the versioned mapping YAML behind `MappingProvider` (§8a), so
  no module here may name an account.
- **`Decimal` amounts.** §12: no `float` in fiscal/accounting amounts.

The package is a skeleton today, so every guard first proves it had something to
scan — a guard that silently scans nothing must fail, not pass.

The two checkers are behaviours in their own right, so they are unit-tested
directly (forbidden import detected, sibling package detected, relative import
resolved, float usage found but docstrings ignored) instead of being trusted.

The same §4 dependency rule is checked one layer out, over ``application/``
(A25): the application layer may depend on the *engine*, the domain and its own
ports, but never on an adapter or a library. The engine's own guard cannot see
that, because it only scans ``contabilidad/``.
"""

import ast
import sys
from pathlib import Path

from sat_descarga_masiva import application as application_package
from sat_descarga_masiva import contabilidad as contabilidad_package
from sat_descarga_masiva.application.ports import persistence as persistence_port

_CONTABILIDAD_ROOT = Path(contabilidad_package.__file__ or "").parent
_CONTABILIDAD_PREFIX = "sat_descarga_masiva.contabilidad"
_APPLICATION_ROOT = Path(application_package.__file__ or "").parent
_APPLICATION_PREFIX = "sat_descarga_masiva.application"

#: What the engine may import: its own modules, plus domain and application (§4).
_ALLOWED_ROOTS = (
    _CONTABILIDAD_PREFIX,
    "sat_descarga_masiva.domain",
    "sat_descarga_masiva.application",
)

#: What ``application/`` may import: itself, the domain, and the engine it orchestrates. The
#: sibling ``fiscal/`` package is legitimate here (M2.8 composes it), which is exactly why the
#: application allow-list is stated separately rather than reused.
_APPLICATION_ALLOWED_ROOTS = (
    _APPLICATION_PREFIX,
    "sat_descarga_masiva.contabilidad",
    "sat_descarga_masiva.domain",
    "sat_descarga_masiva.fiscal",
)

#: §4/§8's named ban: the adapters and the XML/DB/spreadsheet/HTTP libraries.
_FORBIDDEN_ROOTS = (
    "sat_descarga_masiva.infrastructure",
    "sqlite3",
    "lxml",
    "satcfdi",
    "openpyxl",
    "requests",
    "cryptography",
)

#: §8a: account numbers live in the mapping YAML, never in the engine.
_ACCOUNT_NUMBER_TOKENS = (
    "account_number",
    "account_code",
    "codigo_cuenta",
    "cuenta_contable",
    "no_cuenta",
)


def _package_sources() -> dict[str, str]:
    """Every module in `contabilidad/`, keyed by its path relative to the package."""
    return {
        str(path.relative_to(_CONTABILIDAD_ROOT)): path.read_text(encoding="utf-8")
        for path in sorted(_CONTABILIDAD_ROOT.rglob("*.py"))
    }


def _application_sources() -> dict[str, str]:
    """Every module in `application/`, keyed by its path relative to the package."""
    return {
        str(path.relative_to(_APPLICATION_ROOT)): path.read_text(encoding="utf-8")
        for path in sorted(_APPLICATION_ROOT.rglob("*.py"))
    }


def _package_of(prefix: str, relative_path: str) -> str:
    """The package a module at ``relative_path`` sits in, so relative imports resolve.

    `use_cases/execute_accounting.py` is ``sat_descarga_masiva.application.use_cases``: a
    relative import inside it must be judged against *that* package, not against the root.
    """
    parent = Path(relative_path).parent.as_posix()
    return prefix if parent == "." else f"{prefix}.{parent.replace('/', '.')}"


def _imported_modules(
    source: str, *, filename: str = "<memory>", prefix: str = _CONTABILIDAD_PREFIX
) -> list[str]:
    """Every module `source` imports, named the way the boundary sees it.

    Relative imports are resolved against `prefix`, so `from .journal import X`
    is `sat_descarga_masiva.contabilidad.journal` and `from . import roles` is
    `...contabilidad.roles`; intra-package imports are therefore judged by the same
    allow-list as everything else. A symbol taken from the root package is judged as
    if it were imported from its own module (`from sat_descarga_masiva import
    fiscal` is `sat_descarga_masiva.fiscal`), so the root package is no loophole.
    """
    tree = ast.parse(source, filename)
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:  # relative: the package this module lives in
                if node.module:
                    modules.append(f"{prefix}.{node.module}")
                else:
                    modules.extend(f"{prefix}.{alias.name}" for alias in node.names)
            elif node.module == "sat_descarga_masiva":
                modules.extend(f"sat_descarga_masiva.{alias.name}" for alias in node.names)
            elif node.module is not None:
                modules.append(node.module)
        elif isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
    return modules


def _is_external(module: str, *, allowed_roots: tuple[str, ...] = _ALLOWED_ROOTS) -> bool:
    """True when `module` leaves this package's world: not its allow-list, not stdlib."""
    if module.startswith("sat_descarga_masiva"):
        return not module.startswith(allowed_roots)
    return module.split(".", maxsplit=1)[0] not in sys.stdlib_module_names


def _forbidden_modules(modules: list[str]) -> list[str]:
    """The §4/§8 blacklist: adapters and the XML/DB/spreadsheet/HTTP libraries."""
    return [module for module in modules if module.startswith(_FORBIDDEN_ROOTS)]


def _external_modules(
    modules: list[str], *, allowed_roots: tuple[str, ...] = _ALLOWED_ROOTS
) -> list[str]:
    """The allow-list side of §4: anything that is not domain, application or stdlib."""
    return [module for module in modules if _is_external(module, allowed_roots=allowed_roots)]


def _float_usages(source: str, *, filename: str = "<memory>") -> list[int]:
    """Lines using `float` as a name or a float literal — docstrings are not usages."""
    tree = ast.parse(source, filename)
    return sorted(
        node.lineno
        for node in ast.walk(tree)
        if (isinstance(node, ast.Name) and node.id == "float")
        or (isinstance(node, ast.Constant) and isinstance(node.value, float))
    )


def test_the_import_checker_reports_a_forbidden_library() -> None:
    """RED: the blacklist must actually fire, or every guard below is decoration."""
    modules = _imported_modules("import sqlite3\nfrom lxml import etree\nimport openpyxl\n")
    assert _forbidden_modules(modules) == ["sqlite3", "lxml", "openpyxl"]


def test_the_import_checker_accepts_a_pure_domain_module() -> None:
    """GREEN: the shape every real rule has — stdlib, `Domain` model, application port."""
    modules = _imported_modules(
        "from decimal import Decimal\n"
        "from sat_descarga_masiva.domain.policy.money import MoneyPolicy\n"
        "from sat_descarga_masiva.application.ports.accounting import MappingProvider\n"
    )
    assert sorted(modules) == [
        "decimal",
        "sat_descarga_masiva.application.ports.accounting",
        "sat_descarga_masiva.domain.policy.money",
    ]
    assert not _forbidden_modules(modules)
    assert not _external_modules(modules)


def test_the_import_checker_flags_the_sibling_fiscal_package() -> None:
    """`fiscal/` and `contabilidad/` are siblings (§4): the model, not the module."""
    modules = _imported_modules("from sat_descarga_masiva.fiscal.cfdi import RawCfdi\n")
    assert _external_modules(modules) == ["sat_descarga_masiva.fiscal.cfdi"]


def test_the_import_checker_resolves_relative_imports_inside_the_package() -> None:
    """An intra-package import is named, not waved through."""
    modules = _imported_modules("from .journal import JournalLine\nfrom . import roles\n")
    assert sorted(modules) == [
        "sat_descarga_masiva.contabilidad.journal",
        "sat_descarga_masiva.contabilidad.roles",
    ]
    assert not _external_modules(modules)


def test_the_import_checker_expands_root_package_members() -> None:
    """`from sat_descarga_masiva import fiscal` must not dodge the allow-list."""
    modules = _imported_modules("from sat_descarga_masiva import domain, fiscal\n")
    assert sorted(modules) == ["sat_descarga_masiva.domain", "sat_descarga_masiva.fiscal"]
    assert _external_modules(modules) == ["sat_descarga_masiva.fiscal"]


def test_the_float_scanner_reports_code_usage_but_not_the_docstring() -> None:
    """§12: the scan is about code, so prose may discuss amounts freely."""
    source = (
        '"""Amounts are never float here."""\nimport decimal\n\nSCALE = 1.5\nTOTAL = float("2")\n'
    )
    assert _float_usages(source) == [4, 5]


def test_the_contabilidad_package_has_modules_to_guard() -> None:
    """A guard that scans nothing must fail: step 0 ships the package itself."""
    assert "__init__.py" in _package_sources()
    assert Path(contabilidad_package.__file__ or "").name == "__init__.py"


def test_the_contabilidad_package_imports_nothing_from_infrastructure_or_libraries() -> None:
    """§4/§8: the engine consumes typed models — never an adapter or a library."""
    offenders = {
        name: violations
        for name, source in _package_sources().items()
        if (violations := _forbidden_modules(_imported_modules(source, filename=name)))
    }
    assert not offenders


def test_the_contabilidad_package_imports_only_domain_and_application() -> None:
    """The allow-list side of §4, which also bans the sibling `fiscal/` package."""
    offenders = {
        name: violations
        for name, source in _package_sources().items()
        if (violations := _external_modules(_imported_modules(source, filename=name)))
    }
    assert not offenders


def test_the_contabilidad_package_targets_roles_not_account_numbers() -> None:
    """§8a: a rule names an `AccountRole`; the mapping YAML owns the account."""
    offenders = {
        name: [token for token in _ACCOUNT_NUMBER_TOKENS if token in source.lower()]
        for name, source in _package_sources().items()
        if any(token in source.lower() for token in _ACCOUNT_NUMBER_TOKENS)
    }
    assert not offenders


def test_the_contabilidad_package_uses_no_float_amounts() -> None:
    """§12: amounts are `Decimal` through `MoneyPolicy`."""
    offenders = {
        name: lines
        for name, source in _package_sources().items()
        if (lines := _float_usages(source, filename=name))
    }
    assert not offenders


# --- the journal records, and the port that names them --------------------------------


def test_the_journal_records_a_store_writes_are_the_engines_own() -> None:
    """§4: the codec and the aggregate a store writes carry no adapter and no library.

    A SQLite adapter has to import `JournalEntryRecord` and §8:171's codec, and the engine
    has to be able to enforce the POSTED invariant without one — so the module owning both
    is judged by the same allow-list as the rest of `contabilidad/`: the standard library,
    the domain and the application, and nothing else.
    """
    modules = _imported_modules(_package_sources()["journal.py"], filename="journal.py")
    assert not _forbidden_modules(modules)
    assert not _external_modules(modules)
    assert sorted(module for module in modules if module.startswith("sat_descarga_masiva")) == [
        "sat_descarga_masiva.contabilidad.roles",
        "sat_descarga_masiva.domain.model.value_objects",
        "sat_descarga_masiva.domain.policy.money",
    ]


def test_the_journal_port_names_the_record_without_naming_an_adapter() -> None:
    """§4: the port speaks the engine's aggregate, so it needs no adapter to describe it."""
    modules = _imported_modules(
        Path(persistence_port.__file__ or "").read_text(encoding="utf-8"),
        filename="persistence.py",
    )
    assert "sat_descarga_masiva.contabilidad.journal" in modules  # the aggregate it stores
    assert not [
        module for module in modules if module.startswith("sat_descarga_masiva.infrastructure")
    ]


# --- A25: the same §4 rule, one layer out — the application package --------------------------


def test_the_relative_import_checker_resolves_inside_a_subpackage() -> None:
    """RED: a sub-package's relative import must name *its* package, or the guard is blind."""
    modules = _imported_modules(
        "from . import sibling\nfrom ..ports import accounting\n",
        filename="use_cases/execute_accounting.py",
        prefix=_package_of(_APPLICATION_PREFIX, "use_cases/execute_accounting.py"),
    )
    assert sorted(modules) == [
        "sat_descarga_masiva.application.use_cases.ports",
        "sat_descarga_masiva.application.use_cases.sibling",
    ]
    assert not _external_modules(modules, allowed_roots=_APPLICATION_ALLOWED_ROOTS)


def test_the_package_locator_keeps_a_top_level_module_at_the_root() -> None:
    """A module directly in `application/` has the package itself as its relative base."""
    assert _package_of(_APPLICATION_PREFIX, "__init__.py") == _APPLICATION_PREFIX
    assert (
        _package_of(_APPLICATION_PREFIX, "ports/accounting.py")
        == "sat_descarga_masiva.application.ports"
    )


def test_the_application_package_has_modules_to_guard() -> None:
    """A guard that scans nothing must fail: the application layer is where the stages live."""
    sources = _application_sources()
    assert "__init__.py" in sources
    assert "use_cases/execute_accounting.py" in sources  # the stage this guard exists for


def test_a25_the_application_package_imports_no_adapter_or_library() -> None:
    """A25 §4: the application layer orchestrates ports — it never reaches for an adapter."""
    offenders = {
        name: violations
        for name, source in _application_sources().items()
        if (
            violations := _forbidden_modules(
                _imported_modules(
                    source, filename=name, prefix=_package_of(_APPLICATION_PREFIX, name)
                )
            )
        )
    }
    assert not offenders


def test_a25_the_application_package_stays_inside_its_own_allow_list() -> None:
    """A25 §4: itself, the domain, the engine, the sibling `fiscal/` package — and stdlib."""
    offenders = {
        name: violations
        for name, source in _application_sources().items()
        if (
            violations := _external_modules(
                _imported_modules(
                    source, filename=name, prefix=_package_of(_APPLICATION_PREFIX, name)
                ),
                allowed_roots=_APPLICATION_ALLOWED_ROOTS,
            )
        )
    }
    assert not offenders


def test_a25_the_new_stage_depends_inward_only() -> None:
    """A25: the stage that writes the ledger reads the engine and its ports, nothing more."""
    source = _application_sources()["use_cases/execute_accounting.py"]
    modules = _imported_modules(
        source,
        filename="use_cases/execute_accounting.py",
        prefix=_package_of(_APPLICATION_PREFIX, "use_cases/execute_accounting.py"),
    )
    assert not _forbidden_modules(modules)
    assert not _external_modules(modules, allowed_roots=_APPLICATION_ALLOWED_ROOTS)
    assert sorted({module for module in modules if module.startswith("sat_descarga_masiva")}) == [
        "sat_descarga_masiva.application.ports.accounting",
        "sat_descarga_masiva.application.ports.persistence",
        "sat_descarga_masiva.application.ports.services",
        "sat_descarga_masiva.contabilidad.classification",
        "sat_descarga_masiva.contabilidad.journal",
        "sat_descarga_masiva.contabilidad.rules.contract",
        "sat_descarga_masiva.contabilidad.rules.posting",
        "sat_descarga_masiva.contabilidad.validator",
        "sat_descarga_masiva.domain.enums.comprobante",
        "sat_descarga_masiva.domain.errors",
        "sat_descarga_masiva.domain.model.fiscal_document",
        "sat_descarga_masiva.domain.model.perspective",
        "sat_descarga_masiva.domain.model.review",
        "sat_descarga_masiva.domain.model.value_objects",
        "sat_descarga_masiva.fiscal.projection",
    ]


def test_the_reposting_rule_module_reads_the_engine_and_the_domain_only() -> None:
    """§4/§8: a rule is *pure over its context*, which is a dependency claim, not a style one.

    `reposting.py` computes §8:192's REP rows, so it must be unable to do what the design forbids:
    it may not import `posting.py` (the engine's registry appends its rows, not the other way
    round), it may not reach for a store to learn whether an original was posted — that presence
    arrives as `PostingContext.posted_source_uuids` — and it may not name an account, since §8a:207
    keeps every account number in the mapping. Its imports are therefore exactly: the engine's own
    contract, journal vocabulary and roles, plus the domain model it reads.
    """
    modules = _imported_modules(_package_sources()["rules/reposting.py"], filename="reposting.py")
    assert not _forbidden_modules(modules)
    assert not _external_modules(modules)
    assert sorted(module for module in modules if module.startswith("sat_descarga_masiva")) == [
        "sat_descarga_masiva.contabilidad.journal",
        "sat_descarga_masiva.contabilidad.roles",
        "sat_descarga_masiva.contabilidad.rules.contract",
        "sat_descarga_masiva.domain.enums.comprobante",
        "sat_descarga_masiva.domain.model.fiscal_document",
        "sat_descarga_masiva.domain.model.perspective",
        "sat_descarga_masiva.domain.model.review",
        "sat_descarga_masiva.domain.model.value_objects",
        "sat_descarga_masiva.domain.policy.money",
    ]
    assert "sat_descarga_masiva.contabilidad.rules.posting" not in modules
