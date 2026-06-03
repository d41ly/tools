from __future__ import annotations

import hashlib
import secrets
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


@lru_cache
def _fernet() -> Fernet:
    key = get_settings().app_secret_key.encode()
    try:
        return Fernet(key)
    except (ValueError, TypeError) as exc:
        raise RuntimeError(
            "APP_SECRET_KEY must be a 32-byte url-safe base64 Fernet key. "
            "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        ) from exc


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise RuntimeError("Failed to decrypt value (wrong APP_SECRET_KEY?)") from exc


def generate_token() -> tuple[str, str, str]:
    """Generate an API token. Returns (raw_token, sha256_hash, prefix)."""
    raw = "scrp_" + secrets.token_urlsafe(32)
    return raw, hashlib.sha256(raw.encode()).hexdigest(), raw[:13]


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()
