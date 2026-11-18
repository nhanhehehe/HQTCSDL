import base64
import hmac
import json
from contextlib import contextmanager
from datetime import UTC, datetime

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.errors import WebError
from app.modules.auth import repository as auth_repo
from app.modules.auth.router import origin_guard
from app.modules.auth.security import token_hash, valid_csrf
from app.modules.auth.service import invalid_session, storage_error


def fail(status: int, code: str, message: str | None = None) -> None:
    raise WebError(status, code, message or code)


def serialize_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def check_request_shape(request: Request, allowed: set[str], body: bool = False) -> None:
    keys = list(request.query_params.keys())
    if any(k not in allowed or len(request.query_params.getlist(k)) != 1 for k in keys):
        raise WebError(400, "INPUT_INVALID", "Tham số không hợp lệ")
    if not body and (
        request.headers.get("content-length", "0") != "0"
        or request.headers.get("transfer-encoding")
    ):
        raise WebError(400, "INPUT_INVALID", "Không nhận request body")


def require_json_mutation(request: Request) -> None:
    check_request_shape(request, set(), body=True)
    origin_guard(request)
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
        raise WebError(400, "INPUT_INVALID", "Yêu cầu phải là JSON")


def checked_value(function, value):
    try:
        return function(value)
    except (ValueError, TypeError):
        raise WebError(400, "INPUT_INVALID", "Dữ liệu không hợp lệ") from None


def parse_page_size(request: Request, *, default: int = 50, maximum: int = 200) -> int:
    raw = request.query_params.get("page_size", str(default))
    if not raw.isascii() or not raw.isdecimal() or not 1 <= int(raw) <= maximum:
        raise WebError(400, "INPUT_INVALID", "page_size không hợp lệ")
    return int(raw)


@contextmanager
def web_transaction(
    database, settings, cookie: str | None, csrf: str | None = None, write: bool = False
):
    """Open a SNAPSHOT read or READ COMMITTED write transaction guarded by AdminSession."""
    digest = token_hash(cookie)
    if digest is None:
        raise invalid_session()
    if database.engine is None:
        raise storage_error()
    try:
        with (
            database.engine.connect().execution_options(
                isolation_level="READ COMMITTED" if write else "SNAPSHOT"
            ) as connection,
            connection.begin(),
            Session(bind=connection) as session,
        ):
            if write:
                session.execute(text("SET XACT_ABORT ON"))
            if auth_repo.session_guard(session, digest, lock=write) is None:
                raise invalid_session()
            if write and (
                settings.csrf_secret is None
                or not valid_csrf(settings.csrf_secret.get_secret_value(), digest, csrf)
            ):
                fail(403, "CSRF_INVALID")
            yield session
    except SQLAlchemyError:
        raise storage_error() from None


def _b64u_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def sign_cursor(settings, payload: dict) -> str:
    secret = settings.cursor_secret
    if secret is None or not secret.get_secret_value():
        raise storage_error()
    key = secret.get_secret_value().encode()
    body = _b64u_encode(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    )
    prefix = "v1." + body
    return prefix + "." + _b64u_encode(hmac.digest(key, prefix.encode(), "sha256"))


def verify_cursor(settings, token: str) -> dict:
    secret = settings.cursor_secret
    if secret is None or not secret.get_secret_value():
        raise storage_error()
    key = secret.get_secret_value().encode()
    try:
        if len(token) > 4096:
            raise ValueError()
        v, body, signature = token.split(".")
        expected = _b64u_encode(hmac.digest(key, (v + "." + body).encode(), "sha256"))
        if v != "v1" or not hmac.compare_digest(signature, expected):
            raise ValueError()
        raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        if _b64u_encode(raw) != body:
            raise ValueError()
        decoded = json.loads(raw)
        if not isinstance(decoded, dict):
            raise ValueError()
        return decoded
    except (ValueError, TypeError, UnicodeError):
        fail(400, "CURSOR_INVALID")
