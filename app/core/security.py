"""Passwords and tokens. Only hashes of random secrets are ever stored."""

import hashlib
import hmac
import secrets
from contextlib import suppress
from functools import lru_cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# Argon2id with argon2-cffi defaults (RFC 9106 low-memory profile). Decision R-019.
_hasher = PasswordHasher()


def new_token() -> str:
    """256 random bits, URL-safe. Used for session IDs and one-time links."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """SHA-256 is enough for random 256-bit tokens (no offline guessing is possible)."""
    return hashlib.sha256(token.encode()).hexdigest()


def tokens_match(expected: str, given: str) -> bool:
    return hmac.compare_digest(expected.encode(), given.encode())


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    if password_hash is None:
        # Spend the same time as a real check, so unknown accounts are not faster to probe.
        _dummy_verify(password)
        return False
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


@lru_cache
def _dummy_hash() -> str:
    return _hasher.hash(secrets.token_urlsafe(16))


def _dummy_verify(password: str) -> None:
    with suppress(VerificationError):
        _hasher.verify(_dummy_hash(), password)
