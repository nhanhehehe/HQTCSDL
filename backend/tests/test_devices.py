import base64
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from app.core.config import Settings
from app.core.errors import WebError
from app.main import create_app
from app.modules.devices.context import parse_manifest, read_context
from app.modules.devices.schemas import NodeCreate, ProfilePatch, identity, version
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


def service():
    return DeviceService(
        SimpleNamespace(engine=None),
        Settings(
            _env_file=None, cursor_secret=SecretStr("test-cursor-secret"), area_manifest="broken"
        ),
    )


def test_cursor_tamper_and_configuration():
    instance = service()
    payload = {"purpose": "profile-cursor", "boundary": "9223372036854775807"}
    token = instance.cursor(payload)
    assert instance.cursor(token=token) == payload
    with pytest.raises(WebError) as exc:
        instance.cursor(token=token + "x")
    assert exc.value.code == "CURSOR_INVALID"
    assert instance.manifest is None
    assert read_context(None, None)["configuration"]["issues"][0]["code"] == "AREA_MANIFEST_INVALID"


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "{}",
        "[]",
        "1",
        '{"farm_code":1}',
        '{"farm_code":"F","zone_codes":{"indoor":"A","outdoor":"A"}}',
    ],
)
def test_manifest_invalid_is_not_startup_failure(raw):
    assert parse_manifest(raw) is None


def test_context_partial_and_order():
    manifest = parse_manifest('{"farm_code":"F","zone_codes":{"indoor":"I","outdoor":"O"}}')

    class Result:
        def __init__(self, rows):
            self.rows = rows

        def mappings(self):
            return self

        def all(self):
            return self.rows

    class Session:
        def execute(self, query, params):
            code = params["code"]
            return Result(
                []
                if code == "O"
                else [
                    dict(
                        id=1,
                        code=code,
                        name="Name",
                        description=None,
                        **({"farm_id": 1} if code == "I" else {}),
                    )
                ]
            )

    value = read_context(Session(), manifest)
    assert value["farm"]["id"] == "1"
    assert [z["code"] for z in value["zones"]] == ["I"]
    assert value["configuration"]["issues"][0]["code"] == "ZONE_NOT_FOUND"


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

    class Result:
        def mappings(self):
            return self

        def first(self):
            return row

    class Session:
        def execute(self, query, params):
            return Result()

    @contextmanager
    def transaction(*args, **kwargs):
        yield Session()

    monkeypatch.setattr(instance, "transaction", transaction)
    body = ProfilePatch(name="same", expected_row_version=base64.b64encode(b"abcdefgh").decode())
    with pytest.raises(WebError) as exc:
        instance.patch(None, None, "gateways", "1", body)
    assert exc.value.code == "VERSION_CONFLICT"


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
    def transaction(*args, **kwargs):
        try:
            yield FakeSession()
        except Exception:
            outcome.append("rollback")
            raise
        else:
            outcome.append("commit")

    monkeypatch.setattr(instance, "transaction", transaction)
    monkeypatch.setattr(
        "app.modules.devices.service.read_context",
        lambda *a, **kw: {"zones": [{"id": "1"}], "farm": {"id": "1"}},
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


def test_noop_keeps_version_without_update(monkeypatch):
    instance = service()
    calls = []
    row = {"row_ver": b"12345678", "name": "same", "notes": None}

    class Result:
        def mappings(self):
            return self

        def first(self):
            return row

    class FakeSession:
        def execute(self, query, params):
            calls.append(str(query))
            return Result()

    @contextmanager
    def transaction(*args, **kwargs):
        yield FakeSession()

    monkeypatch.setattr(instance, "transaction", transaction)
    monkeypatch.setattr("app.modules.devices.repository.profile", lambda *a: {"id": "1"})
    body = ProfilePatch(name="same", expected_row_version=base64.b64encode(b"12345678").decode())
    instance.patch(None, None, "gateways", "1", body)
    assert not any("UPDATE dbo." in sql for sql in calls)
