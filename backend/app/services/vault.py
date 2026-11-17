"""Encryption of stored collector credentials (Fernet, key derived from SECRET_KEY).

Passwords are never returned by the API; only `has_password` is exposed. Rotating SECRET_KEY
makes stored credentials unreadable, so collectors must be re-entered after a rotation.
"""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from ..auth import secret_key


def _fernet() -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret_key() + b"|collector-credentials").digest()))


def encrypt(value: str | None) -> str | None:
    return _fernet().encrypt(value.encode()).decode() if value else None


def decrypt(token: str | None) -> str | None:
    if not token:
        return None
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Stored credential cannot be decrypted (was SECRET_KEY changed?); re-enter the password") from exc
