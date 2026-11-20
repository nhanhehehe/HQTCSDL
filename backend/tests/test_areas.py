import base64
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from app.core.config import Settings
from app.main import create_app
from app.modules.areas.schemas import AreaCreate, AreaPatch, area_code
from app.modules.areas.service import AreaService


def _settings():
    return Settings(
        _env_file=None,
        csrf_secret=SecretStr("test-csrf-secret"),
        cursor_secret=SecretStr("test-cursor-secret"),
        web_origin="http://127.0.0.1:5173",
    )


def _headers(csrf="csrf-token"):
    return {
        "Origin": "http://127.0.0.1:5173",
        "X-HQ-Client": "web",
        "X-CSRF-Token": csrf,
    }


def _client_with_fake_area_tx(monkeypatch):
    app = create_app(_settings())
    state = {
        "farms": {},
        "zones": {},
        "gateways": {},
        "nodes": {},
        "next_id": 1,
        "ver": 1,
    }

    def next_ver():
        v = state["ver"]
        state["ver"] += 1
        return v.to_bytes(8, "big")

    @contextmanager
    def fake_tx(database, settings, cookie, csrf=None, write=False):
        if cookie != "valid-cookie":
            from app.modules.auth.service import invalid_session

            raise invalid_session()
        if write and csrf != "valid-csrf":
            from app.core.errors import WebError

            raise WebError(403, "CSRF_INVALID", "CSRF_INVALID")
        yield state

    monkeypatch.setattr("app.modules.areas.service.web_transaction", fake_tx)

    def fake_list_farms(session, *, status, code, boundary, limit):
        items = []
        for fid in sorted(session["farms"]):
            row = session["farms"][fid]
            if fid <= boundary:
                continue
            if status == "ACTIVE" and row["archived_at"] is not None:
                continue
            if status == "ARCHIVED" and row["archived_at"] is None:
                continue
            if code is not None and row["farm_code"] != code:
                continue
            items.append(row)
        return items[:limit]

    def fake_get_farm(session, farm_id, *, lock=False):
        return session["farms"].get(int(farm_id))

    def fake_insert_farm(session, *, code, name, description):
        for r in session["farms"].values():
            if r["farm_code"] == code:
                from app.core.errors import WebError

                raise WebError(409, "CODE_EXISTS", "CODE_EXISTS")
        fid = session["next_id"]
        session["next_id"] += 1
        row = {
            "farm_id": fid,
            "farm_code": code,
            "name": name,
            "description": description,
            "archived_at": None,
            "row_ver": next_ver(),
        }
        session["farms"][fid] = row
        return row

    def fake_update_farm_metadata(session, *, farm_id, expected_version, changes):
        row = session["farms"].get(int(farm_id))
        if row is None or bytes(row["row_ver"]) != expected_version:
            return None
        row.update(changes)
        row["row_ver"] = next_ver()
        return row

    def fake_list_zones(session, *, farm_id, status, code, boundary, limit):
        items = []
        for zid in sorted(session["zones"]):
            row = session["zones"][zid]
            if row["farm_id"] != int(farm_id) or zid <= boundary:
                continue
            if status == "ACTIVE" and row["archived_at"] is not None:
                continue
            if status == "ARCHIVED" and row["archived_at"] is None:
                continue
            if code is not None and row["zone_code"] != code:
                continue
            items.append(row)
        return items[:limit]

    def fake_get_zone(session, zone_id, *, lock=False):
        return session["zones"].get(int(zone_id))

    def fake_get_zone_farm_id(session, zone_id):
        row = session["zones"].get(int(zone_id))
        return row["farm_id"] if row else None

    def fake_insert_zone(session, *, farm_id, code, name, description):
        for r in session["zones"].values():
            if r["zone_code"] == code:
                from app.core.errors import WebError

                raise WebError(409, "CODE_EXISTS", "CODE_EXISTS")
        zid = session["next_id"]
        session["next_id"] += 1
        row = {
            "zone_id": zid,
            "farm_id": int(farm_id),
            "zone_code": code,
            "name": name,
            "description": description,
            "archived_at": None,
            "row_ver": next_ver(),
        }
        session["zones"][zid] = row
        return row

    def fake_update_zone_metadata(session, *, zone_id, expected_version, changes):
        row = session["zones"].get(int(zone_id))
        if row is None or bytes(row["row_ver"]) != expected_version:
            return None
        row.update(changes)
        row["row_ver"] = next_ver()
        return row

    def fake_lock_farm_zones(session, farm_id):
        return [z for z in session["zones"].values() if z["farm_id"] == int(farm_id)]

    def fake_lock_farm_gateways(session, farm_id):
        return [g for g in session["gateways"].values() if g["farm_id"] == int(farm_id)]

    def fake_lock_zone_nodes(session, zone_id):
        return [n for n in session["nodes"].values() if n["zone_id"] == int(zone_id)]

    def fake_set_farm_archived(session, *, farm_id, expected_version, archive):
        row = session["farms"].get(int(farm_id))
        if row is None or bytes(row["row_ver"]) != expected_version:
            return None
        row["archived_at"] = datetime(2026, 11, 18, 12, 0, 0) if archive else None
        row["row_ver"] = next_ver()
        return row

    def fake_set_zone_archived(session, *, zone_id, expected_version, archive):
        row = session["zones"].get(int(zone_id))
        if row is None or bytes(row["row_ver"]) != expected_version:
            return None
        row["archived_at"] = datetime(2026, 11, 18, 12, 0, 0) if archive else None
        row["row_ver"] = next_ver()
        return row

    monkeypatch.setattr("app.modules.areas.repository.list_farms", fake_list_farms)
    monkeypatch.setattr("app.modules.areas.repository.get_farm", fake_get_farm)
    monkeypatch.setattr("app.modules.areas.repository.insert_farm", fake_insert_farm)
    monkeypatch.setattr(
        "app.modules.areas.repository.update_farm_metadata", fake_update_farm_metadata
    )
    monkeypatch.setattr("app.modules.areas.repository.list_zones", fake_list_zones)
    monkeypatch.setattr("app.modules.areas.repository.get_zone", fake_get_zone)
    monkeypatch.setattr("app.modules.areas.repository.get_zone_farm_id", fake_get_zone_farm_id)
    monkeypatch.setattr("app.modules.areas.repository.insert_zone", fake_insert_zone)
    monkeypatch.setattr(
        "app.modules.areas.repository.update_zone_metadata", fake_update_zone_metadata
    )
    monkeypatch.setattr("app.modules.areas.repository.lock_farm_zones", fake_lock_farm_zones)
    monkeypatch.setattr("app.modules.areas.repository.lock_farm_gateways", fake_lock_farm_gateways)
    monkeypatch.setattr("app.modules.areas.repository.lock_zone_nodes", fake_lock_zone_nodes)
    monkeypatch.setattr("app.modules.areas.repository.set_farm_archived", fake_set_farm_archived)
    monkeypatch.setattr("app.modules.areas.repository.set_zone_archived", fake_set_zone_archived)

    return app, state


def test_area_code_and_schema_validation():
    assert area_code("Khu-Vực_01") == "Khu-Vực_01"
    assert area_code("a" * 128) == "a" * 128
    assert area_code("😀" * 64) == "😀" * 64
    for bad in ("", " ", " a", "a ", "a\nb", "\u200babc", "a" * 129, "😀" * 65):
        with pytest.raises(ValueError):
            area_code(bad)

    created = AreaCreate(code="FARM-1", name="  Nông trại A  ", description=None)
    assert created.name == "Nông trại A"
    with pytest.raises(ValidationError):
        AreaCreate(code="FARM-1", name="  ", description=None)
    with pytest.raises(ValidationError):
        AreaCreate(code="FARM-1", name="A", extra_field="x")
    with pytest.raises(ValidationError):
        AreaPatch(name=None, expected_row_version=base64.b64encode(b"\x00" * 8).decode())


def test_area_auth_and_request_shape():
    app = create_app(_settings())
    with TestClient(app) as client:
        for path in ("/farms", "/farms/1", "/farms/1/zones", "/zones/1"):
            r = client.get("/api/v1" + path)
            assert r.status_code == 401
            assert r.headers["cache-control"] == "no-store"

        bad = client.get("/api/v1/farms?status=BAD")
        assert bad.status_code == 400
        assert bad.json()["error"]["code"] == "INPUT_INVALID"

        bad_size = client.get("/api/v1/farms?page_size=101")
        assert bad_size.status_code == 400

        dup = client.get("/api/v1/farms?status=ACTIVE&status=ALL")
        assert dup.status_code == 400


def test_farm_zone_crud_pagination_and_lifecycle(monkeypatch):
    app, state = _client_with_fake_area_tx(monkeypatch)
    with TestClient(app) as client:
        client.cookies.set("hq_session", "valid-cookie")
        # Empty DB is valid
        r_empty = client.get("/api/v1/farms")
        assert r_empty.status_code == 200
        assert r_empty.json() == {"items": [], "next_cursor": None}

        # Create Farm
        r_f1 = client.post(
            "/api/v1/farms",
            json={"code": "F-01", "name": "Farm 1", "description": "Desc"},
            headers=_headers("valid-csrf"),
        )
        assert r_f1.status_code == 201
        f1 = r_f1.json()
        assert r_f1.headers["location"] == f"/api/v1/farms/{f1['id']}"
        assert f1["archived_at"] is None

        # Duplicate Farm code
        r_dup = client.post(
            "/api/v1/farms",
            json={"code": "F-01", "name": "Dup"},
            headers=_headers("valid-csrf"),
        )
        assert r_dup.status_code == 409
        assert r_dup.json()["error"]["code"] == "CODE_EXISTS"

        # Patch Farm no-op preserves row_version; stale version returns 409 even on same values
        r_noop = client.patch(
            f"/api/v1/farms/{f1['id']}",
            json={"name": "Farm 1", "expected_row_version": f1["row_version"]},
            headers=_headers("valid-csrf"),
        )
        assert r_noop.status_code == 200
        assert r_noop.json()["row_version"] == f1["row_version"]

        r_upd = client.patch(
            f"/api/v1/farms/{f1['id']}",
            json={
                "name": "Farm 1 Updated",
                "description": None,
                "expected_row_version": f1["row_version"],
            },
            headers=_headers("valid-csrf"),
        )
        assert r_upd.status_code == 200
        f1_v2 = r_upd.json()
        assert f1_v2["description"] is None
        assert f1_v2["row_version"] != f1["row_version"]

        r_stale = client.patch(
            f"/api/v1/farms/{f1['id']}",
            json={"name": "Farm 1 Updated", "expected_row_version": f1["row_version"]},
            headers=_headers("valid-csrf"),
        )
        assert r_stale.status_code == 409
        assert r_stale.json()["error"]["code"] == "VERSION_CONFLICT"

        # Create Zone under Farm
        r_z1 = client.post(
            f"/api/v1/farms/{f1['id']}/zones",
            json={"code": "Z-01", "name": "Zone 1"},
            headers=_headers("valid-csrf"),
        )
        assert r_z1.status_code == 201
        z1 = r_z1.json()
        assert z1["farm_id"] == f1["id"]

        # Archive Farm fails while Zone is active -> 409 AREA_IN_USE
        r_arch_f_fail = client.post(
            f"/api/v1/farms/{f1['id']}/archive",
            json={"expected_row_version": f1_v2["row_version"]},
            headers=_headers("valid-csrf"),
        )
        assert r_arch_f_fail.status_code == 409
        assert r_arch_f_fail.json()["error"]["code"] == "AREA_IN_USE"

        # Add enabled Node in Zone -> archive Zone blocked with 409 AREA_IN_USE
        state["nodes"][100] = {"node_id": 100, "zone_id": int(z1["id"]), "receiving_enabled": True}
        r_arch_z_fail = client.post(
            f"/api/v1/zones/{z1['id']}/archive",
            json={"expected_row_version": z1["row_version"]},
            headers=_headers("valid-csrf"),
        )
        assert r_arch_z_fail.status_code == 409
        assert r_arch_z_fail.json()["error"]["code"] == "AREA_IN_USE"

        # Disable Node (simulating FL-04) -> archive Zone succeeds
        state["nodes"][100]["receiving_enabled"] = False
        r_arch_z = client.post(
            f"/api/v1/zones/{z1['id']}/archive",
            json={"expected_row_version": z1["row_version"]},
            headers=_headers("valid-csrf"),
        )
        assert r_arch_z.status_code == 200
        z1_arch = r_arch_z.json()
        assert z1_arch["archived_at"] is not None

        # Archive Farm succeeds once all zones archived and no enabled gateways
        r_arch_f = client.post(
            f"/api/v1/farms/{f1['id']}/archive",
            json={"expected_row_version": f1_v2["row_version"]},
            headers=_headers("valid-csrf"),
        )
        assert r_arch_f.status_code == 200
        f1_arch = r_arch_f.json()
        assert f1_arch["archived_at"] is not None

        # Creating Zone or restoring Zone under archived Farm returns 409 AREA_ARCHIVED
        r_z_under_arch = client.post(
            f"/api/v1/farms/{f1['id']}/zones",
            json={"code": "Z-02", "name": "Zone 2"},
            headers=_headers("valid-csrf"),
        )
        assert r_z_under_arch.status_code == 409
        assert r_z_under_arch.json()["error"]["code"] == "AREA_ARCHIVED"

        r_res_z_fail = client.post(
            f"/api/v1/zones/{z1['id']}/restore",
            json={"expected_row_version": z1_arch["row_version"]},
            headers=_headers("valid-csrf"),
        )
        assert r_res_z_fail.status_code == 409
        assert r_res_z_fail.json()["error"]["code"] == "AREA_ARCHIVED"

        # Restore Farm then Zone; node remains disabled
        r_res_f = client.post(
            f"/api/v1/farms/{f1['id']}/restore",
            json={"expected_row_version": f1_arch["row_version"]},
            headers=_headers("valid-csrf"),
        )
        assert r_res_f.status_code == 200
        assert r_res_f.json()["archived_at"] is None

        r_res_z = client.post(
            f"/api/v1/zones/{z1['id']}/restore",
            json={"expected_row_version": z1_arch["row_version"]},
            headers=_headers("valid-csrf"),
        )
        assert r_res_z.status_code == 200
        assert r_res_z.json()["archived_at"] is None
        assert state["nodes"][100]["receiving_enabled"] is False


def test_area_cursor_pagination_and_binding(monkeypatch):
    app, _ = _client_with_fake_area_tx(monkeypatch)
    with TestClient(app) as client:
        client.cookies.set("hq_session", "valid-cookie")
        for idx in range(1, 4):
            res = client.post(
                "/api/v1/farms",
                json={"code": f"F-{idx}", "name": f"Farm {idx}"},
                headers=_headers("valid-csrf"),
            )
            assert res.status_code == 201

        p1 = client.get("/api/v1/farms?page_size=2").json()
        assert [x["code"] for x in p1["items"]] == ["F-1", "F-2"]
        assert p1["next_cursor"] is not None

        p2 = client.get(f"/api/v1/farms?page_size=2&cursor={p1['next_cursor']}").json()
        assert [x["code"] for x in p2["items"]] == ["F-3"]
        assert p2["next_cursor"] is None

        # Changed filter rejects cursor
        bad_bind = client.get(f"/api/v1/farms?page_size=2&status=ALL&cursor={p1['next_cursor']}")
        assert bad_bind.status_code == 400
        assert bad_bind.json()["error"]["code"] == "CURSOR_INVALID"

        # Tampered cursor rejected
        bad_sig = client.get(f"/api/v1/farms?page_size=2&cursor={p1['next_cursor']}x")
        assert bad_sig.status_code == 400
        assert bad_sig.json()["error"]["code"] == "CURSOR_INVALID"


def test_area_service_instantiates_without_manifest():
    svc = AreaService(SimpleNamespace(engine=None), Settings(_env_file=None))
    assert svc.settings.dataset_generation == "1"
