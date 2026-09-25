import hashlib

from app.pipeline.hashing import sha256_hex


def test_sha256_hex_matches_stdlib():
    text = "hello world"
    assert sha256_hex(text) == hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_sha256_hex_is_deterministic():
    text = "the same input"
    assert sha256_hex(text) == sha256_hex(text)


def test_sha256_hex_differs_for_different_input():
    assert sha256_hex("a") != sha256_hex("b")


def test_sha256_hex_length_is_64_hex_chars():
    digest = sha256_hex("anything")
    assert len(digest) == 64
    int(digest, 16)  # raises if not valid hex
