"""Technical-failure hierarchy (expected SAT states are Results, not exceptions)."""

from __future__ import annotations


class SatClientError(Exception):
    """Base class for technical failures."""


class AuthenticationError(SatClientError): ...


class RequestValidationError(SatClientError): ...


class SoapError(SatClientError): ...


class XmlParseError(SatClientError): ...


class SignatureError(SatClientError): ...


class TransportError(SatClientError): ...


class SatTimeoutError(SatClientError): ...


class UnexpectedSatResponseError(SatClientError): ...
