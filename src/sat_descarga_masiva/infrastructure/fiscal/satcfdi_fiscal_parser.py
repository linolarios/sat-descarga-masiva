"""SatcfdiFiscalParser -- deserialize CFDI XML into RawCfd via satcfdi (M2.3, M2.3b).

Boundary: satcfdi types never escape this module; results map to the neutral RawCfd.
M2.3b additionally preserves the UUID (TFD), CfdiRelacionados and Pagos 2.0 structures
without interpreting them.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import cast

from satcfdi.cfdi import CFDI  # type: ignore[import-untyped]

from sat_descarga_masiva.domain.errors import XmlParseError
from sat_descarga_masiva.domain.model.raw_cfd import (
    RawCfd,
    RawCfdiRelacionados,
    RawConcepto,
    RawDoctoRelacionado,
    RawImpuestos,
    RawImpuestosDR,
    RawImpuestosP,
    RawPago,
    RawPagos20,
    RawRetencion,
    RawRetencionDR,
    RawRetencionP,
    RawTotalesPagos,
    RawTraslado,
    RawTrasladoDR,
    RawTrasladoP,
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
        receptor = _map(root.get("Receptor"), "Receptor")
        return RawCfd(
            tipo=_required(root, "TipoDeComprobante"),
            version=_required(root, "Version"),
            moneda=_required(root, "Moneda"),
            tipo_cambio=_text(root.get("TipoCambio")),
            emisor_rfc=_required(_map(root.get("Emisor"), "Emisor"), "Rfc"),
            receptor_rfc=_required(receptor, "Rfc"),
            conceptos=tuple(_concepto(c) for c in concepto_nodes),
            impuestos=_impuestos(root.get("Impuestos")),
            total=_required(root, "Total"),
            uuid=_uuid(root),
            subtotal=_text(root.get("SubTotal")),
            descuento=_text(root.get("Descuento")),
            forma_pago=_text(root.get("FormaPago")),
            metodo_pago=_text(root.get("MetodoPago")),
            regimen_fiscal_receptor=_text(receptor.get("RegimenFiscalReceptor")),
            cfdi_relacionados=_cfdi_relacionados(root),
            pagos=_pagos(root),
        )


def _text(value: object | None) -> str | None:
    """Raw string value; unwraps a satcfdi Code to its catalog code."""
    if value is None:
        return None
    code = getattr(value, "code", None)
    return str(code if code is not None else value)


def _date(value: object | None) -> str:
    """ISO-8601 text for a satcfdi datetime.

    str(datetime) is space-separated, which is not the SAT source form; the source
    attribute is aaaa-mm-ddThh:mm:ss, so isoformat() is the faithful conversion.
    """
    if isinstance(value, datetime):
        return value.isoformat()
    return _text(value) or ""


def _map(value: object, what: str) -> Node:
    if not isinstance(value, Mapping):
        raise XmlParseError(f"CFDI node {what!r} is not an element")
    return value


def _required(node: Node, name: str) -> str:
    value = node.get(name)
    if value is None:
        raise XmlParseError(f"CFDI missing required attribute {name!r}")
    return _text(value) or ""


def _as_list(value: object | None) -> list[object]:
    """A repeatable node normalized to a list (list in 4.0, scalar in 3.3).

    Unlike `_grouped_children`, a Mapping here is ONE element, not a container of
    elements: exploding it would yield the element attributes as siblings.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    return [value]


def _grouped_children(value: object | None) -> list[object]:
    """Entries of a satcfdi grouped tax collection (TrasladosDR, RetencionesP, ...).

    satcfdi groups repeated tax entries into a mapping keyed by impuesto|factor|tasa
    whose values are the entries. A mapping whose values are NOT mappings is a single
    ungrouped entry, not a container.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    if isinstance(value, Mapping):
        entries: list[object] = [v for v in value.values() if isinstance(v, Mapping)]
        if entries and len(entries) == len(value):
            return entries
        return [value]
    return [value]


def _uuid(root: Node) -> str | None:
    """The TFD UUID, or None when the input carries no Complemento/TimbreFiscalDigital.

    Never fabricated: not generated, not derived from the filename or the hash.
    """
    complemento = root.get("Complemento")
    if not isinstance(complemento, Mapping):
        return None
    timbre = complemento.get("TimbreFiscalDigital")
    if not isinstance(timbre, Mapping):
        return None
    return _text(timbre.get("UUID"))


def _cfdi_relacionados(root: Node) -> tuple[RawCfdiRelacionados, ...]:
    return tuple(_relacion(n) for n in _as_list(root.get("CfdiRelacionados")))


def _relacion(node: object) -> RawCfdiRelacionados:
    m = _map(node, "CfdiRelacionados")
    return RawCfdiRelacionados(
        tipo_relacion=_text(m.get("TipoRelacion")) or "",
        uuids=tuple(_relacion_uuid(c) for c in _as_list(m.get("CfdiRelacionado"))),
    )


def _relacion_uuid(node: object) -> str:
    """satcfdi yields the related UUID as text; tolerate a nested element."""
    if isinstance(node, Mapping):
        return _text(node.get("UUID")) or ""
    return _text(node) or ""


def _pagos(root: Node) -> RawPagos20 | None:
    """The Pagos 2.0 complemento; None for any non-pago comprobante."""
    complemento = root.get("Complemento")
    if not isinstance(complemento, Mapping):
        return None
    pagos = complemento.get("Pagos")
    if not isinstance(pagos, Mapping):
        return None
    return RawPagos20(
        version=_text(pagos.get("Version")) or "",
        pagos=tuple(_pago(p) for p in _as_list(pagos.get("Pago"))),
        totales=_totales_pagos(pagos.get("Totales")),
    )


def _pago(node: object) -> RawPago:
    m = _map(node, "Pago")
    return RawPago(
        fecha_pago=_date(m.get("FechaPago")),
        forma_de_pago_p=_text(m.get("FormaDePagoP")) or "",
        moneda_p=_text(m.get("MonedaP")) or "",
        monto=_text(m.get("Monto")) or "",
        tipo_cambio_p=_text(m.get("TipoCambioP")),
        num_operacion=_text(m.get("NumOperacion")),
        doctos_relacionados=tuple(
            _docto_relacionado(d) for d in _as_list(m.get("DoctoRelacionado"))
        ),
        impuestos_p=_impuestos_p(m.get("ImpuestosP")),
    )


def _totales_pagos(node: object | None) -> RawTotalesPagos | None:
    if not isinstance(node, Mapping):
        return None
    return RawTotalesPagos(monto_total_pagos=_text(node.get("MontoTotalPagos")))


def _docto_relacionado(node: object) -> RawDoctoRelacionado:
    m = _map(node, "DoctoRelacionado")
    return RawDoctoRelacionado(
        id_documento=_text(m.get("IdDocumento")) or "",
        moneda_dr=_text(m.get("MonedaDR")) or "",
        num_parcialidad=_text(m.get("NumParcialidad")) or "",
        imp_saldo_ant=_text(m.get("ImpSaldoAnt")) or "",
        imp_pagado=_text(m.get("ImpPagado")) or "",
        objeto_imp_dr=_text(m.get("ObjetoImpDR")) or "",
        imp_saldo_insoluto=_text(m.get("ImpSaldoInsoluto")),
        equivalencia_dr=_text(m.get("EquivalenciaDR")),
        serie=_text(m.get("Serie")),
        folio=_text(m.get("Folio")),
        impuestos_dr=_impuestos_dr(m.get("ImpuestosDR")),
    )


def _impuestos_dr(node: object | None) -> RawImpuestosDR | None:
    if not isinstance(node, Mapping):
        return None
    return RawImpuestosDR(
        traslados_dr=tuple(_traslado_dr(v) for v in _grouped_children(node.get("TrasladosDR"))),
        retenciones_dr=tuple(
            _retencion_dr(v) for v in _grouped_children(node.get("RetencionesDR"))
        ),
    )


def _traslado_dr(node: object) -> RawTrasladoDR:
    m = _map(node, "TrasladoDR")
    return RawTrasladoDR(
        base_dr=_text(m.get("BaseDR")) or "",
        impuesto_dr=_text(m.get("ImpuestoDR")) or "",
        tipo_factor_dr=_text(m.get("TipoFactorDR")) or "",
        tasa_o_cuota_dr=_text(m.get("TasaOCuotaDR")),
        importe_dr=_text(m.get("ImporteDR")),
    )


def _retencion_dr(node: object) -> RawRetencionDR:
    m = _map(node, "RetencionDR")
    return RawRetencionDR(
        base_dr=_text(m.get("BaseDR")) or "",
        impuesto_dr=_text(m.get("ImpuestoDR")) or "",
        tipo_factor_dr=_text(m.get("TipoFactorDR")) or "",
        tasa_o_cuota_dr=_text(m.get("TasaOCuotaDR")) or "",
        importe_dr=_text(m.get("ImporteDR")) or "",
    )


def _impuestos_p(node: object | None) -> RawImpuestosP | None:
    if not isinstance(node, Mapping):
        return None
    return RawImpuestosP(
        traslados_p=tuple(_traslado_p(v) for v in _grouped_children(node.get("TrasladosP"))),
        retenciones_p=tuple(_retencion_p(v) for v in _grouped_children(node.get("RetencionesP"))),
    )


def _traslado_p(node: object) -> RawTrasladoP:
    m = _map(node, "TrasladoP")
    return RawTrasladoP(
        base_p=_text(m.get("BaseP")) or "",
        impuesto_p=_text(m.get("ImpuestoP")) or "",
        tipo_factor_p=_text(m.get("TipoFactorP")) or "",
        tasa_o_cuota_p=_text(m.get("TasaOCuotaP")),
        importe_p=_text(m.get("ImporteP")),
    )


def _retencion_p(node: object) -> RawRetencionP:
    m = _map(node, "RetencionP")
    return RawRetencionP(
        impuesto_p=_text(m.get("ImpuestoP")) or "",
        importe_p=_text(m.get("ImporteP")) or "",
    )


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
