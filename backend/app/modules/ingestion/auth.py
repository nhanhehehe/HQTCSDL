from dataclasses import dataclass

from sqlalchemy import text

from app.core.errors import IngestionError
from app.modules.devices import repository as devices_repo
from app.modules.devices.credentials import parse_gateway_key_and_digest


@dataclass(frozen=True)
class AuthenticatedGateway:
    credential_id: int
    gateway_id: int
    gateway_code: str
    farm_id: int
    key_hash: bytes


def extract_api_key_digest(api_key: str | None) -> bytes:
    """Parse X-API-Key using G1 canonical parser and return SHA-256(32 raw bytes)."""
    digest = parse_gateway_key_and_digest(api_key) if isinstance(api_key, str) else None
    if digest is None:
        raise IngestionError(
            401,
            "AUTHENTICATION_FAILED",
            "Missing or invalid Gateway API key",
            retryable=False,
        )
    return digest


def lookup_credential_by_hash(session, key_hash: bytes) -> dict | None:
    row = (
        session.execute(
            text(
                "SELECT c.credential_id, c.gateway_id, c.revoked_at, "
                "g.gateway_code, g.farm_id "
                "FROM dbo.GatewayCredential c "
                "JOIN dbo.Gateway g ON g.gateway_id = c.gateway_id "
                "WHERE c.key_hash = :digest"
            ),
            {"digest": key_hash},
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def authenticate_gateway(
    session, *, api_key: str | None, declared_gateway_code: str
) -> AuthenticatedGateway:
    """Authenticate Gateway X-API-Key and verify declared gateway_id per 03.1 D04."""
    digest = extract_api_key_digest(api_key)
    row = lookup_credential_by_hash(session, digest)
    if row is None:
        raise IngestionError(
            401,
            "AUTHENTICATION_FAILED",
            "Missing or invalid Gateway API key",
            retryable=False,
        )
    if row["revoked_at"] is not None:
        raise IngestionError(
            403,
            "GATEWAY_FORBIDDEN",
            "Gateway API key has been revoked",
            retryable=False,
        )
    if row["gateway_code"] != declared_gateway_code:
        raise IngestionError(
            403,
            "GATEWAY_FORBIDDEN",
            "Declared gateway_id does not match authenticated Gateway",
            retryable=False,
        )
    return AuthenticatedGateway(
        credential_id=int(row["credential_id"]),
        gateway_id=int(row["gateway_id"]),
        gateway_code=str(row["gateway_code"]),
        farm_id=int(row["farm_id"]),
        key_hash=digest,
    )


def recheck_credential_locked(session, auth: AuthenticatedGateway) -> None:
    """Recheck credential active status under the Farm -> Gateway lock guard (E06-03)."""
    row = devices_repo.get_credential(
        session,
        gateway_id=str(auth.gateway_id),
        credential_id=str(auth.credential_id),
        lock=True,
    )
    if row is None or row["revoked_at"] is not None:
        raise IngestionError(
            403,
            "GATEWAY_FORBIDDEN",
            "Gateway API key has been revoked",
            retryable=False,
        )
