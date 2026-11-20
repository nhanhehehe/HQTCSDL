from contextlib import contextmanager
from datetime import datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.exc import OperationalError

from app.core.config import Settings
from app.main import create_app
from app.modules.measurements import repository as meas_repo
from app.modules.measurements.tokens import make_view_payload, sign_view_token

NOW = datetime(2026, 11, 15, 10, 10, 0)


def _epoch(dt: datetime) -> int:
    return int((dt - datetime(1970, 1, 1)).total_seconds())


def _settings(**overrides):
    defaults = {
        "_env_file": None,
        "csrf_secret": SecretStr("C" * 32),
        "cursor_secret": SecretStr("K" * 32),
        "dataset_generation": "1",
        "web_origin": "http://127.0.0.1:5173",
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _make_metrics(
    *,
    temp: Decimal | None = Decimal("28.50"),
    temp_status: str = "OK",
    soil: Decimal | None = None,
    soil_status: str = "READ_ERROR",
    light: Decimal | None = Decimal("0.0"),
    light_status: str = "OK",
):
    return {
        "temperature": {"value": temp, "status": temp_status, "unit": "°C"},
        "airHumidity": {"value": Decimal("72.00"), "status": "OK", "unit": "%RH"},
        "pressure": {"value": Decimal("1012.00"), "status": "OK", "unit": "hPa"},
        "light": {"value": light, "status": light_status, "unit": "lux"},
        "soilMoisture": {"value": soil, "status": soil_status, "unit": "%"},
    }


def _client_with_fake_measurements(monkeypatch, settings=None):
    cfg = settings or _settings()
    app = create_app(cfg)
    state = {
        "now": NOW,
        "farms": {1: {"farm_id": 1, "archived_at": None}},
        "zones": {
            10: {"zone_id": 10, "farm_id": 1, "archived_at": None},
            11: {"zone_id": 11, "farm_id": 1, "archived_at": datetime(2026, 11, 14, 0, 0, 0)},
        },
        "gateways": {100: {"gateway_id": 100, "farm_id": 1}},
        "nodes": {
            101: {
                "node_id": 101,
                "device_code": "NODE-01",
                "zone_id": 10,
                "farm_id": 1,
                "gateway_id": 100,
            },
            102: {
                "node_id": 102,
                "device_code": "NODE-02",
                "zone_id": 11,
                "farm_id": 1,
                "gateway_id": None,
            },
        },
        "measurements": [],  # list of dicts
        "values": {},  # mid -> dict[metric_code, dict]
        "next_mid": 1,
        "deadlocks_before_cap": 0,
    }

    def add_meas(
        *,
        node_id: int = 101,
        boot_id: int = 1,
        seq: int = 1,
        t_node: int | None = None,
        recv_time: int = 1_763_201_400,
        persisted_at: datetime | None = None,
        metrics: dict | None = None,
    ) -> int:
        mid = state["next_mid"]
        state["next_mid"] += 1
        state["measurements"].append(
            {
                "measurement_id": mid,
                "node_id": node_id,
                "boot_id": boot_id,
                "seq": seq,
                "t_node": t_node,
                "time_status": "VALID" if t_node is not None else "UNKNOWN",
                "recv_time": recv_time,
                "persisted_at": persisted_at or state["now"],
            }
        )
        state["values"][mid] = metrics if metrics is not None else _make_metrics()
        return mid

    state["add_meas"] = add_meas

    @contextmanager
    def fake_tx(
        database,
        settings_arg,
        cookie,
        csrf=None,
        *,
        write=False,
        require_csrf=None,
        lock_session=None,
        isolation=None,
        propagate_deadlock=False,
    ):
        if cookie != "valid-cookie":
            from app.modules.auth.service import invalid_session

            raise invalid_session()
        if propagate_deadlock and state["deadlocks_before_cap"] > 0:
            state["deadlocks_before_cap"] -= 1
            raise OperationalError(None, None, Exception("1205 deadlock victim"))
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

    monkeypatch.setattr("app.modules.measurements.service.web_session_transaction", fake_tx)

    def fake_get_node_meta(session, node_id):
        n = session["nodes"].get(int(node_id))
        return dict(n) if n is not None else None

    def fake_capture_cap(session, node_id):
        n = session["nodes"].get(int(node_id))
        if n is None:
            return None
        mids = [
            m["measurement_id"] for m in session["measurements"] if m["node_id"] == int(node_id)
        ]
        return (max(mids, default=0), session["now"])

    def fake_load_values(session, measurement_ids):
        out = {}
        for mid in measurement_ids:
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

    def fake_read_node_current(session, *, node_id, device_code, as_of_iso):
        known = [
            m
            for m in session["measurements"]
            if m["node_id"] == int(node_id) and m["t_node"] is not None
        ]
        if known:
            known.sort(key=lambda m: (m["t_node"], m["boot_id"], m["seq"]), reverse=True)
            top = known[0]
            vals = fake_load_values(session, [top["measurement_id"]])[top["measurement_id"]]
            return {
                "state": "AVAILABLE",
                "measurement": meas_repo.build_measurement_view(top, vals, device_code=device_code),
                "as_of": as_of_iso,
            }
        has_any = any(m["node_id"] == int(node_id) for m in session["measurements"])
        return {
            "state": "NO_EVENT_TIME" if has_any else "NO_DATA",
            "measurement": None,
            "as_of": as_of_iso,
        }

    def fake_list_known(session, *, node_id, lo, hi, cap, ascending, boundary, limit):
        if lo > hi or cap == 0:
            return []
        rows = [
            m
            for m in session["measurements"]
            if m["node_id"] == node_id
            and m["t_node"] is not None
            and lo <= m["t_node"] <= hi
            and m["measurement_id"] <= cap
        ]
        if boundary is not None:
            bt = (boundary["t_node"], boundary["boot_id"], boundary["seq"])
            if ascending:
                rows = [r for r in rows if (r["t_node"], r["boot_id"], r["seq"]) > bt]
            else:
                rows = [r for r in rows if (r["t_node"], r["boot_id"], r["seq"]) < bt]
        rows.sort(key=lambda m: (m["t_node"], m["boot_id"], m["seq"]), reverse=not ascending)
        return rows[:limit]

    def fake_list_unknown(
        session, *, node_id, cap, boundary_persisted_at, boundary_measurement_id, limit
    ):
        if cap == 0:
            return []
        rows = [
            m
            for m in session["measurements"]
            if m["node_id"] == node_id and m["t_node"] is None and m["measurement_id"] <= cap
        ]
        if boundary_persisted_at is not None and boundary_measurement_id is not None:
            rows = [
                r
                for r in rows
                if (r["persisted_at"], r["measurement_id"])
                < (boundary_persisted_at, boundary_measurement_id)
            ]
        rows.sort(key=lambda m: (m["persisted_at"], m["measurement_id"]), reverse=True)
        return rows[:limit]

    def fake_count_existence(session, *, node_id, cap):
        if cap == 0:
            return 0, 0
        rows = [
            m
            for m in session["measurements"]
            if m["node_id"] == node_id and m["measurement_id"] <= cap
        ]
        return len(rows), sum(1 for r in rows if r["t_node"] is not None)

    monkeypatch.setattr(meas_repo, "get_node_meta", fake_get_node_meta)
    monkeypatch.setattr(meas_repo, "capture_node_measurement_cap", fake_capture_cap)
    monkeypatch.setattr(meas_repo, "load_measurement_values_map", fake_load_values)
    monkeypatch.setattr(meas_repo, "read_node_current", fake_read_node_current)
    monkeypatch.setattr(meas_repo, "list_known_headers", fake_list_known)
    monkeypatch.setattr(meas_repo, "list_unknown_headers", fake_list_unknown)
    monkeypatch.setattr(meas_repo, "count_node_existence_up_to_cap", fake_count_existence)
    return app, state


def test_measurements_auth_and_input_validation(monkeypatch):
    app, _ = _client_with_fake_measurements(monkeypatch)
    with TestClient(app) as client:
        # Unauthenticated rejected
        assert client.get("/api/v1/nodes/101/measurements/current").status_code == 401
        assert client.get("/api/v1/nodes/101/measurements").status_code == 401
        assert client.get("/api/v1/nodes/101/measurements/chart").status_code == 401

        client.cookies.set("hq_session", "valid-cookie")
        # Unknown Node -> 404
        assert client.get("/api/v1/nodes/999/measurements/current").status_code == 404
        # G3-D03: farm_id rejected as unknown parameter on node endpoints
        assert client.get("/api/v1/nodes/101/measurements?farm_id=1").status_code == 400
        assert client.get("/api/v1/nodes/101/measurements/current?farm_id=1").status_code == 400
        assert client.get("/api/v1/nodes/101/measurements/chart?farm_id=1").status_code == 400
        # time_mode=unknown rejects from/to
        res = client.get(
            "/api/v1/nodes/101/measurements?time_mode=unknown&from=2026-11-15T00:00:00Z"
        )
        assert res.status_code == 400
        assert res.json()["error"]["code"] == "INPUT_INVALID"
        # Bad time_mode
        assert client.get("/api/v1/nodes/101/measurements?time_mode=all").status_code == 400
        # Archived Zone node still readable (200 NO_DATA)
        archived_res = client.get("/api/v1/nodes/102/measurements/current")
        assert archived_res.status_code == 200
        assert archived_res.json()["state"] == "NO_DATA"


def test_current_selection_late_record_all_error_and_zero_preserved(monkeypatch):
    app, state = _client_with_fake_measurements(monkeypatch)
    add_meas = state["add_meas"]
    t_0930 = _epoch(datetime(2026, 11, 15, 9, 30, 0))
    t_1005 = _epoch(datetime(2026, 11, 15, 10, 5, 0))
    t_1010 = _epoch(datetime(2026, 11, 15, 10, 10, 0))

    with TestClient(app) as client:
        client.cookies.set("hq_session", "valid-cookie")

        # Empty -> NO_DATA
        r0 = client.get("/api/v1/nodes/101/measurements/current")
        assert r0.status_code == 200
        assert r0.json() == {
            "state": "NO_DATA",
            "measurement": None,
            "as_of": "2026-11-15T10:10:00.000Z",
        }

        # Only UNKNOWN -> NO_EVENT_TIME
        add_meas(node_id=101, boot_id=1, seq=1, t_node=None)
        r1 = client.get("/api/v1/nodes/101/measurements/current")
        assert r1.json()["state"] == "NO_EVENT_TIME"

        # Add 10:05 valid record with 0 lux and 1 sensor READ_ERROR
        add_meas(node_id=101, boot_id=1, seq=2, t_node=t_1005)
        # Add late 09:30 record after 10:05 -> current stays 10:05
        add_meas(node_id=101, boot_id=1, seq=3, t_node=t_0930)
        r2 = client.get("/api/v1/nodes/101/measurements/current")
        body2 = r2.json()
        assert body2["state"] == "AVAILABLE"
        meas = body2["measurement"]
        assert meas["t_node"] == str(t_1005)
        assert "backend_received_at" not in meas
        assert list(meas["measurements"].keys()) == [
            "temperature",
            "airHumidity",
            "pressure",
            "light",
            "soilMoisture",
        ]
        assert meas["measurements"]["light"]["value"] == "0"
        assert meas["measurements"]["soilMoisture"] == {
            "value": None,
            "status": "READ_ERROR",
            "unit": "%",
        }

        # Add newest 10:10 record where all 5 metrics are errors -> stays current (never falls back)
        all_err = {
            code: {"value": None, "status": "READ_ERROR", "unit": meas_repo.METRIC_UNITS[code]}
            for code in meas_repo.METRIC_ORDER
        }
        add_meas(node_id=101, boot_id=1, seq=4, t_node=t_1010, metrics=all_err)
        r3 = client.get("/api/v1/nodes/101/measurements/current")
        assert r3.json()["measurement"]["t_node"] == str(t_1010)
        for code in meas_repo.METRIC_ORDER:
            assert r3.json()["measurement"]["measurements"][code]["value"] is None
            assert r3.json()["measurement"]["measurements"][code]["status"] == "READ_ERROR"


def test_history_and_chart_shared_view_token_cap_tiebreak_and_fractional_range(monkeypatch):
    app, state = _client_with_fake_measurements(monkeypatch)
    add_meas = state["add_meas"]
    t1 = _epoch(datetime(2026, 11, 15, 8, 0, 0))
    t2 = _epoch(datetime(2026, 11, 15, 9, 0, 0))
    # Same t_node t2 with distinct boot/seq -> must preserve both and order deterministically
    add_meas(node_id=101, boot_id=1, seq=1, t_node=t1)
    add_meas(node_id=101, boot_id=1, seq=2, t_node=t2)
    add_meas(node_id=101, boot_id=2, seq=1, t_node=t2)
    # Unknown record must NOT enter known or chart
    add_meas(node_id=101, boot_id=2, seq=2, t_node=None)

    with TestClient(app) as client:
        client.cookies.set("hq_session", "valid-cookie")
        state["deadlocks_before_cap"] = 1

        # Page 1 of known history (page_size=2) with default 24h range
        h1 = client.get("/api/v1/nodes/101/measurements?time_mode=known&page_size=2")
        assert h1.status_code == 200
        b1 = h1.json()
        assert b1["state"] == "AVAILABLE"
        assert [i["identity"]["seq"] for i in b1["items"]] == ["1", "2"]
        assert [i["identity"]["boot_id"] for i in b1["items"]] == ["2", "1"]
        token = b1["view_token"]
        assert b1["next_cursor"] is not None

        # Advance clock AND insert a late backlog measurement AFTER cap was captured
        state["now"] = NOW + timedelta(hours=3)
        add_meas(
            node_id=101,
            boot_id=3,
            seq=1,
            t_node=_epoch(datetime(2026, 11, 15, 8, 30, 0)),
        )

        # Page 2 using cursor: backlog after cap excluded AND default 24h range preserved
        h2 = client.get(
            f"/api/v1/nodes/101/measurements?time_mode=known&page_size=2&cursor={b1['next_cursor']}"
        )
        assert h2.status_code == 200
        assert [i["t_node"] for i in h2.json()["items"]] == [str(t1)]
        assert h2.json()["range"] == b1["range"]
        assert h2.json()["next_cursor"] is None

        # Chart using the SAME view_token: ASC order & same 3 items (no post-cap backlog)
        c1 = client.get(f"/api/v1/nodes/101/measurements/chart?page_size=2&view_token={token}")
        assert c1.status_code == 200
        cb1 = c1.json()
        assert cb1["view_token"] == token
        assert [p["identity"] for p in cb1["points"]] == [
            {"device_id": "NODE-01", "boot_id": "1", "seq": "1"},
            {"device_id": "NODE-01", "boot_id": "1", "seq": "2"},
        ]
        c2 = client.get(
            f"/api/v1/nodes/101/measurements/chart?page_size=2&cursor={cb1['next_cursor']}&view_token={token}"
        )
        assert c2.status_code == 200
        assert [p["identity"] for p in c2.json()["points"]] == [
            {"device_id": "NODE-01", "boot_id": "2", "seq": "1"}
        ]
        assert c2.json()["next_cursor"] is None

        # Cross-route cursor (history cursor passed to chart) -> 400 CURSOR_INVALID
        bad_route = client.get(
            f"/api/v1/nodes/101/measurements/chart?page_size=2&cursor={b1['next_cursor']}"
        )
        assert bad_route.status_code == 400
        assert bad_route.json()["error"]["code"] == "CURSOR_INVALID"

        # Changing `from` while keeping view_token -> 400 CURSOR_INVALID
        bad_range_tok = client.get(
            f"/api/v1/nodes/101/measurements/chart?view_token={token}&from=2026-11-14T00:00:00Z"
        )
        assert bad_range_tok.status_code == 400
        assert bad_range_tok.json()["error"]["code"] == "CURSOR_INVALID"

        # Cross-mode view_token rejected on unknown
        bad_mode = client.get(
            f"/api/v1/nodes/101/measurements?time_mode=unknown&view_token={token}"
        )
        assert bad_mode.status_code == 400
        assert bad_mode.json()["error"]["code"] == "CURSOR_INVALID"

        # E07-02: fractional range with no integer second -> EMPTY_RANGE (not RANGE_INVALID)
        empty_frac = client.get(
            "/api/v1/nodes/101/measurements"
            "?time_mode=known&from=1970-01-01T00:01:40.1Z&to=1970-01-01T00:01:40.9Z"
        )
        assert empty_frac.status_code == 200
        assert empty_frac.json()["state"] == "EMPTY_RANGE"

        # from > to -> 400 RANGE_INVALID
        bad_order = client.get(
            "/api/v1/nodes/101/measurements"
            "?time_mode=known&from=2026-11-15T10:00:01Z&to=2026-11-15T10:00:00Z"
        )
        assert bad_order.status_code == 400
        assert bad_order.json()["error"]["code"] == "RANGE_INVALID"


def test_unknown_pagination_and_corrupt_children_returns_503(monkeypatch):
    app, state = _client_with_fake_measurements(monkeypatch)
    add_meas = state["add_meas"]
    p1 = datetime(2026, 11, 15, 9, 0, 0, 100000)
    p2 = datetime(2026, 11, 15, 9, 0, 0, 100000)

    with TestClient(app) as client:
        client.cookies.set("hq_session", "valid-cookie")
        # Only known -> unknown returns EMPTY_RANGE (known exists)
        add_meas(node_id=101, boot_id=1, seq=1, t_node=_epoch(NOW - timedelta(minutes=5)))
        u_empty = client.get("/api/v1/nodes/101/measurements?time_mode=unknown")
        assert u_empty.status_code == 200
        assert u_empty.json()["state"] == "EMPTY_RANGE"
        assert u_empty.json()["range"] is None

        # Add two unknown records with identical persisted_at millisecond -> deterministic tie-break
        m2 = add_meas(node_id=101, boot_id=1, seq=2, t_node=None, persisted_at=p1)
        m3 = add_meas(node_id=101, boot_id=1, seq=3, t_node=None, persisted_at=p2)
        u1 = client.get("/api/v1/nodes/101/measurements?time_mode=unknown&page_size=1")
        assert u1.status_code == 200
        assert [i["id"] for i in u1.json()["items"]] == [str(m3)]
        u2 = client.get(
            f"/api/v1/nodes/101/measurements?time_mode=unknown&page_size=1&cursor={u1.json()['next_cursor']}"
        )
        assert u2.status_code == 200
        assert [i["id"] for i in u2.json()["items"]] == [str(m2)]
        assert u2.json()["next_cursor"] is None

        # Corrupt children (missing 1 of 5 metrics) -> 503 STORAGE_UNAVAILABLE
        del state["values"][m3]["pressure"]
        corrupt = client.get("/api/v1/nodes/101/measurements?time_mode=unknown")
        assert corrupt.status_code == 503
        assert corrupt.json()["error"]["code"] == "STORAGE_UNAVAILABLE"


def test_dataset_generation_change_invalidates_view_token(monkeypatch):
    app, state = _client_with_fake_measurements(monkeypatch)
    with TestClient(app) as client:
        client.cookies.set("hq_session", "valid-cookie")
        vp = make_view_payload(
            generation="old-gen",
            node_id="101",
            mode="known",
            from_iso="2026-11-14T10:10:00.000Z",
            to_iso="2026-11-15T10:10:00.000Z",
            cap=0,
            as_of="2026-11-15T10:10:00.000Z",
        )
        bad_tok = sign_view_token(_settings(dataset_generation="old-gen"), vp)
        res = client.get(f"/api/v1/nodes/101/measurements/chart?view_token={bad_tok}")
        assert res.status_code == 400
        assert res.json()["error"]["code"] == "CURSOR_INVALID"


def test_unknown_non_1205_deadlock_exhausted_returns_503(monkeypatch):
    app, state = _client_with_fake_measurements(monkeypatch)
    state["deadlocks_before_cap"] = 10
    monkeypatch.setattr("app.modules.measurements.service.time.sleep", lambda _s: None)
    with TestClient(app) as client:
        client.cookies.set("hq_session", "valid-cookie")
        res = client.get("/api/v1/nodes/101/measurements")
        assert res.status_code == 503
        assert res.json()["error"]["code"] == "STORAGE_UNAVAILABLE"
