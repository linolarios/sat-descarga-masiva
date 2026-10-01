"""`AccountRole` — the only thing a rule may name (§8, §8a).

A rule targets a **semantic role**, never a raw chart-of-account number: the
concrete number behind each role is configuration, resolved from the versioned
mapping YAML behind the ``MappingProvider`` port. So this module is pure
vocabulary: a rule that cannot type "the receivable account" cannot invent one
either, and a role the mapping does not resolve is a review flag
(``unmapped_account``), never a default.

This is §8a's M3 set, verbatim and complete:

- 4.5a/4.5b **reclassify** the existing IVA roles — they add no role, so the four
  IVA roles below are the whole IVA vocabulary;
- the ⚠-gated treatments exist as roles so a rule can *name* what the contador's
  ruling will need (``IEPS_ACREDITABLE``, the two ``DIFERENCIA_CAMBIARIA_*``);
  until that ruling lands, the rule that would use them stays review-gated and
  posts nothing;
- bank / ``SUPPORTED_BY_BANK``, cost of sales, depreciation, contra-revenue and
  payroll roles are **out of M3**: a role that does not exist cannot be posted to.

Step 1 is vocabulary only — no rule, no validator, no mapping.
"""

from __future__ import annotations

from enum import StrEnum


class AccountRole(StrEnum):
    """Semantic accounting target of a journal line (§8a).

    Values are the lowercased names so the mapping YAML keys on the same words the
    rules use, and a persisted line stays readable without the enum.
    """

    INGRESOS = "ingresos"
    CLIENTES = "clientes"
    PROVEEDORES = "proveedores"
    CLEARING = "clearing"
    IVA_TRASLADADO_COBRADO = "iva_trasladado_cobrado"
    IVA_TRASLADADO_NO_COBRADO = "iva_trasladado_no_cobrado"
    IVA_ACRED_PAGADO = "iva_acred_pagado"
    IVA_ACRED_PENDIENTE = "iva_acred_pendiente"
    GASTO = "gasto"
    INVENTARIO = "inventario"
    ACTIVO_FIJO = "activo_fijo"
    RETENCION_IVA_POR_COBRAR = "retencion_iva_por_cobrar"
    RETENCION_ISR_POR_COBRAR = "retencion_isr_por_cobrar"
    RETENCION_IVA_POR_PAGAR = "retencion_iva_por_pagar"
    RETENCION_ISR_POR_PAGAR = "retencion_isr_por_pagar"
    PAGOS_NO_APLICADOS = "pagos_no_aplicados"
    IEPS_TRASLADADO = "ieps_trasladado"
    IEPS_ACREDITABLE = "ieps_acreditable"
    DIFERENCIA_CAMBIARIA_GANANCIA = "diferencia_cambiaria_ganancia"
    DIFERENCIA_CAMBIARIA_PERDIDA = "diferencia_cambiaria_perdida"
