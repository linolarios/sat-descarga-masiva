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


class UnexpectedSatResponseError(SatClientError):
    """SAT returned an unexpected/technical response.

    Carries the SAT CodEstatus/Mensaje when available so a live failure is
    diagnosable (e.g. a rejected certificate) rather than a generic message.
    """

    def __init__(self, message: str, *, cod_estatus: str = "", mensaje: str = "") -> None:
        super().__init__(message)
        self.cod_estatus = cod_estatus
        self.mensaje = mensaje


class ExtractionError(SatClientError): ...


class SourceHashConflict(SatClientError):
    """An immutable record already exists for that identity with other content."""


class ImmutableRecordConflict(SatClientError):
    """Refusing to rewrite an append-only/versioned record in place."""


class ReviewFlagNotFound(SatClientError):
    """A review flag id does not identify an open flag row."""


class CsfParseError(SatClientError): ...
