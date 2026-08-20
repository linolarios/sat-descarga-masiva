"""M1: SourceIdentity + UUID/hash conflict rule (§6)."""

from sat_descarga_masiva.domain.model.source import (
    IncomingClassification,
    SourceIdentity,
    classify_incoming,
    sha256_hex,
)


def test_sha256_is_hex_digest() -> None:
    assert sha256_hex(b"abc") == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


def test_unknown_uuid_is_new() -> None:
    inc = SourceIdentity("u1", sha256_hex(b"a"))
    assert classify_incoming(inc, {}) is IncomingClassification.NEW


def test_same_uuid_same_hash_is_duplicate() -> None:
    incoming = SourceIdentity("u1", sha256_hex(b"a"))
    known = {"u1": sha256_hex(b"a")}
    assert classify_incoming(incoming, known) is IncomingClassification.DUPLICATE


def test_same_uuid_different_hash_is_conflict() -> None:
    incoming = SourceIdentity("u1", sha256_hex(b"a"))
    known = {"u1": sha256_hex(b"b")}
    assert classify_incoming(incoming, known) is IncomingClassification.CONFLICT
