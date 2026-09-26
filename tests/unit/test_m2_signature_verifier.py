"""M2.5: authenticity verdicts from the pinned satcfdi, offline and deterministic.

Every committed CFDI fixture carries an empty emisor Sello/Certificado/NoCertificado, and the
dummy TFD material in the stamped fixtures is not a real sello, so the honest outcome for each
fixture is ABSENT and never VALID. VALID, INVALID and the allow-listed external failures are
driven by stubbing the pinned validator, because a genuine VALID needs a real SAT-signed CFDI
and the SAT timbre certificate, neither of which may be committed (.gitignore blocks the *.cer
and *.key patterns). The stub replaces the cryptographic validator, never the mapping under
test, so these tests pin exactly what this milestone owns.

One real, unstubbed crypto path is exercised: complete material that the pinned validator
rejects with a pyOpenSSL error before any network call is made.
"""

from pathlib import Path

import pytest
import requests
from lxml import etree
from OpenSSL.crypto import Error as OpenSSLCryptoError
from satcfdi.exceptions import CFDIError
from satcfdi.pacs.sat import SAT

from sat_descarga_masiva.domain.model.signature import SignatureOutcome, SignatureVerdict
from sat_descarga_masiva.infrastructure.fiscal.satcfdi_signature_verifier import (
    SatcfdiSignatureVerifier,
)

_FIXTURES = Path(__file__).parent.parent / "fixtures"
_CFDI_FIXTURES = sorted(path.name for path in _FIXTURES.glob("cfdi_*.xml"))
_SIGNED_FIXTURE = "cfdi_pago_detallado_4_0.xml"
_NO_TFD_FIXTURE = "cfdi_pago_4_0.xml"
_COMPROBANTE_ATTRS = ("Sello", "Certificado", "NoCertificado")
_TFD_ATTRS = ("SelloCFD", "SelloSAT", "NoCertificadoSAT")
_ALL_ATTRS = (*_COMPROBANTE_ATTRS, *_TFD_ATTRS)
_MATERIAL = {
    "Sello": "c2VsbG8tY2ZkLXRlc3Q=",
    "Certificado": "QUJD",
    "NoCertificado": "30001000000400002495",
    "SelloCFD": "c2VsbG8tY2ZkLXRlc3Q=",
    "SelloSAT": "c2VsbG8tc2F0LXRlc3Q=",
    "NoCertificadoSAT": "30001000000400002495",
}

_verifier = SatcfdiSignatureVerifier()


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any HTTP attempt fails this test instead of reaching SAT."""

    def _refuse(*args: object, **kwargs: object) -> None:
        pytest.fail("network access attempted")

    monkeypatch.setattr(requests, "get", _refuse)


def _xml(name: str) -> bytes:
    return (_FIXTURES / name).read_bytes()


def _timbre(root: etree._Element) -> etree._Element:
    return next(
        element
        for element in root.iter()
        if isinstance(element.tag, str) and etree.QName(element).localname == "TimbreFiscalDigital"
    )


def _material(absent: tuple[str, ...] = ()) -> bytes:
    """Signed fixture with the named sello attributes emptied and every other one filled.

    The fixtures carry empty comprobante material but dummy TFD material, so an absence has to
    be written explicitly: each pre-check branch is then reached on its own.
    """
    root = etree.fromstring(_xml(_SIGNED_FIXTURE))
    timbre = _timbre(root)
    for name in _COMPROBANTE_ATTRS:
        root.set(name, "" if name in absent else _MATERIAL[name])
    for name in _TFD_ATTRS:
        timbre.set(name, "" if name in absent else _MATERIAL[name])
    return etree.tostring(root)


def _comprobante_material_without_tfd() -> bytes:
    """A CFDI with complete comprobante material but no TimbreFiscalDigital at all."""
    root = etree.fromstring(_xml(_NO_TFD_FIXTURE))
    for name in _COMPROBANTE_ATTRS:
        root.set(name, _MATERIAL[name])
    return etree.tostring(root)


def _stub_validator(monkeypatch: pytest.MonkeyPatch, result: object) -> None:
    """Replace the pinned cryptographic validator with a fixed answer."""
    monkeypatch.setattr(SAT, "validate", lambda self, cfdi: result)


# --- the committed corpus ---------------------------------------------------


def test_expected_fixtures_are_present() -> None:
    assert _SIGNED_FIXTURE in _CFDI_FIXTURES
    assert _NO_TFD_FIXTURE in _CFDI_FIXTURES


@pytest.mark.parametrize("name", _CFDI_FIXTURES)
def test_unsigned_fixture_is_absent_and_never_valid(name: str) -> None:
    verdict = _verifier.verify(_xml(name))
    assert verdict.outcome is SignatureOutcome.ABSENT
    assert verdict.is_valid is False


def test_empty_comprobante_material_is_absent_even_with_tfd_material_present() -> None:
    """The stamped fixtures carry dummy TFD material, but no emisor certificate: ABSENT."""
    verdict = _verifier.verify(_xml(_SIGNED_FIXTURE))
    assert verdict.outcome is SignatureOutcome.ABSENT
    assert verdict.detail == "missing signature material: Sello, Certificado, NoCertificado"


# --- input form -------------------------------------------------------------


def test_well_formed_non_cfdi_root_is_unsupported() -> None:
    verdict = _verifier.verify(_xml("verifica_response.xml"))
    assert verdict.outcome is SignatureOutcome.UNSUPPORTED
    assert "Envelope" in verdict.detail


def test_unknown_root_element_is_unsupported() -> None:
    verdict = _verifier.verify(b"<NotACfdi/>")
    assert verdict.outcome is SignatureOutcome.UNSUPPORTED
    assert "NotACfdi" in verdict.detail


@pytest.mark.parametrize("payload", [b"", b"<cfdi:Comprobante", b"not xml at all"])
def test_input_that_is_not_well_formed_xml_is_unsupported(payload: bytes) -> None:
    verdict = _verifier.verify(payload)
    assert verdict.outcome is SignatureOutcome.UNSUPPORTED
    assert verdict.detail == "input is not well-formed XML"


# --- material pre-check: one branch at a time -------------------------------


def test_no_material_at_all_is_absent() -> None:
    verdict = _verifier.verify(_material(absent=_ALL_ATTRS))
    assert verdict.outcome is SignatureOutcome.ABSENT
    assert verdict.detail == (
        "missing signature material: Sello, Certificado, NoCertificado, "
        "SelloCFD, SelloSAT, NoCertificadoSAT"
    )


def test_sello_without_certificate_material_is_absent() -> None:
    verdict = _verifier.verify(_material(absent=("Certificado", "NoCertificado")))
    assert verdict.outcome is SignatureOutcome.ABSENT
    assert verdict.detail == "missing signature material: Certificado, NoCertificado"


def test_tfd_material_is_required_for_a_stamped_cfdi() -> None:
    verdict = _verifier.verify(_material(absent=_TFD_ATTRS))
    assert verdict.outcome is SignatureOutcome.ABSENT
    assert verdict.detail == "missing signature material: SelloCFD, SelloSAT, NoCertificadoSAT"


@pytest.mark.parametrize("name", _ALL_ATTRS)
def test_each_material_attribute_is_individually_required(name: str) -> None:
    verdict = _verifier.verify(_material(absent=(name,)))
    assert verdict.outcome is SignatureOutcome.ABSENT
    assert verdict.detail == f"missing signature material: {name}"


def test_document_without_tfd_is_absent() -> None:
    verdict = _verifier.verify(_comprobante_material_without_tfd())
    assert verdict.outcome is SignatureOutcome.ABSENT
    assert verdict.detail == "missing signature material: TimbreFiscalDigital"


# --- validator mapping (stubbed pinned validator) ---------------------------


def test_validator_true_is_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_validator(monkeypatch, True)
    verdict = _verifier.verify(_material())
    assert verdict.outcome is SignatureOutcome.VALID
    assert verdict.is_valid is True
    assert verdict.detail == ""


def test_validator_false_is_invalid_not_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_validator(monkeypatch, False)
    verdict = _verifier.verify(_material())
    assert verdict.outcome is SignatureOutcome.INVALID
    assert verdict.detail == "signature or certificate rejected"
    assert verdict.is_valid is False


def test_validator_none_is_unsupported(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_validator(monkeypatch, None)
    verdict = _verifier.verify(_material())
    assert verdict.outcome is SignatureOutcome.UNSUPPORTED
    assert verdict.detail == "satcfdi has no validation path for this form"


@pytest.mark.parametrize(
    ("error", "detail"),
    [
        (requests.ConnectionError("boom"), "SAT certificate could not be retrieved"),
        (OpenSSLCryptoError(), "certificate material is not a usable certificate"),
        (KeyError("issuer"), "issuer certificate is not in the bundled SAT store"),
        (CFDIError("transform"), "cadena original transform unavailable"),
    ],
    ids=["network", "openssl", "unknown-issuer", "cadena-original"],
)
def test_allow_listed_failure_is_a_validation_error(
    monkeypatch: pytest.MonkeyPatch, error: Exception, detail: str
) -> None:
    def _raise(self: object, cfdi: object) -> bool:
        raise error

    monkeypatch.setattr(SAT, "validate", _raise)
    verdict = _verifier.verify(_material())
    assert verdict.outcome is SignatureOutcome.VALIDATION_ERROR
    assert verdict.detail == detail
    assert verdict.is_valid is False


@pytest.mark.parametrize(
    "error", [AttributeError, TypeError, IndexError, ValueError, AssertionError]
)
def test_unanticipated_failure_propagates(
    monkeypatch: pytest.MonkeyPatch, error: type[Exception]
) -> None:
    """A bug must surface as a bug, not as review state that a human is asked to clear."""

    def _raise(self: object, cfdi: object) -> bool:
        raise error("bug")

    monkeypatch.setattr(SAT, "validate", _raise)
    with pytest.raises(error):
        _verifier.verify(_material())


# --- real crypto path (no stub) ---------------------------------------------


def test_complete_but_unusable_material_is_a_validation_error() -> None:
    """All six attributes present, but the material is not usable as a certificate.

    This is the honest classification: the document is not ABSENT (it carries material) and
    the pinned validator cannot establish INVALID from it, so the failure stays external.
    """
    verdict = _verifier.verify(_material())
    assert verdict.outcome is SignatureOutcome.VALIDATION_ERROR
    assert verdict.detail == "certificate material is not a usable certificate"


def test_verdicts_are_deterministic_for_the_same_bytes() -> None:
    assert _verifier.verify(_material()) == _verifier.verify(_material())


def test_verdict_carries_no_library_object() -> None:
    verdict = _verifier.verify(_material())
    assert isinstance(verdict, SignatureVerdict)
    assert isinstance(verdict.outcome, SignatureOutcome)
    assert isinstance(verdict.detail, str)


# --- byte fidelity ----------------------------------------------------------


def test_the_validator_receives_the_exact_bytes_handed_to_verify(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing is re-serialized between extraction and validation."""
    seen: list[str] = []

    def _capture(self: object, cfdi: object) -> bool:
        seen.append(str(cfdi.get("Total")))
        return True

    monkeypatch.setattr(SAT, "validate", _capture)
    root = etree.fromstring(_material())
    root.set("Total", "116.01")
    verdict = _verifier.verify(etree.tostring(root))
    assert seen == ["116.01"]
    assert isinstance(verdict, SignatureVerdict)


def test_tampering_with_a_fact_is_never_valid_on_the_real_path() -> None:
    root = etree.fromstring(_material())
    root.set("Total", "116.01")
    verdict = _verifier.verify(etree.tostring(root))
    assert verdict.outcome is not SignatureOutcome.VALID
    assert verdict.is_valid is False
