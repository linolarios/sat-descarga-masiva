"""SatcfdiFiscalParser -- deserialize CFDI XML into RawCfd via satcfdi (M2.3).

Boundary: satcfdi types never escape this module; results map to the neutral RawCfd.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from satcfdi.cfdi import CFDI  # type: ignore[import-untyped]

from sat_descarga_masiva.domain.errors import XmlParseError
from sat_descarga_masiva.domain.model.raw_cfd import (
    RawCfd,
    RawConcepto,
    RawImpuestos,
    RawRetencion,
    RawTraslado,
)

Node = Mapping[str, object]


class SatcfdiFiscalParser:
    """Implements application.ports.fiscal_parser.FiscalXmlParser over satcfdi."""

    def parse(self, xml_bytes: bytes, source_hash: str) -> RawCfd:
        root = cast(Node, CFDI.from_string(xml_bytes))
        conceptos_value = root.get("Conceptos")
        concepto_nodes: list[object] = (
            list(conceptos_value) if isinstance(conceptos_value, list) else [conceptos_value]
        )
        return RawCfd(
            tipo=_required(root, "TipoDeComprobante"),
            version=_required(root, "Version"),
            moneda=_required(root, "Moneda"),
            tipo_cambio=_text(root.get("TipoCambio")),
            emisor_rfc=_required(_map(root.get("Emisor"), "Emisor"), "Rfc"),
            receptor_rfc=_required(_map(root.get("Receptor"), "Receptor"), "Rfc"),
            conceptos=tuple(_concepto(c) for c in concepto_nodes),
            impuestos=_impuestos(root.get("Impuestos")),
            total=_required(root, "Total"),
        )


def _text(value: object | None) -> str | None:
    """Raw string value; unwraps a satcfdi Code to its catalog code."""
    if value is None:
        return None
    code = getattr(value, "code", None)
    return str(code if code is not None else value)


def _map(value: object, what: str) -> Node:
    if not isinstance(value, Mapping):
        raise XmlParseError(f"CFDI node {what!r} is not an element")
    return value


def _required(node: Node, name: str) -> str:
    value = node.get(name)
    if value is None:
        raise XmlParseError(f"CFDI missing required attribute {name!r}")
    return _text(value) or ""


def _concepto(node: object) -> RawConcepto:
    m = _map(node, "Concepto")
    return RawConcepto(
        clave_prod_serv=_text(m.get("ClaveProdServ")) or "",
        cantidad=_text(m.get("Cantidad")) or "0",
        valor_unitario=_text(m.get("ValorUnitario")) or "0",
        importe=_text(m.get("Importe")) or "0",
        descuento=_text(m.get("Descuento")),
    )


def _impuestos(node: object) -> RawImpuestos:
    if not isinstance(node, Mapping):
        return RawImpuestos()
    return RawImpuestos(
        traslados=tuple(_traslado(v) for v in _children(node.get("Traslados"))),
        retenciones=tuple(_retencion(v) for v in _children(node.get("Retenciones"))),
        total_traslados=_text(node.get("TotalImpuestosTrasladados")),
        total_retenciones=_text(node.get("TotalImpuestosRetenidos")),
    )


def _children(node: object) -> list[object]:
    if node is None:
        return []
    if isinstance(node, list):
        return list(node)
    if isinstance(node, Mapping):
        return list(node.values())
    return [node]


def _traslado(node: object) -> RawTraslado:
    m = _map(node, "Traslado")
    return RawTraslado(
        impuesto=_text(m.get("Impuesto")) or "",
        tipo_factor=_text(m.get("TipoFactor")) or "",
        tasa_o_cuota=_text(m.get("TasaOCuota")),
        importe=_text(m.get("Importe")),
    )


def _retencion(node: object) -> RawRetencion:
    m = _map(node, "Retencion")
    return RawRetencion(
        impuesto=_text(m.get("Impuesto")) or "",
        tasa_o_cuota=_text(m.get("TasaOCuota")) or "",
        importe=_text(m.get("Importe")) or "",
    )
