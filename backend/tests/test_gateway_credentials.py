import base64
import hashlib
from contextlib import contextmanager
from datetime import datetime

from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import Settings
from app.main import create_app
from app.modules.devices.credentials import (
    KEY_PREFIX,
    KEY_TOTAL_LEN,
    generate_gateway_key,
    parse_gateway_key_and_digest,
    verify_gateway_key,
)


def _settings():
    return Settings(
        _env_file=None,
        csrf_secret=SecretStr("test-csrf-secret"),
        cursor_secret=SecretStr("test-cursor-secret"),
        web_origin="http://127.0.0.1:5173",
    )


def _headers(csrf="valid-csrf"):
    return {
        "Origin": "http://127.0.0.1:5173",
        "X-HQ-Client": "web",
        "X-CSRF-Token": csrf,
    }


def test_key_generation_and_canonical_parser():
    wire, digest = generate_gateway_key()
    assert wire.startswith(KEY_PREFIX)
    assert len(wire) == KEY_TOTAL_LEN
    assert "=" not in wire
    raw = base64.urlsafe_b64decode(wire[len(KEY_PREFIX) :] + "=")
    assert len(raw) == 32
    assert digest == hashlib.sha256(raw).digest()
    assert digest != hashlib.sha256(wire.encode("ascii")).digest()
    assert parse_gateway_key_and_digest(wire) == digest

    # Non-canonical trailing bits in 43rd base64url char (256 bits = 42*6 + 4, so the bottom
    # 2 bits of char 43 must be 0).
    bad_tail = wire[:-1] + ("B" if wire[-1] != "B" else "C")
    assert parse_gateway_key_and_digest(bad_tail) is None

    for bad in ("", "hqgw_v2_" + wire[len(KEY_PREFIX) :], wire + "=", wire[:-1], 123):
        assert parse_gateway_key_and_digest(bad) is None


def test_verify_gateway_key_states():
    wire_active, digest_active = generate_gateway_key()
    wire_revoked, digest_revoked = generate_gateway_key()
    wire_unknown, _ = generate_gateway_key()

    rows = {
        digest_active: {
            "credential_id": 1,
            "gateway_id": 10,
            "revoked_at": None,
            "gateway_code": "GW-01",
        },
        digest_revoked: {
            "credential_id": 2,
            "gateway_id": 10,
            "revoked_at": datetime(2026, 11, 18, 12, 0, 0),
            "gateway_code": "GW-01",
        },
    }

    class Result:
        def __init__(self, row):
            self.row = row

        def mappings(self):
            return self

        def first(self):
            return self.row

    class Session:
        def execute(self, query, params):
            return Result(rows.get(params["digest"]))

    s = Session()
    assert verify_gateway_key(s, gateway_code="GW-01", api_key="bad") == (
        False,
        401,
        "INVALID_API_KEY",
    )
    assert verify_gateway_key(s, gateway_code="GW-01", api_key=wire_unknown) == (
        False,
        401,
        "INVALID_API_KEY",
    )
    assert verify_gateway_key(s, gateway_code="GW-01", api_key=wire_revoked) == (
        False,
        403,
        "GATEWAY_FORBIDDEN",
    )
    assert verify_gateway_key(s, gateway_code="gw-01", api_key=wire_active) == (
        False,
        403,
        "GATEWAY_FORBIDDEN",
    )
    assert verify_gateway_key(s, gateway_code="GW-01", api_key=wire_active) == (True, 200, "10")


def _client_with_fake_credentials(monkeypatch):
    app = create_app(_settings())
    state = {
        "farms": {1: {"farm_id": 1, "archived_at": None}},
        "gateways": {
            10: {
                "gateway_id": 10,
                "farm_id": 1,
                "gateway_code": "GW-10",
                "receiving_enabled": False,
            },
            20: {
                "gateway_id": 20,
                "farm_id": 1,
                "gateway_code": "GW-20",
                "receiving_enabled": True,
            },
        },
        "credentials": {},
        "next_cid": 1,
        "locks": [],
    }

    @contextmanager
    def fake_tx(database, settings, cookie, csrf=None, write=False):
        if cookie != "valid-cookie":
            from app.modules.auth.service import invalid_session

            raise invalid_session()
        if write and csrf != "valid-csrf":
            from app.core.errors import WebError

            raise WebError(403, "CSRF_INVALID", "CSRF_INVALID")
        yield state

    monkeypatch.setattr("app.modules.devices.service.web_transaction", fake_tx)
    monkeypatch.setattr(
        "app.modules.devices.repository.resolve_gateway_farm_id",
        lambda s, gid: s["gateways"][int(gid)]["farm_id"] if int(gid) in s["gateways"] else None,
    )
    monkeypatch.setattr(
        "app.modules.areas.repository.get_farm",
        lambda s, fid, lock=False: (
            s["locks"].append(("farm", int(fid), lock)) or s["farms"].get(int(fid))
        ),
    )
    monkeypatch.setattr(
        "app.modules.devices.repository.lock_gateway",
        lambda s, gid: (
            s["locks"].append(("gateway", int(gid), True)) or s["gateways"].get(int(gid))
        ),
    )

    def fake_list(s, *, gateway_id, boundary, limit):
        rows = [
            c
            for cid, c in sorted(s["credentials"].items(), reverse=True)
            if c["gateway_id"] == int(gateway_id) and (boundary is None or cid < boundary)
        ]
        return rows[:limit]

    def fake_get(s, *, gateway_id, credential_id, lock=False):
        c = s["credentials"].get(int(credential_id))
        if c is None or c["gateway_id"] != int(gateway_id):
            return None
        return c

    def fake_count(s, gateway_id):
        return sum(
            1
            for c in s["credentials"].values()
            if c["gateway_id"] == int(gateway_id) and c["revoked_at"] is None
        )

    def fake_insert(s, *, gateway_id, key_hash):
        cid = s["next_cid"]
        s["next_cid"] += 1
        row = {
            "credential_id": cid,
            "gateway_id": int(gateway_id),
            "key_hash": key_hash,
            "created_at": datetime(2026, 11, 18, 12, 0, cid),
            "revoked_at": None,
        }
        s["credentials"][cid] = row
        return row

    def fake_revoke(s, *, gateway_id, credential_id):
        c = s["credentials"].get(int(credential_id))
        if c is None or c["gateway_id"] != int(gateway_id) or c["revoked_at"] is not None:
            return None
        c["revoked_at"] = datetime(2026, 11, 18, 13, 0, int(credential_id))
        return c

    monkeypatch.setattr("app.modules.devices.repository.list_credentials", fake_list)
    monkeypatch.setattr("app.modules.devices.repository.get_credential", fake_get)
    monkeypatch.setattr("app.modules.devices.repository.count_active_credentials", fake_count)
    monkeypatch.setattr("app.modules.devices.repository.insert_credential", fake_insert)
    monkeypatch.setattr("app.modules.devices.repository.revoke_credential", fake_revoke)
    return app, state


def test_credential_issue_quota_revoke_and_security(monkeypatch):
    app, state = _client_with_fake_credentials(monkeypatch)
    with TestClient(app) as client:
        # Unauthenticated rejected
        assert client.get("/api/v1/gateways/10/credentials").status_code == 401
        client.cookies.set("hq_session", "valid-cookie")

        # Extra body field rejected
        assert (
            client.post(
                "/api/v1/gateways/10/credentials",
                json={"api_key": "custom"},
                headers=_headers(),
            ).status_code
            == 400
        )

        # Issue #1 (allowed even when receiving_enabled=False if Farm active)
        r1 = client.post("/api/v1/gateways/10/credentials", json={}, headers=_headers())
        assert r1.status_code == 201
        assert r1.headers["cache-control"] == "no-store"
        d1 = r1.json()
        assert d1["api_key"].startswith("hqgw_v1_")
        assert len(d1["api_key"]) == 51
        assert d1["credential"]["status"] == "ACTIVE"
        assert r1.headers["location"] == f"/api/v1/gateways/10/credentials/{d1['credential']['id']}"
        assert state["locks"] == [("farm", 1, True), ("gateway", 10, True)]

        # Issue #2
        r2 = client.post("/api/v1/gateways/10/credentials", json={}, headers=_headers())
        assert r2.status_code == 201
        d2 = r2.json()

        # Issue #3 exceeds quota -> 409 CREDENTIAL_LIMIT_REACHED
        r3 = client.post("/api/v1/gateways/10/credentials", json={}, headers=_headers())
        assert r3.status_code == 409
        assert r3.json()["error"]["code"] == "CREDENTIAL_LIMIT_REACHED"

        # List ordered DESC, no secret/hash leaked, pagination works
        page1 = client.get("/api/v1/gateways/10/credentials?page_size=1")
        assert page1.status_code == 200
        j1 = page1.json()
        assert [x["id"] for x in j1["items"]] == [d2["credential"]["id"]]
        assert set(j1["items"][0].keys()) == {
            "id",
            "gateway_id",
            "created_at",
            "revoked_at",
            "status",
        }
        page2 = client.get(
            f"/api/v1/gateways/10/credentials?page_size=1&cursor={j1['next_cursor']}"
        )
        assert [x["id"] for x in page2.json()["items"]] == [d1["credential"]["id"]]

        # Cross-gateway detail returns 404
        assert (
            client.get(f"/api/v1/gateways/20/credentials/{d1['credential']['id']}").status_code
            == 404
        )

        # Revoke #1, idempotent repeat preserves revoked_at
        rev1 = client.post(
            f"/api/v1/gateways/10/credentials/{d1['credential']['id']}/revoke",
            json={},
            headers=_headers(),
        )
        assert rev1.status_code == 200
        assert rev1.json()["status"] == "REVOKED"
        ts = rev1.json()["revoked_at"]
        assert ts is not None

        # Archive Farm -> issue blocked (409 AREA_ARCHIVED), revoke still allowed
        state["farms"][1]["archived_at"] = datetime(2026, 11, 18, 14, 0, 0)
        r_arch_issue = client.post("/api/v1/gateways/10/credentials", json={}, headers=_headers())
        assert r_arch_issue.status_code == 409
        assert r_arch_issue.json()["error"]["code"] == "AREA_ARCHIVED"

        rev1_repeat = client.post(
            f"/api/v1/gateways/10/credentials/{d1['credential']['id']}/revoke",
            json={},
            headers=_headers(),
        )
        assert rev1_repeat.status_code == 200
        assert rev1_repeat.json()["revoked_at"] == ts
