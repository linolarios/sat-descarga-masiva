"""SOAPAction registry — no action magic strings in gateways."""

from __future__ import annotations

from enum import StrEnum


class SoapAction(StrEnum):
    AUTHENTICATE = "http://DescargaMasivaTerceros.gob.mx/IAutenticacion/Autentica"
    REQUEST_EMITTED = (
        "http://DescargaMasivaTerceros.sat.gob.mx/ISolicitaDescargaService/SolicitaDescargaEmitidos"
    )
    REQUEST_RECEIVED = "http://DescargaMasivaTerceros.sat.gob.mx/ISolicitaDescargaService/SolicitaDescargaRecibidos"
    VERIFY = "http://DescargaMasivaTerceros.sat.gob.mx/IVerificaSolicitudDescargaService/VerificaSolicitudDescarga"
    DOWNLOAD = "http://DescargaMasivaTerceros.sat.gob.mx/IDescargaMasivaTercerosService/Descargar"
