"""
crypto.py — Lightweight symmetric encryption for credential storage.

Uses Fernet (AES-128-CBC + HMAC-SHA256) from the `cryptography` package.

The encryption key is derived from the SECRET_KEY environment variable so
credentials are unreadable without it.  The key is padded / hashed to exactly
32 bytes and then base64url-encoded for Fernet.

Usage
─────
    from crypto import encrypt, decrypt

    ct = encrypt("my-password")   # → opaque base64 string, safe to store in DB
    pt = decrypt(ct)               # → "my-password"
"""

import base64
import hashlib
import os

from cryptography.fernet import Fernet


def _get_fernet() -> Fernet:
    """Build a Fernet instance from SECRET_KEY env-var (32-byte SHA-256 digest)."""
    raw_key: str = os.getenv("SECRET_KEY", "hypermonitor-default-insecure-key-change-me")
    # SHA-256 gives us exactly 32 bytes; Fernet wants base64url of 32 bytes
    digest = hashlib.sha256(raw_key.encode()).digest()
    fernet_key = base64.urlsafe_b64encode(digest)
    return Fernet(fernet_key)


def encrypt(plaintext: str) -> str:
    """Return an encrypted, base64-encoded ciphertext string."""
    if not plaintext:
        return ""
    token: bytes = _get_fernet().encrypt(plaintext.encode("utf-8"))
    return token.decode("utf-8")


def decrypt(ciphertext: str) -> str:
    """Decrypt a ciphertext produced by encrypt(). Returns empty string on failure."""
    if not ciphertext:
        return ""
    try:
        plain: bytes = _get_fernet().decrypt(ciphertext.encode("utf-8"))
        return plain.decode("utf-8")
    except Exception:
        return ""
