import base64
import hashlib
import secrets

from sqlalchemy import text

KEY_PREFIX = "hqgw_v1_"
KEY_SECRET_B64_LEN = 43
KEY_TOTAL_LEN = len(KEY_PREFIX) + KEY_SECRET_B64_LEN


def generate_gateway_key() -> tuple[str, bytes]:
    """Generate a 32-byte CSPRNG gateway API key and its SHA-256 raw-byte digest."""
    raw = secrets.token_bytes(32)
    encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return KEY_PREFIX + encoded, hashlib.sha256(raw).digest()


def parse_gateway_key_and_digest(api_key: str) -> bytes | None:
    """Strictly parse a canonical hqgw_v1_ key and return SHA-256(32 raw secret bytes)."""
    if not isinstance(api_key, str) or len(api_key) != KEY_TOTAL_LEN:
        return None
    if not api_key.startswith(KEY_PREFIX):
        return None
    secret_part = api_key[len(KEY_PREFIX) :]
    try:
        raw = base64.urlsafe_b64decode(secret_part + "=")
    except (ValueError, TypeError):
        return None
    if len(raw) != 32:
        return None
    canonical = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    if canonical != secret_part:
        return None
    return hashlib.sha256(raw).digest()


def verify_gateway_key(session, *, gateway_code: str, api_key: str) -> tuple[bool, int, str]:
    """Verify a gateway API key against dbo.GatewayCredential and Gateway code (for G2)."""
    digest = parse_gateway_key_and_digest(api_key)
    if digest is None:
        return False, 401, "INVALID_API_KEY"
    row = (
        session.execute(
            text(
                "SELECT c.credential_id, c.gateway_id, c.revoked_at, g.gateway_code "
                "FROM dbo.GatewayCredential c "
                "JOIN dbo.Gateway g ON g.gateway_id = c.gateway_id "
                "WHERE c.key_hash = :digest"
            ),
            {"digest": digest},
        )
        .mappings()
        .first()
    )
    if row is None:
        return False, 401, "INVALID_API_KEY"
    if row["revoked_at"] is not None:
        return False, 403, "GATEWAY_FORBIDDEN"
    if row["gateway_code"] != gateway_code:
        return False, 403, "GATEWAY_FORBIDDEN"
    return True, 200, str(row["gateway_id"])
