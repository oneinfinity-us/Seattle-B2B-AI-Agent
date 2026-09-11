from __future__ import annotations

import pytest

from app.core.crypto import decrypt, encrypt


def test_decrypt_reverses_encrypt():
    assert decrypt(encrypt("a-refresh-token")) == "a-refresh-token"


def test_ciphertext_does_not_contain_the_plaintext():
    ciphertext = encrypt("super-secret-refresh-token")
    assert "super-secret-refresh-token" not in ciphertext


def test_encrypting_the_same_value_twice_gives_different_ciphertext():
    # Fernet includes a random IV/nonce per encryption, so this also guards against a naive
    # implementation that would make identical tokens distinguishable at rest.
    assert encrypt("same-value") != encrypt("same-value")


def test_decrypting_garbage_raises():
    with pytest.raises(Exception):  # noqa: PT011 - cryptography raises its own InvalidToken, not ours
        decrypt("not-a-real-token")
