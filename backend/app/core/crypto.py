"""Fork: encryption for secrets that models store, such as TOTP secrets.

Same scheme as `app.agents.services.crypto` (PBKDF2 of SECRET_KEY, opened
with the current key or any of `Settings.secret_key_fallbacks`), but
importable from models without loading the agents, and with a salt per
purpose so a TOTP secret never shares a key with a provider credential.
"""
from __future__ import annotations

import functools

from cryptography.fernet import InvalidToken, MultiFernet

from app.core.config import get_settings
from app.core.fernet_keys import CREDENTIALS_SALT, derive

TOTP_SALT = b"securo-user-totp-secrets-v1"


def _keys() -> tuple[str, ...]:
    settings = get_settings()
    return (settings.secret_key.get_secret_value(), *settings.secret_key_fallbacks)


@functools.lru_cache(maxsize=8)
def _fernet(keys: tuple[str, ...], salt: bytes) -> MultiFernet:
    # Encrypts with the first key; opens with any of them.
    return MultiFernet([derive(k, salt) for k in dict.fromkeys(keys)])


def encrypt(plaintext: str | None, salt: bytes = CREDENTIALS_SALT) -> str | None:
    if not plaintext:
        return None
    return _fernet(_keys(), salt).encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(ciphertext: str | None, salt: bytes = CREDENTIALS_SALT) -> str | None:
    if not ciphertext:
        return None
    try:
        return _fernet(_keys(), salt).decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return None  # corrupt or written under a key no longer configured
