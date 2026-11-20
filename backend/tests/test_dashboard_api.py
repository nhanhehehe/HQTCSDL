from contextlib import contextmanager
from datetime import datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import Settings
from app.main import create_app
from app.modules.measurements import repository as meas_repo


def _epoch(dt: datetime) -> int:
    return int((dt - datetime(1970, 1, 1)).total_seconds())


NOW = datetime(2026, 11, 15, 10, 10, 0, 250000)


def _settings(**overrides):
    defaults = {
        "_env_file": None,
        "csrf_secret": SecretStr("C" * 32),
        "cursor_secret": SecretStr("K" * 32),
        "node_time_profiles": {
            "NODE-FRESH": {"sampling_interval_seconds": 60, "normal_delivery_budget_seconds": 30},
            "NODE-BOUNDARY": {
                "sampling_interval_seconds": 60,
                "normal_delivery_budget_seconds": 30,
            },
            "NODE-STALE": {"sampling_interval_seconds": 60, "normal_delivery_budget_seconds": 30},
            "NODE-AHEAD": {"sampling_interval_seconds": 60, "normal_delivery_budget_seconds": 30},
        },
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _metrics(*, soil_error: bool = False):
    return {
        "temperature": {"value": Decimal("28.50"), "status": "OK", "unit": "°C"},
        "airHumidity": {"value": Decimal("72.00"), "status": "OK", "unit": "%RH"},
        "pressure": {"value": Decimal("1012.00"), "status": "OK", "unit": "hPa"},
        "light": {"value": Decimal("0.0"), "status": "OK", "unit": "lux"},
        "soilMoisture": (
            {"value": None, "status": "READ_ERROR", "unit": "%"}
            if soil_error
            else {"value": Decimal("55.5"), "status": "OK", "unit": "%"}
        ),
    }


def _client_with_fake_dashboard(monkeypatch, settings=None):
    cfg = settings or _settings()
    app = create_app(cfg)
    now_epoch = _epoch(datetime(2026, 11, 15, 10, 10, 0))
    state = {
        "now": NOW,
        "farms": {
            1: {"farm_id": 1, "farm_code": "FARM-1", "name": "Farm 1", "archived_at": None},
            2: {
                "farm_id": 2,
                "farm_code": "FARM-2",
                "name": "Archived Farm",
                "archived_at": datetime(2026, 11, 14, 12, 0, 0),
            },
            3: {"farm_id": 3, "farm_code": "FARM-3", "name": "Empty Farm", "archived_at": None},
        },
        "nodes": {
            101: {
                "node_id": 101,
                "device_code": "NODE-FRESH",
                "name": "Fresh Node",
                "receiving_enabled": True,
                "zone_id": 10,
                "farm_id": 1,
                "zone_code": "Z1",
                "zone_name": "Zone 1",
                "zone_archived_at": None,
                "gateway_id": 201,
                "gateway_code": "GW-01",
                "gateway_name": "Gateway 1",
                "gateway_receiving_enabled": True,
            },
            102: {
                "node_id": 102,
                "device_code": "NODE-BOUNDARY",
                "name": "Boundary Node",
                "receiving_enabled": True,
                "zone_id": 10,
                "farm_id": 1,
                "zone_code": "Z1",
                "zone_name": "Zone 1",
                "zone_archived_at": None,
                "gateway_id": None,
                "gateway_code": None,
                "gateway_name": None,
                "gateway_receiving_enabled": None,
            },
            103: {
                "node_id": 103,
                "device_code": "NODE-STALE",
                "name": "Stale Node",
                "receiving_enabled": False,
                "zone_id": 10,
                "farm_id": 1,
                "zone_code": "Z1",
                "zone_name": "Zone 1",
                "zone_archived_at": None,
                "gateway_id": None,
                "gateway_code": None,
                "gateway_name": None,
                "gateway_receiving_enabled": None,
            },
            104: {
                "node_id": 104,
                "device_code": "NODE-AHEAD",
                "name": "Ahead Node",
                "receiving_enabled": True,
                "zone_id": 10,
                "farm_id": 1,
                "zone_code": "Z1",
                "zone_name": "Zone 1",
                "zone_archived_at": None,
                "gateway_id": None,
                "gateway_code": None,
                "gateway_name": None,
                "gateway_receiving_enabled": None,
            },
            105: {
                "node_id": 105,
                "device_code": "NODE-NOPROF",
                "name": "Missing Profile Node",
                "receiving_enabled": True,
                "zone_id": 10,
                "farm_id": 1,
                "zone_code": "Z1",
                "zone_name": "Zone 1",
                "zone_archived_at": None,
                "gateway_id": None,
                "gateway_code": None,
                "gateway_name": None,
                "gateway_receiving_enabled": None,
            },
            106: {
                "node_id": 106,
                "device_code": "NODE-NOEV",
                "name": "Unknown Only Node",
                "receiving_enabled": True,
                "zone_id": 10,
                "farm_id": 1,
                "zone_code": "Z1",
                "zone_name": "Zone 1",
                "zone_archived_at": None,
                "gateway_id": None,
                "gateway_code": None,
                "gateway_name": None,
                "gateway_receiving_enabled": None,
            },
            107: {
                "node_id": 107,
                "device_code": "NODE-EMPTY",
                "name": "Empty Node",
                "receiving_enabled": True,
                "zone_id": 10,
                "farm_id": 1,
                "zone_code": "Z1",
                "zone_name": "Zone 1",
                "zone_archived_at": None,
                "gateway_id": None,
                "gateway_code": None,
                "gateway_name": None,
                "gateway_receiving_enabled": None,
            },
            201: {
                "node_id": 201,
                "device_code": "NODE-ARCH",
                "name": "Archived Node",
                "receiving_enabled": False,
                "zone_id": 20,
                "farm_id": 2,
                "zone_code": "Z-ARCH",
                "zone_name": "Archived Zone",
                "zone_archived_at": datetime(2026, 11, 14, 11, 0, 0),
                "gateway_id": None,
                "gateway_code": None,
                "gateway_name": None,
                "gateway_receiving_enabled": None,
            },
        },
        "measurements": [
            # 101: FRESH (age 20.25s <= 90s), with 1 sensor error
            {
                "measurement_id": 1,
                "node_id": 101,
                "boot_id": 1,
                "seq": 1,
                "t_node": now_epoch - 20,
                "time_status": "VALID",
                "recv_time": now_epoch - 19,
                "persisted_at": NOW - timedelta(seconds=18),
            },
            # 102: exact boundary at as_of == 2026-11-15T10:10:00.000Z when ms=0 -> tested below
            {
                "measurement_id": 2,
                "node_id": 102,
                "boot_id": 1,
                "seq": 1,
                "t_node": now_epoch - 90,
                "time_status": "VALID",
                "recv_time": now_epoch - 89,
                "persisted_at": NOW - timedelta(seconds=88),
            },
            # 103: STALE (age 91.25s > 90s)
            {
                "measurement_id": 3,
                "node_id": 103,
                "boot_id": 1,
                "seq": 1,
                "t_node": now_epoch - 91,
                "time_status": "VALID",
                "recv_time": now_epoch - 90,
                "persisted_at": NOW - timedelta(seconds=89),
            },
            # 104: CLOCK_AHEAD (t_node = now_epoch + 5 -> age_ms = -4750 -> -4.75s)
            {
                "measurement_id": 4,
                "node_id": 104,
                "boot_id": 1,
                "seq": 1,
                "t_node": now_epoch + 5,
                "time_status": "VALID",
                "recv_time": now_epoch,
                "persisted_at": NOW,
            },
            # 105: PROFILE_MISSING
            {
                "measurement_id": 5,
                "node_id": 105,
                "boot_id": 1,
                "seq": 1,
                "t_node": now_epoch - 10,
                "time_status": "VALID",
                "recv_time": now_epoch - 9,
                "persisted_at": NOW - timedelta(seconds=8),
            },
            # 106: UNKNOWN only -> NO_EVENT_TIME
            {
                "measurement_id": 6,
                "node_id": 106,
                "boot_id": 1,
                "seq": 1,
                "t_node": None,
                "time_status": "UNKNOWN",
                "recv_time": now_epoch - 5,
                "persisted_at": NOW - timedelta(seconds=4),
            },
        ],
        "values": {
            1: _metrics(soil_error=True),
            2: _metrics(),
            3: _metrics(),
            4: _metrics(),
            5: _metrics(),
            6: _metrics(),
        },
    }

    @contextmanager
    def fake_tx(database, settings_arg, cookie, csrf=None, *, write=False, **kwargs):
        if cookie != "valid-cookie":
            from app.modules.auth.service import invalid_session

            raise invalid_session()
        yield (
            state,
            {
                "session_id": 1,
                "admin_id": 1,
                "username": "Admin",
                "expires_at": state["now"] + timedelta(hours=2),
                "db_now": state["now"],
            },
        )

    monkeypatch.setattr(
        "app.modules.measurements.dashboard_service.web_session_transaction", fake_tx
    )
    monkeypatch.setattr(meas_repo, "get_farm_summary", lambda s, fid: s["farms"].get(int(fid)))
    monkeypatch.setattr(meas_repo, "get_dashboard_node", lambda s, nid: s["nodes"].get(int(nid)))
    monkeypatch.setattr(
        meas_repo,
        "list_farm_dashboard_nodes",
        lambda s, fid: [dict(n) for _, n in sorted(s["nodes"].items()) if n["farm_id"] == int(fid)],
    )

    def fake_latest(session, node_ids):
        out = {nid: None for nid in node_ids}
        for nid in node_ids:
            known = [
                m
                for m in session["measurements"]
                if m["node_id"] == nid and m["t_node"] is not None
            ]
            if known:
                known.sort(key=lambda m: (m["t_node"], m["boot_id"], m["seq"]), reverse=True)
                out[nid] = dict(known[0])
        return out

    def fake_any(session, node_ids):
        nset = set(node_ids)
        return {m["node_id"] for m in session["measurements"] if m["node_id"] in nset}

    def fake_vals(session, mids):
        out = {}
        for mid in mids:
            raw = session["values"].get(int(mid))
            if raw is None or set(raw.keys()) != set(meas_repo.METRIC_ORDER):
                from app.modules.auth.service import storage_error

                raise storage_error()
            out[int(mid)] = {
                code: {
                    "value": (
                        meas_repo.format_decimal_clean(raw[code]["value"])
                        if raw[code]["value"] is not None
                        else None
                    ),
                    "status": raw[code]["status"],
                    "unit": raw[code]["unit"],
                }
                for code in meas_repo.METRIC_ORDER
            }
        return out

    monkeypatch.setattr(meas_repo, "load_latest_known_headers_for_nodes", fake_latest)
    monkeypatch.setattr(meas_repo, "find_nodes_with_any_measurement", fake_any)
    monkeypatch.setattr(meas_repo, "load_measurement_values_map", fake_vals)
    return app, state


def test_dashboard_scope_validation_and_g3_dto_contract(monkeypatch):
    app, _ = _client_with_fake_dashboard(monkeypatch)
    with TestClient(app) as client:
        assert client.get("/api/v1/dashboard?farm_id=1").status_code == 401
        client.cookies.set("hq_session", "valid-cookie")

        # G3-D01: missing farm_id -> 400, zone_id rejected -> 400
        assert client.get("/api/v1/dashboard").status_code == 400
        assert client.get("/api/v1/dashboard?farm_id=1&zone_id=10").status_code == 400
        # Missing Farm or Node -> 404
        assert client.get("/api/v1/dashboard?farm_id=999").status_code == 404
        assert client.get("/api/v1/dashboard?farm_id=1&node_id=999").status_code == 404
        # Node belonging to Farm 2 queried with farm_id=1 -> 400 INPUT_INVALID
        cross = client.get("/api/v1/dashboard?farm_id=1&node_id=201")
        assert cross.status_code == 400
        assert cross.json()["error"]["code"] == "INPUT_INVALID"

        # Empty farm returns 200 with nodes=[], active_count=None (G3-D02)
        empty_res = client.get("/api/v1/dashboard?farm_id=3")
        assert empty_res.status_code == 200
        empty_body = empty_res.json()
        assert empty_body["capabilities"] == {"monitoring": False, "alerts": False}
        assert empty_body["active_count"] is None
        assert empty_body["scope"] == {
            "kind": "FARM",
            "farm_id": "3",
            "farm": {"id": "3", "code": "FARM-3", "name": "Empty Farm", "archived_at": None},
            "node_ids": [],
        }
        assert empty_body["nodes"] == []

        # Archived Farm & Zone return 200 with snapshot archived_at
        arch_res = client.get("/api/v1/dashboard?farm_id=2")
        assert arch_res.status_code == 200
        arch_body = arch_res.json()
        assert arch_body["scope"]["farm"]["archived_at"] == "2026-11-14T12:00:00.000Z"
        assert arch_body["nodes"][0]["profile"]["zone"]["archived_at"] == "2026-11-14T11:00:00.000Z"

        # NODE scope narrows scope.kind and node_ids
        node_res = client.get("/api/v1/dashboard?farm_id=1&node_id=101")
        assert node_res.status_code == 200
        assert node_res.json()["scope"]["kind"] == "NODE"
        assert node_res.json()["scope"]["node_ids"] == ["101"]


def test_dashboard_freshness_states_boundaries_and_privacy(monkeypatch):
    app, state = _client_with_fake_dashboard(monkeypatch)
    with TestClient(app) as client:
        client.cookies.set("hq_session", "valid-cookie")
        res = client.get("/api/v1/dashboard?farm_id=1")
        assert res.status_code == 200
        body = res.json()
        assert "last_new_persisted_at" not in res.text
        assert "backend_received_at" not in res.text
        assert body["capabilities"] == {"monitoring": False, "alerts": False}
        assert body["active_count"] is None

        by_id = {n["profile"]["id"]: n for n in body["nodes"]}
        # Check G3-D02 null G4 fields on every node
        for node_view in body["nodes"]:
            assert node_view["monitoring"] is None
            assert node_view["evaluation"] is None
            assert node_view["active_alerts"] is None
            assert node_view["active_count"] is None

        # 101: FRESH with sensor error & 0 lux preserved in fixed 5-metric order
        n101 = by_id["101"]
        assert n101["freshness"] == {
            "state": "FRESH",
            "age_seconds": "20.25",
            "stale_after_seconds": 90,
        }
        meas = n101["current"]["measurement"]
        assert list(meas["measurements"].keys()) == [
            "temperature",
            "airHumidity",
            "pressure",
            "light",
            "soilMoisture",
        ]
        assert meas["measurements"]["light"]["value"] == "0"
        assert meas["measurements"]["soilMoisture"]["value"] is None

        # 102 at NOW (ms=250): 90.25s > 90s -> STALE
        assert by_id["102"]["freshness"] == {
            "state": "STALE",
            "age_seconds": "90.25",
            "stale_after_seconds": 90,
        }
        # 103: STALE
        assert by_id["103"]["freshness"] == {
            "state": "STALE",
            "age_seconds": "91.25",
            "stale_after_seconds": 90,
        }
        # 104: CLOCK_AHEAD preserves negative age_seconds without clamping
        assert by_id["104"]["freshness"] == {
            "state": "CLOCK_AHEAD",
            "age_seconds": "-4.75",
            "stale_after_seconds": 90,
        }
        # 105: PROFILE_MISSING
        assert by_id["105"]["freshness"] == {
            "state": "PROFILE_MISSING",
            "age_seconds": "10.25",
            "stale_after_seconds": None,
        }
        # 106: NO_EVENT_TIME
        assert by_id["106"]["current"]["state"] == "NO_EVENT_TIME"
        assert by_id["106"]["freshness"] == {
            "state": "NO_EVENT_TIME",
            "age_seconds": None,
            "stale_after_seconds": None,
        }
        # 107: NO_DATA
        assert by_id["107"]["current"]["state"] == "NO_DATA"
        assert by_id["107"]["freshness"] == {
            "state": "NO_DATA",
            "age_seconds": None,
            "stale_after_seconds": None,
        }

        # Exact boundary check: age_ms == stale_after_seconds * 1000 -> FRESH!
        state["now"] = datetime(2026, 11, 15, 10, 10, 0, 0)
        boundary_res = client.get("/api/v1/dashboard?farm_id=1&node_id=102")
        assert boundary_res.status_code == 200
        assert boundary_res.json()["nodes"][0]["freshness"] == {
            "state": "FRESH",
            "age_seconds": "90",
            "stale_after_seconds": 90,
        }

        # Missing child value -> 503 STORAGE_UNAVAILABLE for entire dashboard package
        del state["values"][1]["light"]
        corrupt = client.get("/api/v1/dashboard?farm_id=1")
        assert corrupt.status_code == 503
        assert corrupt.json()["error"]["code"] == "STORAGE_UNAVAILABLE"
