"""
End-to-end encryption using Fernet (AES-128-CBC + HMAC-SHA256).
Provides session-based key derivation and message encryption/decryption.
"""
from __future__ import annotations
import base64
import hashlib
import os
from cryptography.fernet import Fernet, InvalidToken


def derive_key(secret: str, salt: bytes | None = None) -> tuple[bytes, bytes]:
    """Derive a Fernet-compatible key from a shared secret using PBKDF2."""
    if salt is None:
        salt = os.urandom(16)
    key_material = hashlib.pbkdf2_hmac(
        "sha256", secret.encode(), salt, iterations=100_000, dklen=32
    )
    fernet_key = base64.urlsafe_b64encode(key_material)
    return fernet_key, salt


class SessionCipher:
    """Symmetric cipher for a single authenticated session."""

    def __init__(self, key: bytes) -> None:
        self._fernet = Fernet(key)

    @classmethod
    def from_secret(cls, secret: str, salt: bytes) -> "SessionCipher":
        key, _ = derive_key(secret, salt)
        return cls(key)

    def encrypt(self, data: bytes) -> bytes:
        return self._fernet.encrypt(data)

    def decrypt(self, token: bytes) -> bytes:
        return self._fernet.decrypt(token)

    def encrypt_str(self, text: str) -> str:
        return self.encrypt(text.encode()).decode()

    def decrypt_str(self, token: str) -> str:
        return self.decrypt(token.encode()).decode()


def generate_token() -> str:
    """Generate a secure random auth token."""
    return base64.urlsafe_b64encode(os.urandom(32)).decode()


def safe_decrypt(cipher: SessionCipher, data: bytes) -> bytes | None:
    """Decrypt without raising; returns None on failure."""
    try:
        return cipher.decrypt(data)
    except (InvalidToken, Exception):
        return None
