from sat_descarga_masiva.infrastructure.credentials.fiel import FielIdentity


def test_rfc_extracted(fiel: FielIdentity) -> None:
    assert fiel.rfc == "AAA010101AAA"


def test_sign_roundtrips(fiel: FielIdentity) -> None:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    sig = fiel.sign(b"hello")
    # verify with the public key from the cert (no exception == valid)
    fiel._certificate.public_key().verify(  # noqa: SLF001 (test reaches in deliberately)
        sig, b"hello", padding.PKCS1v15(), hashes.SHA1()
    )


def test_repr_hides_key(fiel: FielIdentity) -> None:
    assert "rfc=" in repr(fiel)
    assert "private" not in repr(fiel).lower()
