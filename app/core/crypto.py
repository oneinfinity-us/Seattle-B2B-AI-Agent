"""
Field-level encryption for GmailAccountCredential's stored OAuth tokens (see app/db/models.py).
`EncryptedText` is a SQLAlchemy column type: application code reads/writes plaintext as normal, and
encryption/decryption happens transparently at the DB boundary, so nothing else in the app needs to
know these columns are encrypted.

Fernet (from `cryptography`) gives authenticated symmetric encryption — a tampered or corrupted value
fails to decrypt loudly, rather than silently returning garbage.
"""
from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet
from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

from app.core.config import get_settings


@lru_cache
def _fernet() -> Fernet:
    key = get_settings().token_encryption_key
    if not key:
        raise RuntimeError(
            "TOKEN_ENCRYPTION_KEY is not set. Generate one with: "
            "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    return Fernet(key.encode())


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    return _fernet().decrypt(value.encode()).decode()


class EncryptedText(TypeDecorator):
    """A Text column that's encrypted at rest. The underlying DB column stays a plain TEXT — only the
    Python-level encoding changes, so this needs no schema migration."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect) -> str | None:
        return None if value is None else encrypt(value)

    def process_result_value(self, value: str | None, dialect) -> str | None:
        return None if value is None else decrypt(value)
