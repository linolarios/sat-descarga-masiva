"""Regenerate synthetic-but-real CFDI 4.0/3.3 XML golden fixtures (M2.3).
Synthetic TEST data only (RFCs from the SAT FIEL-de-pruebas package), no real taxpayer
information. The runtime SatcfdiFiscalParser parses REAL CFDI XML; these fixtures
exercise the satcfdi create/parse round-trip deterministically.

Run:  python tests/fixtures/generate_cfdi_fixtures.py
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from lxml import etree
from satcfdi.create.cfd import catalogos, cfdi33, cfdi40
from satcfdi.create.cfd.nomina12 import Nomina
from satcfdi.create.cfd.pago20 import (
    DoctoRelacionado,
    ImpuestosDR,
    Pago,
    Pagos,
    RetencionDR,
    TrasladoDR,
)

_OUT = Path(__file__).resolve().parent
_FECHA = datetime(2024, 1, 15, 12, 0, tzinfo=UTC)
_UUID = "123e4567-e89b-12d3-a456-426614174000"


def _emisor() -> dict[str, object]:
    return {
        "Rfc": "AAA010101AAA",
        "Nombre": "EMPRESA DE PRUEBA SA DE CV",
        "RegimenFiscal": catalogos.RegimenFiscal.GENERAL_DE_LEY_PERSONAS_MORALES,
    }


def _receptor(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "Rfc": "BBB010101BBB",
        "Nombre": "RECEPTOR DE PRUEBA SA DE CV",
        "DomicilioFiscalReceptor": "12345",
        "RegimenFiscalReceptor": catalogos.RegimenFiscal.GENERAL_DE_LEY_PERSONAS_MORALES,
        "UsoCFDI": catalogos.UsoCFDI.GASTOS_EN_GENERAL,
    }
    data.update(overrides)
    return data


def _receptor_nomina() -> dict[str, object]:
    return {
        "Rfc": "BBB010101BBB",
        "Nombre": "RECEPTOR DE PRUEBA SA DE CV",
        "Curp": "BABR010101HDFPLN02",
        "TipoContrato": "01",
        "TipoRegimen": "02",
        "NumEmpleado": "1",
        "PeriodicidadPago": "04",
        "ClaveEntFed": "DIF",
    }


def _concepto(
    descripcion: str, valor: Decimal, *, descuento: Decimal | None = None
) -> dict[str, object]:
    base: dict[str, object] = {
        "ClaveProdServ": "01010101",
        "Cantidad": Decimal("1"),
        "ClaveUnidad": "H87",
        "Descripcion": descripcion,
        "ValorUnitario": valor,
    }
    if descuento is not None:
        base["Descuento"] = descuento
    return base


def _concepto_con_iva(descripcion: str, valor: Decimal) -> dict[str, object]:
    concepto = _concepto(descripcion, valor)
    concepto["Impuestos"] = {
        "Traslados": [
            {
                "Base": valor,
                "Impuesto": catalogos.Impuesto.IVA,
                "TipoFactor": catalogos.TipoFactor.TASA,
                "TasaOCuota": Decimal("0.160000"),
                "Importe": Decimal("16.00"),
            }
        ],
        "Retenciones": [],
    }
    return concepto


def build_ingreso() -> cfdi40.Comprobante:
    return cfdi40.Comprobante(
        emisor=_emisor(),
        lugar_expedicion="12345",
        receptor=_receptor(),
        conceptos=[_concepto_con_iva("Servicio de ingreso", Decimal("100.00"))],
        tipo_de_comprobante=catalogos.TipoDeComprobante.INGRESO,
        forma_pago=catalogos.FormaPago.TRANSFERENCIA_ELECTRONICA_DE_FONDOS,
        metodo_pago=catalogos.MetodoPago.PAGO_EN_UNA_SOLA_EXHIBICION,
        fecha=_FECHA,
    )


def build_egreso() -> cfdi40.Comprobante:
    return cfdi40.Comprobante(
        emisor=_emisor(),
        lugar_expedicion="12345",
        receptor=_receptor(),
        conceptos=[_concepto("Nota de credito", Decimal("100.00"), descuento=Decimal("10.00"))],
        tipo_de_comprobante=catalogos.TipoDeComprobante.EGRESO,
        forma_pago=catalogos.FormaPago.TRANSFERENCIA_ELECTRONICA_DE_FONDOS,
        metodo_pago=catalogos.MetodoPago.PAGO_EN_UNA_SOLA_EXHIBICION,
        fecha=_FECHA,
    )


def build_traslado() -> cfdi40.Comprobante:
    return cfdi40.Comprobante(
        emisor=_emisor(),
        lugar_expedicion="12345",
        receptor=_receptor(),
        conceptos=[_concepto("Traslado de mercancia", Decimal("100.00"))],
        tipo_de_comprobante=catalogos.TipoDeComprobante.TRASLADO,
        fecha=_FECHA,
    )


def build_nomina() -> cfdi40.Comprobante:
    nomina = Nomina(
        tipo_nomina="O",
        fecha_pago=date(2024, 1, 15),
        fecha_inicial_pago=date(2024, 1, 1),
        fecha_final_pago=date(2024, 1, 15),
        num_dias_pagados=Decimal("15"),
        receptor=_receptor_nomina(),
        emisor=_emisor(),
        percepciones={
            "Percepcion": [
                {
                    "TipoPercepcion": "001",
                    "Clave": "P001",
                    "Concepto": "Sueldo",
                    "ImporteGravado": Decimal("10000.00"),
                    "ImporteExento": Decimal("0.00"),
                }
            ]
        },
        deducciones={
            "Deduccion": [
                {
                    "TipoDeduccion": "002",
                    "Clave": "D001",
                    "Concepto": "ISR",
                    "Importe": Decimal("1500.00"),
                }
            ]
        },
    )
    return cfdi40.Comprobante.nomina(
        emisor=_emisor(),
        lugar_expedicion="12345",
        receptor=_receptor(),
        complemento_nomina=nomina,
        fecha=_FECHA,
    )


def build_pago() -> cfdi40.Comprobante:
    pagos = Pagos(
        pago=[
            Pago(
                fecha_pago=datetime(2024, 1, 16, 12, 0, tzinfo=UTC),
                forma_de_pago_p=catalogos.FormaPago.TRANSFERENCIA_ELECTRONICA_DE_FONDOS,
                moneda_p="MXN",
                tipo_cambio_p=Decimal("1"),
                monto=Decimal("116.00"),
                docto_relacionado=[
                    DoctoRelacionado(
                        id_documento=_UUID,
                        moneda_dr="MXN",
                        num_parcialidad=1,
                        imp_saldo_ant=Decimal("116.00"),
                        imp_pagado=Decimal("116.00"),
                        objeto_imp_dr="02",
                    )
                ],
            )
        ]
    )
    return cfdi40.Comprobante.pago(
        emisor=_emisor(),
        lugar_expedicion="12345",
        receptor=_receptor(),
        complemento_pago=pagos,
        fecha=_FECHA,
    )


def build_eur() -> cfdi40.Comprobante:
    return cfdi40.Comprobante(
        emisor=_emisor(),
        lugar_expedicion="12345",
        receptor=_receptor(),
        conceptos=[_concepto_con_iva("Servicio en EUR", Decimal("100.00"))],
        tipo_de_comprobante=catalogos.TipoDeComprobante.INGRESO,
        forma_pago=catalogos.FormaPago.TRANSFERENCIA_ELECTRONICA_DE_FONDOS,
        metodo_pago=catalogos.MetodoPago.PAGO_EN_UNA_SOLA_EXHIBICION,
        moneda="EUR",
        tipo_cambio=Decimal("17.8456"),
        fecha=_FECHA,
    )


def build_ingreso_33() -> cfdi33.Comprobante:
    return cfdi33.Comprobante(
        emisor=_emisor(),
        lugar_expedicion="12345",
        receptor=_receptor(),
        conceptos=[_concepto_con_iva("Servicio de ingreso 3.3", Decimal("100.00"))],
        tipo_de_comprobante=catalogos.TipoDeComprobante.INGRESO,
        forma_pago=catalogos.FormaPago.TRANSFERENCIA_ELECTRONICA_DE_FONDOS,
        metodo_pago=catalogos.MetodoPago.PAGO_EN_UNA_SOLA_EXHIBICION,
        fecha=_FECHA,
    )


_TFD_NS = "http://www.sat.gob.mx/TimbreFiscalDigital"
etree.register_namespace("tfd", _TFD_NS)

_TFD_UUID = "123e4567-e89b-12d3-a456-426614174000"
_RELACION_UUID_1 = "11111111-1111-1111-1111-111111111111"
_RELACION_UUID_2 = "22222222-2222-2222-2222-222222222222"
_PAGO_UUID_1 = "33333333-3333-3333-3333-333333333333"
_PAGO_UUID_2 = "44444444-4444-4444-4444-444444444444"
_PAGO_UUID_3 = "55555555-5555-5555-5555-555555555555"


def _with_tfd(xml: bytes, uuid: str = _TFD_UUID) -> bytes:
    """Attach a deterministic TimbreFiscalDigital to a generated comprobante.

    satcfdi create-side timbrado requires a real Signer (certificate) and these
    fixtures exercise parsing/preservation rather than crypto validation, so the TFD
    carries fixed test values. The UUID is the value M2.3b reads.
    """
    root = etree.fromstring(xml)
    ns = root.tag.split("}")[0].strip("{")
    complemento = root.find(f"{{{ns}}}Complemento")
    if complemento is None:
        complemento = etree.SubElement(root, f"{{{ns}}}Complemento")
    etree.SubElement(
        complemento,
        f"{{{_TFD_NS}}}TimbreFiscalDigital",
        Version="1.1",
        UUID=uuid,
        FechaTimbrado="2024-01-15T12:00:00",
        RfcProvCertif="SPR190613I52",
        SelloCFD="c2VsbG8tY2ZkLXRlc3Q=",
        NoCertificadoSAT="30001000000400002495",
        SelloSAT="c2VsbG8tc2F0LXRlc3Q=",
    )
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8")


def build_tfd() -> bytes:
    """Ingreso carrying a TFD: UUID extraction only, no crypto meaning."""
    return _with_tfd(build_ingreso().xml_bytes())


def build_relacion() -> bytes:
    """Egreso with one TipoRelacion 01 and TWO related UUIDs (source order)."""
    comprobante = cfdi40.Comprobante(
        emisor=_emisor(),
        lugar_expedicion="12345",
        receptor=_receptor(),
        conceptos=[_concepto("Nota de credito relacionada", Decimal("100.00"))],
        tipo_de_comprobante=catalogos.TipoDeComprobante.EGRESO,
        forma_pago=catalogos.FormaPago.TRANSFERENCIA_ELECTRONICA_DE_FONDOS,
        metodo_pago=catalogos.MetodoPago.PAGO_EN_UNA_SOLA_EXHIBICION,
        cfdi_relacionados=cfdi40.CfdiRelacionados(
            tipo_relacion="01",
            cfdi_relacionado=[_RELACION_UUID_1, _RELACION_UUID_2],
        ),
        fecha=_FECHA,
    )
    return _with_tfd(comprobante.xml_bytes())


def build_relacion_33() -> bytes:
    """CFDI 3.3 relation: satcfdi yields a scalar CfdiRelacionados container here."""
    comprobante = cfdi33.Comprobante(
        emisor=_emisor(),
        lugar_expedicion="12345",
        receptor=_receptor(),
        conceptos=[_concepto("Nota de credito relacionada 3.3", Decimal("100.00"))],
        tipo_de_comprobante=catalogos.TipoDeComprobante.EGRESO,
        forma_pago=catalogos.FormaPago.TRANSFERENCIA_ELECTRONICA_DE_FONDOS,
        metodo_pago=catalogos.MetodoPago.PAGO_EN_UNA_SOLA_EXHIBICION,
        cfdi_relacionados=cfdi33.CfdiRelacionados(
            tipo_relacion="01",
            cfdi_relacionado=[_RELACION_UUID_1, _RELACION_UUID_2],
        ),
        fecha=_FECHA,
    )
    return _with_tfd(comprobante.xml_bytes())


def _traslado_dr(base: Decimal, importe: Decimal) -> TrasladoDR:
    return TrasladoDR(
        base_dr=base,
        impuesto_dr="002",
        tipo_factor_dr="Tasa",
        tasa_o_cuota_dr=Decimal("0.160000"),
        importe_dr=importe,
    )


def build_pago_detallado() -> bytes:
    """REP 2.0: two Pago elements, three DoctoRelacionado, ImpuestosDR and ImpuestosP.

    Proves the raw model keeps the one-to-many structure instead of aggregating it.
    """
    pagos = Pagos(
        pago=[
            Pago(
                fecha_pago=datetime(2024, 2, 10, 10, 30, tzinfo=UTC),
                forma_de_pago_p=catalogos.FormaPago.TRANSFERENCIA_ELECTRONICA_DE_FONDOS,
                moneda_p="MXN",
                tipo_cambio_p=Decimal("1"),
                num_operacion="OP-0001",
                docto_relacionado=[
                    DoctoRelacionado(
                        id_documento=_PAGO_UUID_1,
                        moneda_dr="MXN",
                        num_parcialidad=1,
                        imp_saldo_ant=Decimal("116.00"),
                        imp_pagado=Decimal("116.00"),
                        objeto_imp_dr="02",
                        impuestos_dr=ImpuestosDR(
                            traslados_dr=[_traslado_dr(Decimal("100.00"), Decimal("16.00"))]
                        ),
                    ),
                    DoctoRelacionado(
                        id_documento=_PAGO_UUID_2,
                        moneda_dr="MXN",
                        num_parcialidad=2,
                        imp_saldo_ant=Decimal("200.00"),
                        imp_pagado=Decimal("50.00"),
                        objeto_imp_dr="02",
                        impuestos_dr=ImpuestosDR(
                            traslados_dr=[_traslado_dr(Decimal("50.00"), Decimal("8.00"))],
                            retenciones_dr=[
                                RetencionDR(
                                    base_dr=Decimal("50.00"),
                                    impuesto_dr="002",
                                    tipo_factor_dr="Tasa",
                                    tasa_o_cuota_dr=Decimal("0.106667"),
                                    importe_dr=Decimal("5.33"),
                                )
                            ],
                        ),
                    ),
                ],
            ),
            Pago(
                fecha_pago=datetime(2024, 3, 10, 9, 15, tzinfo=UTC),
                forma_de_pago_p=catalogos.FormaPago.EFECTIVO,
                moneda_p="USD",
                tipo_cambio_p=Decimal("17.5000"),
                num_operacion="OP-0002",
                docto_relacionado=[
                    DoctoRelacionado(
                        id_documento=_PAGO_UUID_3,
                        moneda_dr="USD",
                        num_parcialidad=1,
                        imp_saldo_ant=Decimal("100.00"),
                        imp_pagado=Decimal("100.00"),
                        objeto_imp_dr="01",
                        serie="A",
                        folio="100",
                    )
                ],
            ),
        ]
    )
    comprobante = cfdi40.Comprobante.pago(
        emisor=_emisor(),
        lugar_expedicion="12345",
        receptor=_receptor(),
        complemento_pago=pagos,
        fecha=_FECHA,
    )
    return _with_tfd(comprobante.xml_bytes())


def main() -> None:
    _OUT.mkdir(parents=True, exist_ok=True)
    fixtures = {
        "cfdi_ingreso_4_0.xml": build_ingreso,
        "cfdi_egreso_4_0.xml": build_egreso,
        "cfdi_traslado_4_0.xml": build_traslado,
        "cfdi_nomina_4_0.xml": build_nomina,
        "cfdi_pago_4_0.xml": build_pago,
        "cfdi_eur_4_0.xml": build_eur,
        "cfdi_ingreso_3_3.xml": build_ingreso_33,
        "cfdi_tfd_4_0.xml": build_tfd,
        "cfdi_relacion_4_0.xml": build_relacion,
        "cfdi_relacion_3_3.xml": build_relacion_33,
        "cfdi_pago_detallado_4_0.xml": build_pago_detallado,
    }
    for name, builder in fixtures.items():
        produced = builder()
        xml = produced if isinstance(produced, bytes) else produced.xml_bytes()
        (_OUT / name).write_bytes(xml)
        print(f"wrote {_OUT / name}")


if __name__ == "__main__":
    main()
