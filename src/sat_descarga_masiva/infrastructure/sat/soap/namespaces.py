"""Namespace registry — no XML namespace magic strings elsewhere."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Namespace:
    prefix: str
    uri: str


class NS:
    SOAP = Namespace("s", "http://schemas.xmlsoap.org/soap/envelope/")
    WSSE = Namespace(
        "o", "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
    )
    WSU = Namespace(
        "u", "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"
    )
    DS = Namespace("ds", "http://www.w3.org/2000/09/xmldsig#")
    SAT = Namespace("des", "http://DescargaMasivaTerceros.sat.gob.mx")
