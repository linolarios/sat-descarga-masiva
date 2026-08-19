"""FIEL loading (infrastructure). FielIdentity is repr-safe and keeps the key internal."""

from __future__ import annotations

import base64

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from cryptography.hazmat.primitives.serialization import load_der_private_key

_OID_RFC = x509.ObjectIdentifier("2.5.4.45")


class FielIdentity:
    """Implements domain.model.credentials.SigningIdentity structurally."""

    __slots__ = ("_certificate", "_private_key", "_cer_der")

    def __init__(
        self, certificate: x509.Certificate, private_key: RSAPrivateKey, cer_der: bytes
    ) -> None:
        self._certificate = certificate
        self._private_key = private_key
        self._cer_der = cer_der

    @property
    def rfc(self) -> str:
        for attr in self._certificate.subject.get_attributes_for_oid(_OID_RFC):
            return str(attr.value).split("/")[0].strip().upper()
        raise ValueError("RFC (OID 2.5.4.45) not present in certificate subject")

    @property
    def cer_base64(self) -> str:
        return base64.b64encode(self._cer_der).decode("ascii")

    def sign(self, data: bytes) -> bytes:
        return self._private_key.sign(data, padding.PKCS1v15(), hashes.SHA1())

    def __repr__(self) -> str:  # never leak key material
        return f"FielIdentity(rfc={self.rfc!r})"


class CryptographyFielLoader:
    """Implements application.ports.services.FielLoader."""

    def load(self, cer: bytes, key: bytes, password: str) -> FielIdentity:
        certificate = x509.load_der_x509_certificate(cer)
        private_key = load_der_private_key(key, password.encode("utf-8"))
        if not isinstance(private_key, RSAPrivateKey):
            raise ValueError("FIEL private key must be RSA")
        return FielIdentity(certificate, private_key, cer)
