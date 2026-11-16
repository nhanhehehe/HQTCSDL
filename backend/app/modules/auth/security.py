import base64
import hashlib
import hmac
import re
import secrets

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError

password_hasher = PasswordHasher(memory_cost=19456, time_cost=2, parallelism=1, type=Type.ID)
DUMMY_HASH = password_hasher.hash(secrets.token_urlsafe(32))


def verify_password(encoded: str, password: str) -> bool:
    try:
        return password_hasher.verify(encoded, password)
    except (VerificationError, InvalidHashError):
        return False


def encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode(value: str | None) -> bytes | None:
    if value is None or re.fullmatch(r"[A-Za-z0-9_-]{43}", value) is None:
        return None
    raw = base64.urlsafe_b64decode(value + "=")
    return raw if len(raw) == 32 and encode(raw) == value else None


def token_hash(value: str | None) -> bytes | None:
    raw = decode(value)
    return hashlib.sha256(raw).digest() if raw is not None else None


def csrf(secret: str, digest: bytes) -> str:
    message = b"csrf-v1:" + digest.hex().encode("ascii")
    return encode(hmac.digest(secret.encode("utf-8"), message, "sha256"))


def valid_csrf(secret: str, digest: bytes, value: str | None) -> bool:
    return decode(value) is not None and hmac.compare_digest(csrf(secret, digest), value)
