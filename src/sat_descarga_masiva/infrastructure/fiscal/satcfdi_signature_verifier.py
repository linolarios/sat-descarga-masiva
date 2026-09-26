"""SatcfdiSignatureVerifier -- CFDI authenticity validation via satcfdi (M2.5, §6a.3).

Boundary: satcfdi, lxml and pyOpenSSL stay inside this module; only the neutral domain
SignatureVerdict crosses back (§4/§5). No FIEL is needed: validation is read-only. The
pinned validator re-checks the emisor Sello against the embedded certificate, the TFD
SelloSAT against the SAT certificate named by NoCertificadoSAT, the certificate validity
windows and the 72 hour issuance window.

A verdict depends only on the document bytes, except for the single external call satcfdi
makes to fetch a SAT certificate (rdc.sat.gob.mx). A network failure is reported as
VALIDATION_ERROR and never as INVALID: not reaching the authority is not evidence against
the document.

Exception mapping is an explicit allow-list per step. An unanticipated exception
(AttributeError, TypeError, IndexError, ValueError, AssertionError, ...) is a bug here or in
satcfdi and PROPAGATES instead of quietly becoming fiscal review state:

    input is not well-formed XML                                -> UNSUPPORTED
    root is not a CFDI form the validator supports              -> UNSUPPORTED
    required sello material is missing or empty                 -> ABSENT
    SAT.validate(cfdi) is True                                  -> VALID
    SAT.validate(cfdi) is False                                 -> INVALID
    SAT.validate(cfdi) is None (no validation path)             -> UNSUPPORTED
    requests.RequestException (SAT certificate unreachable)     -> VALIDATION_ERROR
    OpenSSL.crypto.Error (material present, unusable cert)      -> VALIDATION_ERROR
    KeyError (issuer not in the bundled SAT certificate store)  -> VALIDATION_ERROR
    satcfdi CFDIError (cadena original transform unavailable)   -> VALIDATION_ERROR

Documented limitation: satcfdi raises an undifferentiated KeyError when an issuer is not in
its bundled certificate store, so a chain that cannot be TRUSTED is reported as
VALIDATION_ERROR instead of INVALID. Telling those two apart would mean depending on
satcfdi internals, which this project deliberately avoids.
"""

from __future__ import annotations

from lxml import etree  # type: ignore[import-untyped]  # no stubs installed
from OpenSSL.crypto import Error as OpenSSLCryptoError
from requests import RequestException
from satcfdi.cfdi import CFDI  # type: ignore[import-untyped]
from satcfdi.exceptions import (  # type: ignore[import-untyped]
    CFDIError,
    NamespaceMismatchError,
    SchemaValidationError,
)
from satcfdi.pacs.sat import SAT  # type: ignore[import-untyped]

from sat_descarga_masiva.domain.model.signature import SignatureOutcome, SignatureVerdict

# Root elements the pinned validator has a case for (satcfdi 26.8, pacs/sat.py:498), with the
# sello material _validate reads from each document form. A root outside this table has no
# validation path at all, so it is UNSUPPORTED rather than a false INVALID.
_REQUIRED_MATERIAL: dict[str, tuple[str, ...]] = {
    "{http://www.sat.gob.mx/cfd/3}Comprobante": ("Sello", "Certificado", "NoCertificado"),
    "{http://www.sat.gob.mx/cfd/4}Comprobante": ("Sello", "Certificado", "NoCertificado"),
    "{http://www.sat.gob.mx/esquemas/retencionpago/1}Retenciones": ("Sello", "Cert", "NumCert"),
    "{http://www.sat.gob.mx/esquemas/retencionpago/2}Retenciones": (
        "Sello",
        "Certificado",
        "NoCertificado",
    ),
}

# The TFD is located by local name, so its namespace need not be hard-coded here.
_TFD_LOCAL_NAME = "TimbreFiscalDigital"
_TFD_MATERIAL = ("SelloCFD", "SelloSAT", "NoCertificadoSAT")


class SatcfdiSignatureVerifier:
    """Implements application.ports.signature.CfdiSignatureVerifier over satcfdi."""

    def verify(self, xml_bytes: bytes) -> SignatureVerdict:
        """Authenticity verdict for the exact source bytes of one downloaded CFDI."""
        root = _well_formed_root(xml_bytes)
        if root is None:
            return SignatureVerdict(SignatureOutcome.UNSUPPORTED, "input is not well-formed XML")
        required = _REQUIRED_MATERIAL.get(str(root.tag))
        if required is None:
            return SignatureVerdict(
                SignatureOutcome.UNSUPPORTED, f"root element {root.tag} has no validation path"
            )
        missing = _missing_material(root, required)
        if missing is not None:
            return SignatureVerdict(
                SignatureOutcome.ABSENT, f"missing signature material: {missing}"
            )
        cfdi = _deserialize(xml_bytes)
        if cfdi is None:
            return SignatureVerdict(
                SignatureOutcome.UNSUPPORTED, "satcfdi cannot deserialize this document"
            )
        return _validated(cfdi)


def _well_formed_root(xml_bytes: bytes) -> etree._Element | None:
    """Root element of well-formed XML bytes, or None when they are not well-formed XML.

    Documents arrive inside a downloaded package and are untrusted, so parsing never expands
    entities, never loads a DTD and never touches the network. The parser is built per call
    because lxml parsers are not thread-safe and this module keeps no global state.
    """
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    try:
        return etree.fromstring(xml_bytes, parser=parser)
    except etree.XMLSyntaxError:
        return None


def _deserialize(xml_bytes: bytes) -> CFDI | None:
    """satcfdi CFDI for these bytes, or None when satcfdi cannot deserialize this form.

    KeyError is in the allow-list because satcfdi raises it for a root it does not know; the
    table above already rejects non-CFDI roots, so this is a second, independent gate.
    """
    try:
        return CFDI.from_string(xml_bytes)
    except (CFDIError, SchemaValidationError, NamespaceMismatchError, KeyError):
        return None


def _missing_material(root: etree._Element, required: tuple[str, ...]) -> str | None:
    """Names of the missing or empty sello material, or None when material is complete.

    Comprobante material is checked first, then the TFD the CFDI must carry: a document
    without a TimbreFiscalDigital is not a stamped CFDI and cannot be authenticated.
    """
    missing = [name for name in required if _is_empty(root.get(name))]
    timbre = _timbre(root)
    if timbre is None:
        missing.append(_TFD_LOCAL_NAME)
    else:
        missing.extend(name for name in _TFD_MATERIAL if _is_empty(timbre.get(name)))
    return ", ".join(missing) if missing else None


def _timbre(root: etree._Element) -> etree._Element | None:
    """First TimbreFiscalDigital descendant, located by local name."""
    for element in root.iter():
        if isinstance(element.tag, str) and etree.QName(element).localname == _TFD_LOCAL_NAME:
            return element
    return None


def _is_empty(value: str | None) -> bool:
    """True for an absent attribute and for one that carries only whitespace."""
    return value is None or not value.strip()


def _validated(cfdi: CFDI) -> SignatureVerdict:
    """Verdict from the pinned validator; only allow-listed failures become a verdict.

    The validator is stateless for validate(), so a fresh instance per document keeps this
    adapter free of shared mutable state (satcfdi stores a SOAP token on SAT instances).
    """
    validator = SAT(signer=None)
    try:
        result = validator.validate(cfdi)
    except (RequestException, OpenSSLCryptoError, KeyError, CFDIError) as exc:
        return SignatureVerdict(SignatureOutcome.VALIDATION_ERROR, _blocked_detail(exc))
    if result is None:
        return SignatureVerdict(
            SignatureOutcome.UNSUPPORTED, "satcfdi has no validation path for this form"
        )
    if result is True:
        return SignatureVerdict(SignatureOutcome.VALID)
    return SignatureVerdict(SignatureOutcome.INVALID, "signature or certificate rejected")


def _blocked_detail(exc: BaseException) -> str:
    """Deterministic detail for an allow-listed external validation failure."""
    if isinstance(exc, RequestException):
        return "SAT certificate could not be retrieved"
    if isinstance(exc, OpenSSLCryptoError):
        return "certificate material is not a usable certificate"
    if isinstance(exc, KeyError):
        return "issuer certificate is not in the bundled SAT store"
    return "cadena original transform unavailable"
