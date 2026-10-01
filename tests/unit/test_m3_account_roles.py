"""M3 step 1: `AccountRole` is exactly §8a's M3 set — no more, no less (§8).

The role vocabulary is the seam between a rule and the chart of accounts: a rule
can name a role, only the mapping YAML can name an account. So these tests pin
the *set*, which is what makes "a rule never invents an account" checkable.
"""

from sat_descarga_masiva.contabilidad.roles import AccountRole

#: §8a, in order: the complete M3 role set.
M3_ROLES = (
    "INGRESOS",
    "CLIENTES",
    "PROVEEDORES",
    "CLEARING",
    "IVA_TRASLADADO_COBRADO",
    "IVA_TRASLADADO_NO_COBRADO",
    "IVA_ACRED_PAGADO",
    "IVA_ACRED_PENDIENTE",
    "GASTO",
    "INVENTARIO",
    "ACTIVO_FIJO",
    "RETENCION_IVA_POR_COBRAR",
    "RETENCION_ISR_POR_COBRAR",
    "RETENCION_IVA_POR_PAGAR",
    "RETENCION_ISR_POR_PAGAR",
    "PAGOS_NO_APLICADOS",
    "IEPS_TRASLADADO",
    "IEPS_ACREDITABLE",
    "DIFERENCIA_CAMBIARIA_GANANCIA",
    "DIFERENCIA_CAMBIARIA_PERDIDA",
)


def test_the_role_set_is_exactly_the_m3_set_from_8a() -> None:
    """A role added without a §8a ruling fails here, not in a posting rule."""
    assert tuple(role.name for role in AccountRole) == M3_ROLES


def test_role_values_are_semantic_names_that_cannot_look_like_an_account() -> None:
    """§8: roles are words. If a value could be an account number, it is one."""
    for role in AccountRole:
        assert role.value == role.name.lower()
        assert role.value.replace("_", "").isalpha()


def test_roles_are_strings_so_the_mapping_yaml_keys_on_the_same_words() -> None:
    """A persisted line or a mapping key must not need the enum to be readable."""
    assert isinstance(AccountRole.CLIENTES, str)
    assert AccountRole.CLIENTES == "clientes"


def test_iva_reclassification_rules_add_no_role() -> None:
    """§8a: 4.5a/4.5b **reclassify** the IVA roles — so the four below are the whole set."""
    assert {role.name for role in AccountRole if role.name.startswith("IVA_")} == {
        "IVA_TRASLADADO_COBRADO",
        "IVA_TRASLADADO_NO_COBRADO",
        "IVA_ACRED_PAGADO",
        "IVA_ACRED_PENDIENTE",
    }


def test_retentions_have_both_directions_and_stay_explicit() -> None:
    """§8: retentions are explicit legs — receivable when emitido, payable when recibido."""
    assert {role.name for role in AccountRole if role.name.startswith("RETENCION_")} == {
        "RETENCION_IVA_POR_COBRAR",
        "RETENCION_ISR_POR_COBRAR",
        "RETENCION_IVA_POR_PAGAR",
        "RETENCION_ISR_POR_PAGAR",
    }


def test_ieps_and_iva_never_share_a_role() -> None:
    """§8: IEPS stays separate from IVA — the roles that exist prove it."""
    assert {role.name for role in AccountRole if role.name.startswith("IEPS_")} == {
        "IEPS_TRASLADADO",
        "IEPS_ACREDITABLE",
    }


def test_the_roles_declared_out_of_m3_scope_do_not_exist() -> None:
    """§8a: absent means unpostable — a rule cannot target a role that is not here."""
    out_of_scope_tokens = (
        "BANCO",
        "SUPPORTED_BY_BANK",
        "COSTO",
        "DEPRECIACION",
        "DESCUENTO",
        "NOMINA",
        "SUELDO",
    )
    assert not [
        role.name
        for role in AccountRole
        if any(token in role.name for token in out_of_scope_tokens)
    ]


def test_fx_differences_book_to_explicit_roles() -> None:
    """§8a ⚠: never absorbed into clearing/AR/AP — so both directions exist, named."""
    assert {role.name for role in AccountRole if role.name.startswith("DIFERENCIA_")} == {
        "DIFERENCIA_CAMBIARIA_GANANCIA",
        "DIFERENCIA_CAMBIARIA_PERDIDA",
    }
