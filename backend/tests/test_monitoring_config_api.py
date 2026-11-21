import base64
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.exc import OperationalError

from app.core.config import NodeTimeProfile, Settings
from app.main import create_app
from app.modules.alerts import timeout_engine
from app.modules.monitoring_config import repository as mc_repo
from app.modules.monitoring_config import service as mc_service
from app.modules.monitoring_config.schemas import METRIC_ORDER

BASE_TIME = datetime(2026, 11, 20, 10, 0, 0)
WEB_HEADERS = {
    "Origin": "http://127.0.0.1:5173",
    "X-HQ-Client": "web",
    "X-CSRF-Token": "valid-csrf",
}


def _settings(**overrides):
    defaults = {
        "_env_file": None,
        "env": "test",
        "csrf_secret": SecretStr("C" * 32),
        "cursor_secret": SecretStr("K" * 32),
        "web_origin": "http://127.0.0.1:5173",
        "node_time_profiles": {
            "NODE-01": NodeTimeProfile(
                sampling_interval_seconds=60,
                normal_delivery_budget_seconds=60,
            ),
            "NODE-02": NodeTimeProfile(
                sampling_interval_seconds=60,
                normal_delivery_budget_seconds=60,
            ),
        },
    }
    defaults.update(overrides)
    return Settings(**defaults)


class _FakeMonitoringStore:
    def __init__(self):
        self.now = BASE_TIME
        self.ver_seq = 1
        self.next_config_id = 10
        self.next_period_id = 20
        self.next_episode_id = 30
        self.farms = {1: {"farm_id": 1, "archived_at": None}}
        self.zones = {11: {"zone_id": 11, "farm_id": 1, "archived_at": None}}
        self.gateways = {
            201: {
                "gateway_id": 201,
                "farm_id": 1,
                "receiving_enabled": True,
                "row_ver": (1).to_bytes(8, "big"),
            },
            202: {
                "gateway_id": 202,
                "farm_id": 1,
                "receiving_enabled": True,
                "row_ver": (2).to_bytes(8, "big"),
            },
        }
        self.nodes = {
            101: {
                "node_id": 101,
                "device_code": "NODE-01",
                "zone_id": 11,
                "gateway_id": 201,
                "receiving_enabled": True,
                "row_ver": (3).to_bytes(8, "big"),
            },
            102: {
                "node_id": 102,
                "device_code": "NODE-02",
                "zone_id": 11,
                "gateway_id": 201,
                "receiving_enabled": False,
                "row_ver": (4).to_bytes(8, "big"),
            },
            103: {
                "node_id": 103,
                "device_code": "NODE-03",
                "zone_id": 11,
                "gateway_id": None,
                "receiving_enabled": True,
                "row_ver": (5).to_bytes(8, "big"),
            },
        }
        self.ver_seq = 6
        self.runtimes = {
            nid: {
                "node_id": nid,
                "current_config_id": None,
                "current_timeout_config_id": None,
                "active_period_id": None,
                "last_measurement_id": None,
                "last_new_persisted_at": None,
            }
            for nid in self.nodes
        }
        self.eval_states = {
            nid: {
                "node_id": nid,
                "desired_generation": 0,
                "completed_generation": 0,
                "replay_from_t_node": None,
                "full_replay_required": False,
                "last_success_at": None,
                "last_error_code": None,
                "retry_after": None,
                "last_threshold_measurement_id": None,
                "last_threshold_event_at": None,
                "threshold_checkpoint_json": None,
                "timeout_last_error_code": None,
                "timeout_retry_after": None,
            }
            for nid in self.nodes
        }
        self.configs: dict[int, dict] = {}
        self.threshold_settings: dict[int, dict[str, dict]] = {}
        self.periods: dict[int, dict] = {}
        self.measurements: list[dict] = []
        self.episodes: dict[int, dict] = {}
        self.revisions: dict[tuple[int, int], dict] = {}
        self.fail_on_node_bump: int | None = None

    def next_row_ver(self) -> bytes:
        self.ver_seq += 1
        return self.ver_seq.to_bytes(8, "big")


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def _install_fake_backend(monkeypatch, settings=None):
    cfg = settings or _settings()
    app = create_app(cfg)
    store = _FakeMonitoringStore()

    @contextmanager
    def fake_tx(
        database,
        s,
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
        csrf_needed = write if require_csrf is None else require_csrf
        if csrf_needed and csrf != "valid-csrf":
            from app.core.errors import WebError

            raise WebError(403, "CSRF_INVALID", "CSRF_INVALID")
        snap = deepcopy(store.__dict__)
        try:
            yield store, {"admin_id": 1, "db_now": store.now}
        except Exception:
            store.__dict__.clear()
            store.__dict__.update(snap)
            raise

    monkeypatch.setattr(mc_service, "web_session_transaction", fake_tx)

    # Alerts repository bindings
    monkeypatch.setattr(mc_service.alerts_repo, "get_db_utc_now", lambda _s: store.now)
    monkeypatch.setattr(
        mc_service.alerts_repo,
        "lock_node_runtime",
        lambda _s, nid: store.runtimes.get(int(nid)),
    )
    monkeypatch.setattr(
        mc_service.alerts_repo,
        "get_node_runtime",
        lambda _s, nid: store.runtimes.get(int(nid)),
    )
    monkeypatch.setattr(
        mc_service.alerts_repo,
        "lock_evaluation_state",
        lambda _s, nid: store.eval_states.get(int(nid)),
    )
    monkeypatch.setattr(
        mc_service.alerts_repo,
        "get_evaluation_state",
        lambda _s, nid: store.eval_states.get(int(nid)),
    )

    def _lock_open_timeout(_s, nid):
        for ep in store.episodes.values():
            if (
                ep["node_id"] == int(nid)
                and ep["alert_kind"] == "TIMEOUT"
                and ep["is_current"]
                and ep["lifecycle_status"] == "OPEN"
            ):
                return ep
        return None

    def _lock_open_threshold(_s, nid, metric_code):
        for ep in store.episodes.values():
            if (
                ep["node_id"] == int(nid)
                and ep["alert_kind"] == "THRESHOLD"
                and ep["metric_code"] == metric_code
                and ep["is_current"]
                and ep["lifecycle_status"] == "OPEN"
            ):
                return ep
        return None

    monkeypatch.setattr(mc_service.alerts_repo, "lock_open_timeout_episode", _lock_open_timeout)
    monkeypatch.setattr(mc_service.alerts_repo, "lock_open_threshold_episode", _lock_open_threshold)
    monkeypatch.setattr(
        mc_service.alerts_repo,
        "get_alert_revision",
        lambda _s, eid, rno: store.revisions.get((int(eid), int(rno))),
    )

    def _find_after(_s, nid, baseline):
        floor = 0 if baseline is None else int(baseline)
        cands = [
            m
            for m in store.measurements
            if m["node_id"] == int(nid) and m["measurement_id"] > floor
        ]
        cands.sort(key=lambda m: m["measurement_id"])
        return cands[0] if cands else None

    monkeypatch.setattr(mc_service.alerts_repo, "find_first_measurement_after", _find_after)

    def _insert_ep(_s, **kwargs):
        eid = store.next_episode_id
        store.next_episode_id += 1
        store.episodes[eid] = {
            "episode_id": eid,
            "node_id": kwargs["node_id"],
            "alert_kind": kwargs["alert_kind"],
            "metric_code": kwargs["metric_code"],
            "lifecycle_status": kwargs["lifecycle_status"],
            "is_current": kwargs["is_current"],
            "current_revision_no": None,
            "first_detected_at": kwargs["first_detected_at"],
            "acknowledgement_version": kwargs["acknowledgement_version"],
        }
        return eid

    def _insert_rev(_s, **kwargs):
        store.revisions[(kwargs["episode_id"], kwargs["revision_no"])] = dict(kwargs)

    def _upd_ep(
        _s,
        *,
        episode_id,
        current_revision_no,
        lifecycle_status=None,
        is_current=None,
        acknowledgement_version=None,
    ):
        ep = store.episodes[episode_id]
        ep["current_revision_no"] = current_revision_no
        if lifecycle_status is not None:
            ep["lifecycle_status"] = lifecycle_status
        if is_current is not None:
            ep["is_current"] = is_current
        if acknowledgement_version is not None:
            ep["acknowledgement_version"] = acknowledgement_version
        return 1

    monkeypatch.setattr(mc_service.alerts_repo, "insert_alert_episode", _insert_ep)
    monkeypatch.setattr(mc_service.alerts_repo, "insert_alert_revision", _insert_rev)
    monkeypatch.setattr(mc_service.alerts_repo, "update_alert_episode_pointer", _upd_ep)

    def _open_timeout_period(_s, nid, *, lock=False):
        for p in store.periods.values():
            if (
                p["node_id"] == int(nid)
                and p["monitoring_kind"] == "TIMEOUT"
                and p["ended_at"] is None
            ):
                return p
        return None

    def _insert_period(_s, **kwargs):
        pid = store.next_period_id
        store.next_period_id += 1
        store.periods[pid] = {
            "period_id": pid,
            "node_id": kwargs["node_id"],
            "monitoring_kind": kwargs["monitoring_kind"],
            "metric_code": kwargs["metric_code"],
            "started_at": kwargs["started_at"],
            "ended_at": None,
            "start_config_id": kwargs["start_config_id"],
            "start_reason": kwargs["start_reason"],
            "end_reason": None,
        }
        return pid

    def _close_period(_s, *, period_id, node_id, ended_at, end_reason):
        p = store.periods[period_id]
        assert p["node_id"] == node_id and p["ended_at"] is None
        p["ended_at"] = ended_at
        p["end_reason"] = end_reason
        return 1

    monkeypatch.setattr(mc_service.alerts_repo, "get_open_timeout_period", _open_timeout_period)
    monkeypatch.setattr(
        mc_service.alerts_repo,
        "get_timeout_config_by_id",
        lambda _s, nid, cid: (
            store.configs[cid]
            if cid in store.configs
            and store.configs[cid]["node_id"] == nid
            and store.configs[cid]["config_kind"] == "TIMEOUT"
            else None
        ),
    )
    monkeypatch.setattr(mc_service.alerts_repo, "insert_monitoring_period", _insert_period)
    monkeypatch.setattr(mc_service.alerts_repo, "close_monitoring_period", _close_period)
    monkeypatch.setattr(
        mc_service.alerts_repo,
        "update_node_runtime_active_period",
        lambda _s, *, node_id, active_period_id: (
            store.runtimes[node_id].__setitem__("active_period_id", active_period_id) or 1
        ),
    )

    # timeout_engine.repo uses same references (already patched on alerts_repo)
    assert timeout_engine.repo is mc_service.alerts_repo
    monkeypatch.setattr(
        timeout_engine.areas_repo,
        "get_farm",
        lambda _s, fid, lock=False: store.farms.get(int(fid)),
    )
    monkeypatch.setattr(
        timeout_engine.areas_repo,
        "get_zone",
        lambda _s, zid, lock=False: store.zones.get(int(zid)),
    )

    # Devices & monitoring_config repository bindings
    monkeypatch.setattr(
        mc_service.devices_repo,
        "resolve_node_parents",
        lambda _s, nid: (
            {
                "node_id": int(nid),
                "zone_id": store.nodes[int(nid)]["zone_id"],
                "farm_id": store.zones[store.nodes[int(nid)]["zone_id"]]["farm_id"],
                "gateway_id": store.nodes[int(nid)]["gateway_id"],
            }
            if int(nid) in store.nodes
            else None
        ),
    )
    monkeypatch.setattr(
        mc_service.devices_repo,
        "resolve_gateway_farm_id",
        lambda _s, gid: store.gateways[int(gid)]["farm_id"] if int(gid) in store.gateways else None,
    )
    monkeypatch.setattr(
        mc_service.devices_repo,
        "lock_gateway",
        lambda _s, gid: store.gateways.get(int(gid)),
    )
    monkeypatch.setattr(
        mc_service.devices_repo,
        "lock_node",
        lambda _s, nid: store.nodes.get(int(nid)),
    )

    def _get_node_ctx(_s, nid):
        n = store.nodes.get(int(nid))
        if n is None:
            return None
        z = store.zones[n["zone_id"]]
        f = store.farms[z["farm_id"]]
        gw = store.gateways.get(n["gateway_id"]) if n["gateway_id"] is not None else None
        return {
            **n,
            "farm_id": z["farm_id"],
            "zone_archived_at": z["archived_at"],
            "farm_archived_at": f["archived_at"],
            "gateway_farm_id": gw["farm_id"] if gw else None,
            "gateway_receiving_enabled": gw["receiving_enabled"] if gw else None,
        }

    def _resolve_gw_ctx(_s, gid):
        gw = store.gateways.get(int(gid))
        if gw is None:
            return None
        linked_nodes = [n for n in store.nodes.values() if n["gateway_id"] == int(gid)]
        zone_ids = sorted({n["zone_id"] for n in linked_nodes})
        farm_ids = sorted({gw["farm_id"], *(store.zones[zid]["farm_id"] for zid in zone_ids)})
        return {
            "gateway_id": int(gid),
            "farm_id": gw["farm_id"],
            "farm_ids": farm_ids,
            "zone_ids": zone_ids,
        }

    monkeypatch.setattr(mc_repo, "get_node_context", _get_node_ctx)
    monkeypatch.setattr(mc_repo, "resolve_gateway_context", _resolve_gw_ctx)
    monkeypatch.setattr(
        mc_repo,
        "lock_farms_ordered",
        lambda _s, fids: {fid: store.farms[fid] for fid in sorted(set(fids)) if fid in store.farms},
    )
    monkeypatch.setattr(
        mc_repo,
        "lock_zones_ordered",
        lambda _s, zids: {zid: store.zones[zid] for zid in sorted(set(zids)) if zid in store.zones},
    )
    monkeypatch.setattr(
        mc_repo,
        "lock_gateway_nodes",
        lambda _s, gid: sorted(
            [n for n in store.nodes.values() if n["gateway_id"] == int(gid)],
            key=lambda n: n["node_id"],
        ),
    )

    def _upd_node_recv(_s, *, node_id, expected_version, enabled):
        n = store.nodes[int(node_id)]
        if n["row_ver"] != expected_version:
            return None
        n["receiving_enabled"] = enabled
        n["row_ver"] = store.next_row_ver()
        return n

    def _upd_gw_recv(_s, *, gateway_id, expected_version, enabled):
        gw = store.gateways[int(gateway_id)]
        if gw["row_ver"] != expected_version:
            return None
        gw["receiving_enabled"] = enabled
        gw["row_ver"] = store.next_row_ver()
        return gw

    monkeypatch.setattr(mc_repo, "update_node_receiving", _upd_node_recv)
    monkeypatch.setattr(mc_repo, "update_gateway_receiving", _upd_gw_recv)

    def _get_cur_cfg(_s, *, node_id, config_kind, lock=False):
        for cfg_row in store.configs.values():
            if (
                cfg_row["node_id"] == int(node_id)
                and cfg_row["config_kind"] == config_kind
                and cfg_row["effective_to"] is None
            ):
                return cfg_row
        return None

    def _get_th_settings(_s, cid):
        return store.threshold_settings[int(cid)]

    def _get_open_th_periods(_s, nid, *, lock=False):
        return {
            p["metric_code"]: p
            for p in store.periods.values()
            if p["node_id"] == int(nid)
            and p["monitoring_kind"] == "THRESHOLD"
            and p["ended_at"] is None
        }

    def _close_cfg(_s, *, config_id, node_id, config_kind, effective_to):
        c = store.configs[int(config_id)]
        assert c["node_id"] == int(node_id) and c["config_kind"] == config_kind
        assert c["effective_to"] is None
        c["effective_to"] = effective_to
        return 1

    def _max_cfg_no(_s, *, node_id, config_kind):
        nos = [
            c["config_no"]
            for c in store.configs.values()
            if c["node_id"] == int(node_id) and c["config_kind"] == config_kind
        ]
        return max(nos) if nos else 0

    def _insert_cfg(
        _s,
        *,
        node_id,
        config_kind,
        config_no,
        timeout_minutes,
        effective_from,
        created_by,
        change_reason,
    ):
        cid = store.next_config_id
        store.next_config_id += 1
        store.configs[cid] = {
            "config_id": cid,
            "node_id": int(node_id),
            "config_kind": config_kind,
            "config_no": int(config_no),
            "timeout_minutes": timeout_minutes,
            "effective_from": effective_from,
            "effective_to": None,
            "created_by": created_by,
            "change_reason": change_reason,
        }
        return cid

    def _insert_th_settings(_s, *, config_id, settings_by_metric):
        store.threshold_settings[int(config_id)] = {
            m: {
                "metric_code": m,
                "enabled": settings_by_metric[m]["enabled"],
                "lower_value": settings_by_metric[m]["lower_value"],
                "upper_value": settings_by_metric[m]["upper_value"],
            }
            for m in METRIC_ORDER
        }
        return 5

    def _upd_rt_cfg(_s, *, node_id, column, config_id):
        store.runtimes[int(node_id)][column] = int(config_id)
        return 1

    def _bump_gen(_s, *, node_id, full_replay):
        if store.fail_on_node_bump == int(node_id):
            raise OperationalError("injected", {}, Exception("simulated SQL fault"))
        es = store.eval_states[int(node_id)]
        es["desired_generation"] += 1
        if full_replay:
            es["full_replay_required"] = True
        return es["desired_generation"]

    monkeypatch.setattr(mc_repo, "get_current_config_version", _get_cur_cfg)
    monkeypatch.setattr(mc_repo, "get_threshold_settings", _get_th_settings)
    monkeypatch.setattr(mc_repo, "get_open_threshold_periods", _get_open_th_periods)
    monkeypatch.setattr(mc_repo, "close_current_config_version", _close_cfg)
    monkeypatch.setattr(mc_repo, "get_max_config_no", _max_cfg_no)
    monkeypatch.setattr(mc_repo, "insert_config_version", _insert_cfg)
    monkeypatch.setattr(mc_repo, "insert_threshold_settings", _insert_th_settings)
    monkeypatch.setattr(mc_repo, "update_node_runtime_config_pointer", _upd_rt_cfg)
    monkeypatch.setattr(mc_repo, "bump_evaluation_generation", _bump_gen)

    client = TestClient(app)
    client.cookies.set("hq_session", "valid-cookie")
    return client, store


def test_auth_csrf_shape_and_not_found(monkeypatch):
    client, _ = _install_fake_backend(monkeypatch)

    # Missing/invalid session -> 401
    unauth = TestClient(client.app)
    assert unauth.get("/api/v1/nodes/101/monitoring").status_code == 401
    assert (
        unauth.patch(
            "/api/v1/nodes/101/monitoring",
            headers=WEB_HEADERS,
            json={"expected_timeout_config_id": None, "timeout_minutes": 5},
        ).status_code
        == 401
    )

    # Bad CSRF / Origin -> 403
    assert (
        client.patch(
            "/api/v1/nodes/101/receiving",
            headers={"Origin": "http://127.0.0.1:5173", "X-CSRF-Token": "wrong"},
            json={"receiving_enabled": False, "expected_row_version": _b64((3).to_bytes(8, "big"))},
        ).status_code
        == 403
    )

    # Unexpected query param -> 400
    assert client.get("/api/v1/nodes/101/monitoring?foo=bar").status_code == 400
    # Missing Node/Gateway -> 404
    assert client.get("/api/v1/nodes/999/monitoring").status_code == 404
    assert (
        client.patch(
            "/api/v1/gateways/999/receiving",
            headers=WEB_HEADERS,
            json={"receiving_enabled": False, "expected_row_version": _b64((1).to_bytes(8, "big"))},
        ).status_code
        == 404
    )


def test_g4_d07_http_422_and_strict_400_validation(monkeypatch):
    client, _ = _install_fake_backend(monkeypatch)

    # G4-D07: Non-finite or non-DECIMAL(9,2) values on lower/upper -> 422 INPUT_INVALID
    raw_422_payloads = [
        (
            '{"expected_threshold_config_id":null,'
            '"thresholds":{"temperature":{"lower":NaN,"upper":30}}}',
            "thresholds.temperature.lower",
        ),
        (
            '{"expected_threshold_config_id":null,'
            '"thresholds":{"temperature":{"lower":10,"upper":Infinity}}}',
            "thresholds.temperature.upper",
        ),
        (
            '{"expected_threshold_config_id":null,'
            '"thresholds":{"temperature":{"lower":-Infinity,"upper":30}}}',
            "thresholds.temperature.lower",
        ),
        (
            '{"expected_threshold_config_id":null,'
            '"thresholds":{"light":{"lower":12.345,"upper":100}}}',
            "thresholds.light.lower",
        ),
        (
            '{"expected_threshold_config_id":null,'
            '"thresholds":{"soilMoisture":{"lower":10,"upper":1e-3}}}',
            "thresholds.soilMoisture.upper",
        ),
        (
            '{"expected_threshold_config_id":null,'
            '"thresholds":{"pressure":{"lower":300,"upper":10000000}}}',
            "thresholds.pressure.upper",
        ),
        (
            '{"expected_threshold_config_id":null,'
            '"thresholds":{"pressure":{"lower":300,"upper":1e1000}}}',
            "thresholds.pressure.upper",
        ),
    ]
    for raw_body, expected_field in raw_422_payloads:
        res = client.patch(
            "/api/v1/nodes/101/monitoring",
            headers={**WEB_HEADERS, "Content-Type": "application/json"},
            content=raw_body,
        )
        assert res.status_code == 422, raw_body
        err = res.json()["error"]
        assert err["code"] == "INPUT_INVALID"
        assert err["retryable"] is False
        assert err["details"] == {
            "reason": "THRESHOLD_DECIMAL_INVALID",
            "field": expected_field,
        }
        assert "12.345" not in err["message"]

    # G4-D07: Malformed JSON, NaN in T, bad types, out-of-domain, bad pairs, null -> 400
    bad_400_payloads = [
        "{bad json",
        '{"expected_timeout_config_id":null,"timeout_minutes":NaN}',
        '{"expected_timeout_config_id":null,"timeout_minutes":true}',
        '{"expected_timeout_config_id":null,"timeout_minutes":0}',
        '{"expected_timeout_config_id":null,"timeout_minutes":5,"timeout_minutes":6}',
        '{"timeout_minutes":5}',
        '{"expected_threshold_config_id":null,"thresholds":{}}',
        '{"expected_threshold_config_id":null,"thresholds":{"unknown":{"lower":1,"upper":2}}}',
        '{"expected_threshold_config_id":null,"thresholds":{"temperature":{"enabled":1}}}',
        '{"expected_threshold_config_id":null,"thresholds":{"temperature":{"lower":"10","upper":20}}}',
        '{"expected_threshold_config_id":null,"thresholds":{"temperature":{"lower":null,"upper":20}}}',
        '{"expected_threshold_config_id":null,"thresholds":{"temperature":{"lower":-40.01,"upper":20}}}',
        '{"expected_threshold_config_id":null,"thresholds":{"light":{"lower":10,"upper":65535.01}}}',
        '{"expected_threshold_config_id":null,"thresholds":{"temperature":{"lower":20,"upper":20}}}',
        '{"expected_threshold_config_id":null,"thresholds":{"temperature":{"lower":10}}}',
        '{"expected_threshold_config_id":null,"thresholds":{"temperature":{"enabled":true}}}',
    ]
    for raw_body in bad_400_payloads:
        res = client.patch(
            "/api/v1/nodes/101/monitoring",
            headers={**WEB_HEADERS, "Content-Type": "application/json"},
            content=raw_body,
        )
        assert res.status_code == 400, f"Expected 400 for {raw_body}, got {res.status_code}"
        assert res.json()["error"]["code"] == "INPUT_INVALID"


def test_g4_d03_two_decimals_all_metrics_and_fl08_lifecycle(monkeypatch):
    client, store = _install_fake_backend(monkeypatch)

    # Initial GET -> all UNCONFIGURED, READY profile
    r0 = client.get("/api/v1/nodes/101/monitoring")
    assert r0.status_code == 200
    b0 = r0.json()
    assert b0["threshold_config_id"] is None
    assert b0["timeout_config_id"] is None
    assert b0["timeout_state"] == "UNCONFIGURED"
    assert [t["metric_code"] for t in b0["thresholds"]] == list(METRIC_ORDER)
    assert all(t["state"] == "UNCONFIGURED" for t in b0["thresholds"])
    assert b0["timeout_validation"] == {
        "state": "READY",
        "sampling_interval_seconds": 60,
        "normal_delivery_budget_seconds": 60,
        "min_timeout_minutes": 3,
    }

    # G4-D03: save 2-decimal thresholds for light and soilMoisture + enable temperature
    r1 = client.patch(
        "/api/v1/nodes/101/monitoring",
        headers=WEB_HEADERS,
        json={
            "expected_threshold_config_id": None,
            "thresholds": {
                "temperature": {"enabled": True, "lower": 18, "upper": 35.5},
                "light": {"enabled": True, "lower": 100.25, "upper": 50000.75},
                "soilMoisture": {"enabled": False, "lower": 12.34, "upper": 88.99},
            },
        },
    )
    assert r1.status_code == 200
    b1 = r1.json()
    th_cfg_1 = b1["threshold_config_id"]
    assert th_cfg_1 is not None
    assert b1["timeout_config_id"] is None
    by_m = {t["metric_code"]: t for t in b1["thresholds"]}
    assert by_m["temperature"] == {
        "metric_code": "temperature",
        "enabled": True,
        "lower": "18.00",
        "upper": "35.50",
        "state": "ACTIVE",
    }
    assert by_m["light"]["lower"] == "100.25"
    assert by_m["light"]["upper"] == "50000.75"
    assert by_m["light"]["state"] == "ACTIVE"
    assert by_m["soilMoisture"]["lower"] == "12.34"
    assert by_m["soilMoisture"]["upper"] == "88.99"
    assert by_m["soilMoisture"]["state"] == "DISABLED"
    assert by_m["airHumidity"]["state"] == "UNCONFIGURED"
    assert b1["evaluation"]["desired_generation"] == "1"
    assert b1["evaluation"]["status"] == "PENDING"
    assert store.eval_states[101]["full_replay_required"] is True

    # Mark threshold replay completed to isolate T-only check (E08-04)
    store.eval_states[101]["completed_generation"] = 1
    store.eval_states[101]["full_replay_required"] = False

    # Boundary check on T: sampling=60 + budget=60 = 120s -> T=2 (120s) fails, T=3 (180s) succeeds
    r_bad_t = client.patch(
        "/api/v1/nodes/101/monitoring",
        headers=WEB_HEADERS,
        json={"expected_timeout_config_id": None, "timeout_minutes": 2},
    )
    assert r_bad_t.status_code == 400
    assert r_bad_t.json()["error"]["code"] == "INPUT_INVALID"
    assert r_bad_t.json()["error"]["details"]["reason"] == "TIMEOUT_PROFILE_INCOMPATIBLE"

    # T-only sets T=5 without touching threshold config or full_replay_required
    r_t = client.patch(
        "/api/v1/nodes/101/monitoring",
        headers=WEB_HEADERS,
        json={"expected_timeout_config_id": None, "timeout_minutes": 5},
    )
    assert r_t.status_code == 200
    bt = r_t.json()
    t_cfg_1 = bt["timeout_config_id"]
    assert t_cfg_1 is not None
    assert bt["threshold_config_id"] == th_cfg_1
    assert bt["timeout_state"] == "ACTIVE"
    assert bt["S"] == "2026-11-20T10:00:00.000Z"
    assert bt["evaluation"]["desired_generation"] == "2"
    assert bt["evaluation"]["status"] == "PENDING"
    assert store.eval_states[101]["full_replay_required"] is False

    # Update T continuously at 10:03 -> keeps S=10:00, L, and period_id
    store.now = BASE_TIME + timedelta(minutes=3)
    r_t2 = client.patch(
        "/api/v1/nodes/101/monitoring",
        headers=WEB_HEADERS,
        json={"expected_timeout_config_id": t_cfg_1, "timeout_minutes": 10},
    )
    assert r_t2.status_code == 200
    bt2 = r_t2.json()
    t_cfg_2 = bt2["timeout_config_id"]
    assert t_cfg_2 != t_cfg_1
    assert bt2["S"] == "2026-11-20T10:00:00.000Z"
    assert bt2["active_timeout_period_id"] == bt["active_timeout_period_id"]

    # Stale expected_timeout_config_id checked BEFORE no-op (E08-01 ABA)
    r_stale = client.patch(
        "/api/v1/nodes/101/monitoring",
        headers=WEB_HEADERS,
        json={"expected_timeout_config_id": t_cfg_1, "timeout_minutes": 10},
    )
    assert r_stale.status_code == 409
    assert r_stale.json()["error"]["code"] == "CONFIG_VERSION_CONFLICT"
    assert r_stale.json()["error"]["details"] == {"current_timeout_config_id": t_cfg_2}

    # Valid no-op does not create new config or bump generation
    gen_before = store.eval_states[101]["desired_generation"]
    r_noop = client.patch(
        "/api/v1/nodes/101/monitoring",
        headers=WEB_HEADERS,
        json={"expected_timeout_config_id": t_cfg_2, "timeout_minutes": 10},
    )
    assert r_noop.status_code == 200
    assert r_noop.json()["timeout_config_id"] == t_cfg_2
    assert store.eval_states[101]["desired_generation"] == gen_before

    # Update bounds while enabled -> does NOT split open THRESHOLD period
    old_light_period = [
        p["period_id"]
        for p in store.periods.values()
        if p["node_id"] == 101 and p["metric_code"] == "light" and p["ended_at"] is None
    ][0]
    r_bounds = client.patch(
        "/api/v1/nodes/101/monitoring",
        headers=WEB_HEADERS,
        json={
            "expected_threshold_config_id": th_cfg_1,
            "thresholds": {"light": {"upper": 45000.5}},
        },
    )
    assert r_bounds.status_code == 200
    th_cfg_2 = r_bounds.json()["threshold_config_id"]
    new_light_period = [
        p["period_id"]
        for p in store.periods.values()
        if p["node_id"] == 101 and p["metric_code"] == "light" and p["ended_at"] is None
    ][0]
    assert new_light_period == old_light_period

    # Seed OPEN THRESHOLD episode on light, then disable -> THRESHOLD_DISABLED + keep bounds
    ep_id = 900
    store.episodes[ep_id] = {
        "episode_id": ep_id,
        "node_id": 101,
        "alert_kind": "THRESHOLD",
        "metric_code": "light",
        "lifecycle_status": "OPEN",
        "is_current": True,
        "current_revision_no": 1,
        "first_detected_at": BASE_TIME + timedelta(minutes=1),
        "acknowledgement_version": 1,
    }
    store.revisions[(ep_id, 1)] = {
        "episode_id": ep_id,
        "revision_no": 1,
        "config_id": int(th_cfg_2),
        "period_id": old_light_period,
        "direction": "HIGH",
        "start_event_time": 1794384060,
        "end_event_time": None,
        "start_backend_at": None,
        "end_backend_at": None,
        "closed_at": None,
        "end_reason": None,
        "start_measurement_id": 1,
        "end_measurement_id": None,
        "lower_snapshot": Decimal("100.25"),
        "upper_snapshot": Decimal("45000.50"),
        "timeout_snapshot": None,
        "evaluated_at": BASE_TIME + timedelta(minutes=1),
        "evaluation_generation": 1,
        "acknowledgement_version": 1,
        "threshold_metadata_json": '{"codec":1,"is_current":true,"transition":"PROGRESS"}',
    }
    store.now = BASE_TIME + timedelta(minutes=4)
    r_dis = client.patch(
        "/api/v1/nodes/101/monitoring",
        headers=WEB_HEADERS,
        json={
            "expected_threshold_config_id": th_cfg_2,
            "thresholds": {"light": {"enabled": False}},
        },
    )
    assert r_dis.status_code == 200
    light_dto = {t["metric_code"]: t for t in r_dis.json()["thresholds"]}["light"]
    assert light_dto == {
        "metric_code": "light",
        "enabled": False,
        "lower": "100.25",
        "upper": "45000.50",
        "state": "DISABLED",
    }
    assert store.periods[old_light_period]["end_reason"] == "THRESHOLD_DISABLED"
    assert store.episodes[ep_id]["lifecycle_status"] == "CLOSED"
    assert store.episodes[ep_id]["current_revision_no"] == 2
    assert store.revisions[(ep_id, 2)]["end_reason"] == "THRESHOLD_DISABLED"
    assert store.revisions[(ep_id, 2)]["end_measurement_id"] is None
    assert store.revisions[(ep_id, 2)]["end_event_time"] is None


def test_fl08_atomic_rollback_and_profile_missing_behavior(monkeypatch):
    # NODE-03 has no profile in settings
    client, store = _install_fake_backend(monkeypatch)
    # Give NODE-03 a pre-existing saved timeout=5 (e.g. profile was removed later)
    store.configs[88] = {
        "config_id": 88,
        "node_id": 103,
        "config_kind": "TIMEOUT",
        "config_no": 1,
        "timeout_minutes": 5,
        "effective_from": BASE_TIME,
        "effective_to": None,
        "created_by": 1,
        "change_reason": "USER_UPDATE",
    }
    store.runtimes[103]["current_timeout_config_id"] = 88

    g = client.get("/api/v1/nodes/103/monitoring")
    assert g.status_code == 200
    assert g.json()["timeout_validation"]["state"] == "PROFILE_MISSING"

    # Missing profile on T change -> 400 PROFILE_MISSING; combined edit rolls back too!
    r_comb = client.patch(
        "/api/v1/nodes/103/monitoring",
        headers=WEB_HEADERS,
        json={
            "expected_threshold_config_id": None,
            "thresholds": {"temperature": {"enabled": True, "lower": 10, "upper": 30}},
            "expected_timeout_config_id": "88",
            "timeout_minutes": 10,
        },
    )
    assert r_comb.status_code == 400
    assert r_comb.json()["error"]["code"] == "PROFILE_MISSING"
    assert store.runtimes[103]["current_config_id"] is None
    assert store.runtimes[103]["current_timeout_config_id"] == 88

    # Valid no-op on T succeeds even with PROFILE_MISSING, and threshold-only succeeds independently
    r_noop = client.patch(
        "/api/v1/nodes/103/monitoring",
        headers=WEB_HEADERS,
        json={"expected_timeout_config_id": "88", "timeout_minutes": 5},
    )
    assert r_noop.status_code == 200
    r_th = client.patch(
        "/api/v1/nodes/103/monitoring",
        headers=WEB_HEADERS,
        json={
            "expected_threshold_config_id": None,
            "thresholds": {"temperature": {"enabled": True, "lower": 10, "upper": 30}},
        },
    )
    assert r_th.status_code == 200
    assert r_th.json()["threshold_config_id"] is not None

    # Profile present for NODE-01, saved T=3 < min_timeout_minutes=5 -> INCOMPATIBLE
    client2, store2 = _install_fake_backend(
        monkeypatch,
        _settings(
            node_time_profiles={
                "NODE-01": NodeTimeProfile(
                    sampling_interval_seconds=240,
                    normal_delivery_budget_seconds=30,
                )
            }
        ),
    )
    store2.configs[77] = {
        "config_id": 77,
        "node_id": 101,
        "config_kind": "TIMEOUT",
        "config_no": 1,
        "timeout_minutes": 3,
        "effective_from": BASE_TIME,
        "effective_to": None,
        "created_by": 1,
        "change_reason": "USER_UPDATE",
    }
    store2.runtimes[101]["current_timeout_config_id"] = 77
    g2 = client2.get("/api/v1/nodes/101/monitoring")
    assert g2.status_code == 200
    assert g2.json()["timeout_validation"] == {
        "state": "TIMEOUT_PROFILE_INCOMPATIBLE",
        "sampling_interval_seconds": 240,
        "normal_delivery_budget_seconds": 30,
        "min_timeout_minutes": 5,
    }


def test_fl04_node_and_gateway_receiving_fanout_and_rollback(monkeypatch):
    client, store = _install_fake_backend(monkeypatch)

    # Configure both threshold & timeout on NODE-01 (node 101)
    r_cfg = client.patch(
        "/api/v1/nodes/101/monitoring",
        headers=WEB_HEADERS,
        json={
            "expected_threshold_config_id": None,
            "thresholds": {"temperature": {"enabled": True, "lower": 15, "upper": 30}},
            "expected_timeout_config_id": None,
            "timeout_minutes": 5,
        },
    )
    assert r_cfg.status_code == 200

    # Strict bool/version validation
    assert (
        client.patch(
            "/api/v1/nodes/101/receiving",
            headers=WEB_HEADERS,
            json={
                "receiving_enabled": 0,
                "expected_row_version": _b64(store.nodes[101]["row_ver"]),
            },
        ).status_code
        == 400
    )

    # Valid no-op on node 101 preserves row_ver and generation
    ver_101 = _b64(store.nodes[101]["row_ver"])
    gen_101 = store.eval_states[101]["desired_generation"]
    r_noop = client.patch(
        "/api/v1/nodes/101/receiving",
        headers=WEB_HEADERS,
        json={"receiving_enabled": True, "expected_row_version": ver_101},
    )
    assert r_noop.status_code == 200
    assert r_noop.json()["profile_row_version"] == ver_101
    assert store.eval_states[101]["desired_generation"] == gen_101

    # Seed OPEN TIMEOUT episode at 10:05 and witness M=50 at 10:06 before pausing at 10:08
    store.now = BASE_TIME + timedelta(minutes=5)
    timeout_engine.evaluate_timeout_node(None, node_id=101, settings=client.app.state.settings)
    store.measurements.append(
        {"measurement_id": 50, "node_id": 101, "persisted_at": BASE_TIME + timedelta(minutes=6)}
    )
    store.runtimes[101]["last_measurement_id"] = 50
    store.runtimes[101]["last_new_persisted_at"] = BASE_TIME + timedelta(minutes=6)
    store.now = BASE_TIME + timedelta(minutes=8)

    # Also seed an OPEN THRESHOLD episode on NODE-01 (must remain OPEN across pause)
    store.episodes[500] = {
        "episode_id": 500,
        "node_id": 101,
        "alert_kind": "THRESHOLD",
        "metric_code": "temperature",
        "lifecycle_status": "OPEN",
        "is_current": True,
        "current_revision_no": 1,
        "first_detected_at": BASE_TIME + timedelta(minutes=2),
        "acknowledgement_version": 1,
    }
    store.revisions[(500, 1)] = {"episode_id": 500, "revision_no": 1, "end_reason": None}

    # Disable Gateway 201 at C=10:08: 101 closes witness FIRST, leaves 102 untouched
    gw_ver = _b64(store.gateways[201]["row_ver"])
    r_gw_off = client.patch(
        "/api/v1/gateways/201/receiving",
        headers=WEB_HEADERS,
        json={"receiving_enabled": False, "expected_row_version": gw_ver},
    )
    assert r_gw_off.status_code == 200
    bg_off = r_gw_off.json()
    assert bg_off["receiving_enabled"] is False
    assert [n["node_id"] for n in bg_off["affected_nodes"]] == ["101", "102"]
    n101 = bg_off["affected_nodes"][0]
    assert n101["timeout_state"] == "PAUSED"
    assert n101["S"] is None
    assert n101["L"] == "2026-11-20T10:06:00.000Z"
    assert n101["thresholds"][0]["state"] == "PAUSED"
    # Witness closed timeout episode as NEW_DATA_STORED before pause!
    assert store.episodes[30]["lifecycle_status"] == "CLOSED"
    assert store.revisions[(30, 2)]["end_reason"] == "NEW_DATA_STORED"
    assert store.revisions[(30, 2)]["end_measurement_id"] == 50
    # THRESHOLD episode stays OPEN across pause
    assert store.episodes[500]["lifecycle_status"] == "OPEN"
    # NODE-102 was individually disabled -> untouched
    assert store.eval_states[102]["desired_generation"] == 0

    # Stale ABA version check on Gateway 201 -> 409 VERSION_CONFLICT
    r_gw_stale = client.patch(
        "/api/v1/gateways/201/receiving",
        headers=WEB_HEADERS,
        json={"receiving_enabled": False, "expected_row_version": gw_ver},
    )
    assert r_gw_stale.status_code == 409
    assert r_gw_stale.json()["error"]["code"] == "VERSION_CONFLICT"

    # Fault during Node 101 bump when re-enabling Gateway -> rolls back Gateway + Node 101!
    store.fail_on_node_bump = 101
    r_fault = client.patch(
        "/api/v1/gateways/201/receiving",
        headers=WEB_HEADERS,
        json={"receiving_enabled": True, "expected_row_version": bg_off["profile_row_version"]},
    )
    assert r_fault.status_code == 503
    assert store.gateways[201]["receiving_enabled"] is False
    assert store.runtimes[101]["active_period_id"] is None
    store.fail_on_node_bump = None

    # Re-enable Gateway 201 at 10:20 -> opens S=10:20 on 101 (keeps L=10:06), leaves 102 PAUSED
    store.now = BASE_TIME + timedelta(minutes=20)
    r_gw_on = client.patch(
        "/api/v1/gateways/201/receiving",
        headers=WEB_HEADERS,
        json={"receiving_enabled": True, "expected_row_version": bg_off["profile_row_version"]},
    )
    assert r_gw_on.status_code == 200
    n101_on = r_gw_on.json()["affected_nodes"][0]
    assert n101_on["timeout_state"] == "ACTIVE"
    assert n101_on["S"] == "2026-11-20T10:20:00.000Z"
    assert n101_on["L"] == "2026-11-20T10:06:00.000Z"
    assert n101_on["thresholds"][0]["state"] == "ACTIVE"
    assert store.eval_states[102]["desired_generation"] == 0

    # Gateway 202 with 0 linked Nodes -> succeeds with affected_nodes=[]
    r_empty_gw = client.patch(
        "/api/v1/gateways/202/receiving",
        headers=WEB_HEADERS,
        json={
            "receiving_enabled": False,
            "expected_row_version": _b64(store.gateways[202]["row_ver"]),
        },
    )
    assert r_empty_gw.status_code == 200
    assert r_empty_gw.json()["affected_nodes"] == []

    # Archived parent blocks enabling, allows disabling
    store.zones[11]["archived_at"] = store.now
    r_arch_node = client.patch(
        "/api/v1/nodes/102/receiving",
        headers=WEB_HEADERS,
        json={
            "receiving_enabled": True,
            "expected_row_version": _b64(store.nodes[102]["row_ver"]),
        },
    )
    assert r_arch_node.status_code == 409
    assert r_arch_node.json()["error"]["code"] == "AREA_ARCHIVED"
    r_dis_under_arch = client.patch(
        "/api/v1/nodes/101/receiving",
        headers=WEB_HEADERS,
        json={
            "receiving_enabled": False,
            "expected_row_version": _b64(store.nodes[101]["row_ver"]),
        },
    )
    assert r_dis_under_arch.status_code == 200

    # Unpaired node 103 toggles cleanly without opening periods or bumping generation
    r_unpaired = client.patch(
        "/api/v1/nodes/103/receiving",
        headers=WEB_HEADERS,
        json={
            "receiving_enabled": False,
            "expected_row_version": _b64(store.nodes[103]["row_ver"]),
        },
    )
    assert r_unpaired.status_code == 200
    assert r_unpaired.json()["receiving_enabled"] is False
    assert store.eval_states[103]["desired_generation"] == 0


def test_fl08_t_change_reconciles_witness_before_config_changed(monkeypatch):
    client, store = _install_fake_backend(monkeypatch)

    r_init = client.patch(
        "/api/v1/nodes/101/monitoring",
        headers=WEB_HEADERS,
        json={"expected_timeout_config_id": None, "timeout_minutes": 5},
    )
    assert r_init.status_code == 200
    t_cfg_1 = r_init.json()["timeout_config_id"]

    # Case 1: OPEN episode has a persisted witness before T increases (5 -> 15) -> NEW_DATA_STORED
    store.now = BASE_TIME + timedelta(minutes=5)
    timeout_engine.evaluate_timeout_node(None, node_id=101, settings=client.app.state.settings)
    ep1_id = next(iter(store.episodes))
    witness_ts = BASE_TIME + timedelta(minutes=6)
    store.measurements.append({"measurement_id": 70, "node_id": 101, "persisted_at": witness_ts})
    store.runtimes[101]["last_measurement_id"] = 70
    store.runtimes[101]["last_new_persisted_at"] = witness_ts

    store.now = BASE_TIME + timedelta(minutes=8)
    r_up1 = client.patch(
        "/api/v1/nodes/101/monitoring",
        headers=WEB_HEADERS,
        json={"expected_timeout_config_id": t_cfg_1, "timeout_minutes": 15},
    )
    assert r_up1.status_code == 200
    t_cfg_2 = r_up1.json()["timeout_config_id"]
    assert store.episodes[ep1_id]["lifecycle_status"] == "CLOSED"
    rev1_close = store.revisions[(ep1_id, store.episodes[ep1_id]["current_revision_no"])]
    assert rev1_close["end_reason"] == "NEW_DATA_STORED"
    assert rev1_close["end_measurement_id"] == 70
    assert rev1_close["closed_at"] == witness_ts

    # Case 2: OPEN episode has NO witness and T increases (15 -> 25 -> 15? use T=5 -> 15 at E=6m)
    r_down = client.patch(
        "/api/v1/nodes/101/monitoring",
        headers=WEB_HEADERS,
        json={"expected_timeout_config_id": t_cfg_2, "timeout_minutes": 5},
    )
    assert r_down.status_code == 200
    t_cfg_3 = r_down.json()["timeout_config_id"]

    store.now = BASE_TIME + timedelta(minutes=11)
    timeout_engine.evaluate_timeout_node(None, node_id=101, settings=client.app.state.settings)
    ep2_id = max(store.episodes)
    assert ep2_id != ep1_id
    assert store.episodes[ep2_id]["lifecycle_status"] == "OPEN"

    store.now = BASE_TIME + timedelta(minutes=12)
    r_up2 = client.patch(
        "/api/v1/nodes/101/monitoring",
        headers=WEB_HEADERS,
        json={"expected_timeout_config_id": t_cfg_3, "timeout_minutes": 15},
    )
    assert r_up2.status_code == 200
    assert store.episodes[ep2_id]["lifecycle_status"] == "CLOSED"
    rev2_close = store.revisions[(ep2_id, store.episodes[ep2_id]["current_revision_no"])]
    assert rev2_close["end_reason"] == "CONFIG_CHANGED"
    assert rev2_close["end_measurement_id"] is None
    assert rev2_close["closed_at"] == BASE_TIME + timedelta(minutes=12)
