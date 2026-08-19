"""Shared fixtures. Generates a self-signed FIEL de PRUEBAS in-memory (never a real e.firma)."""

from __future__ import annotations

import datetime as dt

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from sat_descarga_masiva.infrastructure.credentials.fiel import CryptographyFielLoader, FielIdentity

TEST_RFC = "AAA010101AAA"
TEST_PASSWORD = "test-password"


@pytest.fixture(scope="session")
def fiel_bytes() -> tuple[bytes, bytes, str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "FIEL DE PRUEBAS"),
            x509.NameAttribute(x509.ObjectIdentifier("2.5.4.45"), f"{TEST_RFC} / TEST"),
        ]
    )
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=365))
        .sign(key, hashes.SHA256())
    )
    cer_der = cert.public_bytes(serialization.Encoding.DER)
    key_der = key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.BestAvailableEncryption(TEST_PASSWORD.encode()),
    )
    return cer_der, key_der, TEST_PASSWORD, TEST_RFC


@pytest.fixture()
def fiel(fiel_bytes: tuple[bytes, bytes, str, str]) -> FielIdentity:
    cer, key, pwd, _ = fiel_bytes
    return CryptographyFielLoader().load(cer, key, pwd)
