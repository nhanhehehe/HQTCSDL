import copy
import hashlib
from contextlib import contextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.core.config import Settings
from app.main import create_app
from app.modules.auth import repository as repo
from app.modules.auth.security import (
    csrf,
    decode,
    encode,
    password_hasher,
    token_hash,
    verify_password,
)

ORIGIN = "http://127.0.0.1:5173"
HEADERS = {"Origin": ORIGIN, "X-HQ-Client": "web"}
PASSWORD = "  private-password  "
NOW = datetime(2026, 11, 16, 0, 0, 0)


class MemorySessions:
    """Transaction double: rollback on failure, never evidence of SQL lock behavior."""

    def __init__(self):
        self.rows = {}
        self.now = NOW
        self.commit_failure = False
        self.inside = False
        self.account = {
            "admin_id": 9223372036854775807,
            "username": "Admin",
            "password_hash": password_hasher.hash(PASSWORD),
            "role_code": "ADMIN",
            "is_enabled": True,
        }

    @contextmanager
    def __call__(self):
        yield self

    @contextmanager
    def begin(self):
        old = copy.deepcopy(self.rows)
        self.inside = True
        try:
            yield self
            if self.commit_failure:
                raise OperationalError(None, None, Exception("PRIVATE_SQL_SECRET"))
        except BaseException:
            self.rows = old
            raise
        finally:
            self.inside = False


@pytest.fixture
def auth(monkeypatch):
    store = MemorySessions()

    def account(session, username, *, lock=False):
        assert lock == store.inside
        return dict(store.account) if username == store.account["username"] else None

    def guard(session, digest, *, lock=False):
        row = store.rows.get(digest)
        if (
            row
            and row["revoked_at"] is None
            and store.now < row["expires_at"]
            and store.account["is_enabled"]
            and store.account["role_code"] == "ADMIN"
        ):
            return {**row, "username": store.account["username"], "db_now": store.now}
        return None

    def create(session, admin_id, digest):
        assert store.inside
        row = {
            "session_id": len(store.rows) + 1,
            "admin_id": admin_id,
            "expires_at": store.now + timedelta(hours=2),
            "db_now": store.now,
            "revoked_at": None,
        }
        store.rows[digest] = row
        return row

    def revoke(session, digest):
        assert store.inside
        if store.rows[digest]["revoked_at"] is None:
            store.rows[digest]["revoked_at"] = store.now

    monkeypatch.setattr(repo, "account", account)
    monkeypatch.setattr(repo, "session_guard", guard)
    monkeypatch.setattr(repo, "create_session", create)
    monkeypatch.setattr(repo, "revoke", revoke)
    app = create_app(Settings(_env_file=None, csrf_secret="test-only-secret"))
    app.state.auth.database = SimpleNamespace(sessions=store)
    with TestClient(app) as client:
        yield client, store


def login(client, **overrides):
    return client.post(
        "/api/v1/auth/login",
        headers=HEADERS,
        json={"username": "Admin", "password": PASSWORD, **overrides},
    )


def test_login_session_contract_and_rotation(auth):
    client, store = auth
    first = login(client)
    assert first.status_code == 200
    body = first.json()
    assert body["admin"] == {"id": "9223372036854775807", "username": "Admin"}
    assert body["expires_at"] == "2026-11-16T02:00:00.000Z"
    cookie = first.cookies["hq_session"]
    digest = hashlib.sha256(decode(cookie)).digest()
    assert digest in store.rows
    assert body["csrf_token"] == csrf("test-only-secret", digest)
    header = first.headers["set-cookie"]
    for flag in ("HttpOnly", "SameSite=strict", "Path=/api"):
        assert flag in header
    assert "Domain=" not in header and "Secure" not in header
    assert int(header.split("Max-Age=")[1].split(";")[0]) <= 7200
    assert client.get("/api/v1/auth/session").json() == body
    # An unrelated browser session remains valid when this browser rotates its token.
    client.cookies.clear()
    other = login(client).cookies["hq_session"]
    client.cookies.set("hq_session", cookie, path="/api")
    assert login(client).status_code == 200
    assert store.rows[digest]["revoked_at"] == NOW
    assert store.rows[token_hash(other)]["revoked_at"] is None


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"username": "Admin"},
        {"username": "", "password": "x"},
        {"username": 1, "password": "x"},
        {"username": "Admin", "password": True},
        {"username": "Admin", "password": "x", "extra": 1},
        {"username": "Admin", "password": "x" * 1025},
    ],
)
def test_strict_input_never_creates_session(auth, payload):
    client, store = auth
    response = client.post("/api/v1/auth/login", headers=HEADERS, json=payload)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INPUT_INVALID"
    assert not store.rows


@pytest.mark.parametrize(
    "changes",
    [
        {"username": "admin"},
        {"username": "Admin "},
        {"password": PASSWORD.strip()},
    ],
)
def test_wrong_credentials_common_error(auth, changes):
    client, store = auth
    response = login(client, **changes)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_CREDENTIALS"
    assert not store.rows


@pytest.mark.parametrize("field,value", [("is_enabled", False), ("role_code", "OTHER")])
def test_disabled_or_wrong_role(auth, field, value):
    client, store = auth
    store.account[field] = value
    assert login(client).json()["error"]["code"] == "INVALID_CREDENTIALS"
    assert not store.rows


def test_account_hash_changed_after_verification(auth, monkeypatch):
    from app.modules.auth import service

    client, store = auth
    original = service.verify_password

    def verify(encoded, password):
        assert not store.inside
        result = original(encoded, password)
        store.account["password_hash"] = password_hasher.hash("changed")
        return result

    monkeypatch.setattr(service, "verify_password", verify)
    assert login(client).status_code == 401
    assert not store.rows


def test_origin_client_and_content_type_guards(auth):
    client, store = auth
    for headers in ({}, {**HEADERS, "Origin": "http://evil.test"}, {"Origin": ORIGIN}):
        assert client.post("/api/v1/auth/login", headers=headers, json={}).status_code == 403
    assert client.post("/api/v1/auth/login", headers=HEADERS, content="{}").status_code == 400
    assert client.post("/api/v1/auth/logout").status_code == 403
    assert not store.rows


def test_logout_guard_idempotency_and_exact_expiry(auth):
    client, store = auth
    response = login(client)
    token = response.cookies["hq_session"]
    digest = token_hash(token)
    assert client.post("/api/v1/auth/logout", headers={"Origin": ORIGIN}).status_code == 403
    assert store.rows[digest]["revoked_at"] is None
    headers = {"Origin": ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]}
    result = client.post("/api/v1/auth/logout", headers=headers)
    assert result.status_code == 204 and result.content == b""
    assert "Max-Age=0" in result.headers["set-cookie"]
    revoked = store.rows[digest]["revoked_at"]
    client.cookies.set("hq_session", token, path="/api")
    store.now += timedelta(minutes=1)
    assert client.post("/api/v1/auth/logout", headers={"Origin": ORIGIN}).status_code == 204
    assert store.rows[digest]["revoked_at"] == revoked
    client.cookies.clear()
    response = login(client)
    expires = next(row["expires_at"] for row in store.rows.values() if row["revoked_at"] is None)
    store.now = expires - timedelta(milliseconds=1)
    assert client.get("/api/v1/auth/session").status_code == 200
    store.now = expires
    assert client.get("/api/v1/auth/session").status_code == 401
    assert client.post("/api/v1/auth/logout", headers={"Origin": ORIGIN}).status_code == 204


@pytest.mark.parametrize("token", [None, "bad", "=" * 43, encode(b"x" * 32)[:-1] + "B"])
def test_malformed_cookie_and_missing_session(auth, token):
    client, _ = auth
    if token is not None:
        client.cookies.set("hq_session", token, path="/api")
    assert client.get("/api/v1/auth/session").status_code == 401
    assert client.post("/api/v1/auth/logout", headers={"Origin": ORIGIN}).status_code == 204


def test_storage_failure_rolls_back_rotation_and_has_no_cookie(auth):
    client, store = auth
    previous = login(client).cookies["hq_session"]
    store.commit_failure = True
    response = login(client)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "STORAGE_UNAVAILABLE"
    assert "set-cookie" not in response.headers
    assert store.rows[token_hash(previous)]["revoked_at"] is None
    assert len(store.rows) == 1
    assert "PRIVATE_SQL_SECRET" not in response.text


def test_all_auth_responses_no_store_and_no_secret(auth):
    client, _ = auth
    for response in (
        login(client),
        login(client, password="wrong"),
        client.get("/api/v1/auth/session"),
        client.post("/api/v1/auth/logout", headers={"Origin": ORIGIN}),
    ):
        assert response.headers["cache-control"] == "no-store"
        assert "x-request-id" in response.headers
        assert PASSWORD not in response.text and "password_hash" not in response.text


def test_password_profile_and_csrf_vector():
    encoded = password_hasher.hash(PASSWORD)
    assert "$argon2id$v=19$m=19456,t=2,p=1$" in encoded
    assert verify_password(encoded, PASSWORD)
    assert not verify_password("malformed", PASSWORD)
    assert decode(encode(bytes(range(32)))) == bytes(range(32))
    assert token_hash(encode(bytes(range(32)))) == hashlib.sha256(bytes(range(32))).digest()


def test_settings_reuses_auth_variables_without_exposing_secret():
    settings = Settings(_env_file=None, csrf_secret="PRIVATE", auth_test_database_url=None)
    assert "PRIVATE" not in repr(settings)
    with pytest.raises(ValueError):
        Settings(_env_file=None, web_origin=ORIGIN + "/")
