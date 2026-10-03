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


class SourceIntegrityError(SatClientError):
    """A stored artifact no longer hashes to the digest its manifest records.

    This is **store** integrity (AGENT.md §6a.2): evidence we wrote has changed
    underneath us. It says nothing about SAT authenticity (§6a.3), which the
    signature verifier owns.
    """


class ImmutableRecordConflict(SatClientError):
    """Refusing to rewrite an append-only/versioned record in place."""


class ReviewFlagNotFound(SatClientError):
    """A review flag id does not identify an open flag row."""


class CsfParseError(SatClientError): ...


class MappingNotConfigured(SatClientError):
    """No account mapping exists for that client (§8a:207).

    A configuration failure, not a document-level review case: without a chart of
    accounts the engine could not book *any* of the client's documents, so the run
    stops loudly instead of falling back to a guessed account.
    """


class InvalidMapping(SatClientError):
    """A mapping source does not describe a usable, versioned account mapping (§8a:207)."""


class UnbalancedJournalCommit(SatClientError):
    """A ``POSTED`` record does not balance: the commit is aborted, never downgraded (§8:158).

    §8:158's ``Debe == Haber`` gate is checked twice, deliberately. The first check belongs to
    the ``PostingEligibilityValidator``: an unbalanced *proposal* is ``UNBALANCED_ENTRY`` +
    ``PROPOSED``, i.e. a review case. The second check is on the way to the ledger and is not a
    review case at all: by then the entry says ``POSTED``, so a record that does not balance is a
    *writer* bug, and a commit path that swallowed it would be the defect rather than the
    safeguard. It aborts loudly rather than writing half a posting to nobody, and it never
    rewrites the verdict, because a posting state is assigned once (§8:166): an unbalanced
    ``POSTED`` entry is fixed by fixing the writer, not by demoting it to ``PROPOSED``.
    """
