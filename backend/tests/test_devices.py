import base64
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from app.core.config import Settings
from app.core.errors import WebError
from app.main import create_app
from app.modules.devices.context import read_context
from app.modules.devices.schemas import GatewayCreate, NodeCreate, ProfilePatch, identity, version
from app.modules.devices.service import DeviceService


@pytest.mark.parametrize("value", ["0", "-1", "01", "1.0", "9223372036854775808", "１"])
def test_invalid_ids(value):
    with pytest.raises(ValueError):
        identity(value)


@pytest.mark.parametrize("value", ["", "AAAAAAAAAAA", "AAAAAAAAAAA_", "AAAAAAAAAAB="])
def test_invalid_versions(value):
    with pytest.raises(ValueError):
        version(value)


def test_presence_unicode_and_strict_types():
    assert ProfilePatch(expected_row_version="AAAAAAAAAAA=").model_fields_set == {
        "expected_row_version"
    }
    assert ProfilePatch(notes=None, expected_row_version="AAAAAAAAAAA=").notes is None
    for fields in ({"name": None}, {"name": " "}, {"name": "😀" * 101}, {"zone_id": "1"}):
        with pytest.raises(ValidationError):
            ProfilePatch(**fields, expected_row_version="AAAAAAAAAAA=")
    with pytest.raises(ValidationError):
        NodeCreate(device_id=" X ", name="a", zone_id="1")
    with pytest.raises(ValidationError):
        NodeCreate(device_id="X", name="a", zone_id=1)
    assert NodeCreate(device_id="X", name=" a ", zone_id="1").name == "a"

    # GatewayCreate now requires explicit farm_id as decimal string.
    with pytest.raises(ValidationError):
        GatewayCreate(gateway_id="GW-1", name="GW")
    with pytest.raises(ValidationError):
        GatewayCreate(gateway_id="GW-1", name="GW", farm_id=1)
    assert GatewayCreate(gateway_id="GW-1", name="GW", farm_id="1").farm_id == "1"


def service():
    return DeviceService(
        SimpleNamespace(engine=None),
        Settings(_env_file=None, cursor_secret=SecretStr("test-cursor-secret")),
    )


def test_cursor_tamper():
    instance = service()
    payload = {"purpose": "profile-cursor", "boundary": "9223372036854775807"}
    token = instance.cursor(payload)
    assert instance.cursor(token=token) == payload
    with pytest.raises(WebError) as exc:
        instance.cursor(token=token + "x")
    assert exc.value.code == "CURSOR_INVALID"


def test_context_zero_one_multi_and_archived():
    class Result:
        def __init__(self, rows):
            self.rows = rows

        def mappings(self):
            return self

        def all(self):
            return self.rows

        def first(self):
            return self.rows[0] if self.rows else None

    class Session:
        def __init__(self, farms, zones):
            self.farms = farms
            self.zones = zones

        def execute(self, query, params=None):
            sql = str(query)
            if "FROM dbo.Farm WHERE archived_at IS NULL" in sql:
                return Result([f for f in self.farms if f["archived_at"] is None])
            if "FROM dbo.Farm WHERE farm_id = :id" in sql:
                return Result([f for f in self.farms if f["farm_id"] == params["id"]])
            if "FROM dbo.Zone" in sql:
                return Result(
                    [
                        z
                        for z in self.zones
                        if z["farm_id"] == params["farm_id"] and z["archived_at"] is None
                    ]
                )
            return Result([])

    # 0 active Farms
    empty_ctx = read_context(Session([], []))
    assert empty_ctx["farm"] is None
    assert empty_ctx["zones"] == []
    assert empty_ctx["configuration"] == {
        "state": "INVALID",
        "issues": [{"code": "AREA_NOT_CONFIGURED", "entity_type": "CONFIGURATION"}],
    }

    # 1 active Farm, 0 Zones -> VALID
    f1 = {
        "farm_id": 1,
        "farm_code": "F1",
        "name": "Farm 1",
        "description": None,
        "archived_at": None,
    }
    one_ctx = read_context(Session([f1], []))
    assert one_ctx["configuration"] == {"state": "VALID", "issues": []}
    assert one_ctx["farm"] == {"id": "1", "code": "F1", "name": "Farm 1", "description": None}
    assert "archived_at" not in one_ctx["farm"]

    # >1 active Farms without farm_id -> 409 AREA_SELECTION_REQUIRED
    f2 = {
        "farm_id": 2,
        "farm_code": "F2",
        "name": "Farm 2",
        "description": None,
        "archived_at": None,
    }
    with pytest.raises(WebError) as exc:
        read_context(Session([f1, f2], []))
    assert exc.value.status == 409
    assert exc.value.code == "AREA_SELECTION_REQUIRED"

    # Explicit archived farm_id -> 200 INVALID / AREA_ARCHIVED
    f1_arch = {**f1, "archived_at": datetime(2026, 11, 18, 12, 0, 0)}
    arch_ctx = read_context(Session([f1_arch], []), farm_id="1")
    assert arch_ctx["zones"] == []
    assert arch_ctx["configuration"] == {
        "state": "INVALID",
        "issues": [{"code": "AREA_ARCHIVED", "entity_type": "FARM", "id": "1"}],
    }

    # Unknown farm_id -> 404
    with pytest.raises(WebError) as exc404:
        read_context(Session([f1], []), farm_id="99")
    assert exc404.value.status == 404


@pytest.mark.parametrize("path", ["/context", "/nodes", "/gateways", "/nodes/1", "/gateways/1"])
def test_protected_no_store(path):
    app = create_app(Settings(_env_file=None))
    with TestClient(app) as client:
        response = client.get("/api/v1" + path)
        assert response.status_code == 401
        assert response.headers["cache-control"] == "no-store"


def test_route_shape():
    with TestClient(create_app(Settings(_env_file=None))) as client:
        for path in (
            "/context?x=1",
            "/context?farm_id=abc",
            "/context?farm_id=1&farm_id=2",
            "/nodes?page_size=0",
            "/nodes?page_size=1&page_size=2",
            "/nodes?gateway_id=X",
            "/nodes/01",
        ):
            assert client.get("/api/v1" + path).status_code == 400
        assert client.request("GET", "/api/v1/context", json={}).status_code == 400


def test_patch_stale_before_noop(monkeypatch):
    instance = service()
    row = {"row_ver": b"12345678", "name": "same", "notes": None}

    @contextmanager
    def fake_tx(*args, **kwargs):
        yield "fake-session"

    monkeypatch.setattr("app.modules.devices.service.web_transaction", fake_tx)
    monkeypatch.setattr("app.modules.devices.repository.resolve_gateway_farm_id", lambda s, gid: 1)
    monkeypatch.setattr(
        "app.modules.areas.repository.get_farm", lambda s, fid, lock=False: {"farm_id": 1}
    )
    monkeypatch.setattr("app.modules.devices.repository.lock_gateway", lambda s, gid: row)

    body = ProfilePatch(name="same", expected_row_version=base64.b64encode(b"abcdefgh").decode())
    with pytest.raises(WebError) as exc:
        instance.patch(None, None, "gateways", "1", body)
    assert exc.value.code == "VERSION_CONFLICT"


def test_patch_node_lock_order(monkeypatch):
    instance = service()
    locks = []
    row = {"row_ver": b"12345678", "name": "Node 1", "notes": None}

    @contextmanager
    def fake_tx(*args, **kwargs):
        yield "fake-session"

    monkeypatch.setattr("app.modules.devices.service.web_transaction", fake_tx)
    monkeypatch.setattr(
        "app.modules.devices.repository.resolve_node_parents",
        lambda s, nid: {"node_id": 5, "zone_id": 3, "farm_id": 1, "gateway_id": 2},
    )
    monkeypatch.setattr(
        "app.modules.areas.repository.get_farm",
        lambda s, fid, lock=False: locks.append(("farm", int(fid), lock)) or {"farm_id": 1},
    )
    monkeypatch.setattr(
        "app.modules.areas.repository.get_zone",
        lambda s, zid, lock=False: locks.append(("zone", int(zid), lock)) or {"zone_id": 3},
    )
    monkeypatch.setattr(
        "app.modules.devices.repository.lock_gateway",
        lambda s, gid: locks.append(("gateway", int(gid), True)) or {"gateway_id": 2},
    )
    monkeypatch.setattr(
        "app.modules.devices.repository.lock_node",
        lambda s, nid: locks.append(("node", int(nid), True)) or row,
    )
    monkeypatch.setattr("app.modules.devices.repository.profile", lambda *a: {"id": "5"})

    body = ProfilePatch(name="Node 1", expected_row_version=base64.b64encode(b"12345678").decode())
    instance.patch(None, None, "nodes", "5", body)
    assert locks == [
        ("farm", 1, True),
        ("zone", 3, True),
        ("gateway", 2, True),
        ("node", 5, True),
    ]


@pytest.mark.parametrize("failure", [False, True])
def test_create_node_state_and_fault_transaction(monkeypatch, failure):
    instance = service()
    statements = []
    outcome = []

    class Result:
        def scalar_one(self):
            return 101

    class FakeSession:
        def execute(self, query, params):
            sql = str(query)
            statements.append(sql)
            if failure and "EvaluationState" in sql:
                raise RuntimeError("injected state failure")
            return Result()

    @contextmanager
    def fake_tx(*args, **kwargs):
        try:
            yield FakeSession()
        except Exception:
            outcome.append("rollback")
            raise
        else:
            outcome.append("commit")

    monkeypatch.setattr("app.modules.devices.service.web_transaction", fake_tx)
    monkeypatch.setattr("app.modules.areas.repository.get_zone_farm_id", lambda s, zid: 1)
    monkeypatch.setattr(
        "app.modules.areas.repository.get_farm",
        lambda s, fid, lock=False: {"farm_id": 1, "archived_at": None},
    )
    monkeypatch.setattr(
        "app.modules.areas.repository.get_zone",
        lambda s, zid, lock=False: {"zone_id": 1, "farm_id": 1, "archived_at": None},
    )
    monkeypatch.setattr("app.modules.devices.repository.profile", lambda *a: {"id": "101"})
    body = NodeCreate(device_id="N", name="Node", zone_id="1")
    if failure:
        with pytest.raises(RuntimeError):
            instance.create(None, None, "nodes", body)
    else:
        assert instance.create(None, None, "nodes", body) == {"id": "101"}
    assert len(statements) == 3
    assert "NodeRuntime" in statements[1]
    assert "VALUES (:id,0,0,0)" in statements[2]
    assert outcome == ["rollback" if failure else "commit"]


def test_create_rejects_archived_parent(monkeypatch):
    instance = service()

    @contextmanager
    def fake_tx(*args, **kwargs):
        yield "fake-session"

    monkeypatch.setattr("app.modules.devices.service.web_transaction", fake_tx)
    monkeypatch.setattr(
        "app.modules.areas.repository.get_farm",
        lambda s, fid, lock=False: {"farm_id": 1, "archived_at": datetime(2026, 11, 18)},
    )
    with pytest.raises(WebError) as exc:
        instance.create(
            None, None, "gateways", GatewayCreate(gateway_id="GW1", name="GW 1", farm_id="1")
        )
    assert exc.value.status == 409
    assert exc.value.code == "AREA_ARCHIVED"


def test_noop_keeps_version_without_update(monkeypatch):
    instance = service()
    calls = []
    row = {"row_ver": b"12345678", "name": "same", "notes": None}

    class FakeSession:
        def execute(self, query, params):
            calls.append(str(query))

    @contextmanager
    def fake_tx(*args, **kwargs):
        yield FakeSession()

    monkeypatch.setattr("app.modules.devices.service.web_transaction", fake_tx)
    monkeypatch.setattr("app.modules.devices.repository.resolve_gateway_farm_id", lambda s, gid: 1)
    monkeypatch.setattr(
        "app.modules.areas.repository.get_farm", lambda s, fid, lock=False: {"farm_id": 1}
    )
    monkeypatch.setattr("app.modules.devices.repository.lock_gateway", lambda s, gid: row)
    monkeypatch.setattr("app.modules.devices.repository.profile", lambda *a: {"id": "1"})
    body = ProfilePatch(name="same", expected_row_version=base64.b64encode(b"12345678").decode())
    instance.patch(None, None, "gateways", "1", body)
    assert not any("UPDATE dbo." in sql for sql in calls)
