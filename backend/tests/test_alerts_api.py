import json
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import Settings
from app.main import create_app
from app.modules.alerts import service as alerts_service_mod
from app.modules.alerts.threshold_codec import encode_threshold_metadata

NOW = datetime(2026, 11, 21, 12, 0, 0, 0)
WEB_HEADERS = {
    "Origin": "http://127.0.0.1:5173",
    "X-HQ-Client": "web",
    "X-CSRF-Token": "valid-csrf",
    "Content-Type": "application/json",
}


def _settings():
    return Settings(
        _env_file=None,
        env="test",
        csrf_secret=SecretStr("C" * 32),
        cursor_secret=SecretStr("K" * 32),
        web_origin="http://127.0.0.1:5173",
    )


def _fake_alerts_client(monkeypatch):
    cfg = _settings()
    app = create_app(cfg)
    state = {
        "now": NOW,
        "farms": {
            1: {"farm_id": 1, "farm_code": "F1", "name": "Farm 1", "archived_at": None},
            2: {
                "farm_id": 2,
                "farm_code": "F2",
                "name": "Archived Farm",
                "archived_at": NOW - timedelta(days=2),
            },
        },
        "nodes": {
            101: {
                "node_id": 101,
                "device_code": "NODE-01",
                "name": "Node 1",
                "zone_id": 11,
                "farm_id": 1,
                "zone_code": "Z1",
                "zone_name": "Zone 1",
                "zone_archived_at": None,
                "gateway_id": 201,
                "gateway_code": "GW-01",
                "gateway_name": "GW 1",
                "gateway_receiving_enabled": True,
                "receiving_enabled": True,
            },
            201: {
                "node_id": 201,
                "device_code": "NODE-02",
                "name": "Node 2",
                "zone_id": 21,
                "farm_id": 2,
                "zone_code": "Z2",
                "zone_name": "Zone 2",
                "zone_archived_at": NOW - timedelta(days=2),
                "gateway_id": None,
                "gateway_code": None,
                "gateway_name": None,
                "gateway_receiving_enabled": None,
                "receiving_enabled": False,
            },
        },
        "eval_states": {
            101: {
                "node_id": 101,
                "desired_generation": 3,
                "completed_generation": 3,
                "last_success_at": NOW - timedelta(seconds=5),
                "last_error_code": None,
                "timeout_last_error_code": None,
                "threshold_checkpoint_json": '{"codec":1,"secret_internal":true}',
            },
            201: {
                "node_id": 201,
                "desired_generation": 1,
                "completed_generation": 0,
                "last_success_at": None,
                "last_error_code": None,
                "timeout_last_error_code": None,
                "threshold_checkpoint_json": None,
            },
        },
        "episodes": {
            1: {
                "episode_id": 1,
                "node_id": 101,
                "alert_kind": "THRESHOLD",
                "metric_code": "temperature",
                "lifecycle_status": "OPEN",
                "is_current": True,
                "current_revision_no": 2,
                "first_detected_at": NOW - timedelta(hours=30),
                "acknowledgement_version": 1,
            },
            2: {
                "episode_id": 2,
                "node_id": 101,
                "alert_kind": "TIMEOUT",
                "metric_code": None,
                "lifecycle_status": "CLOSED",
                "is_current": True,
                "current_revision_no": 1,
                "first_detected_at": NOW - timedelta(hours=1),
                "acknowledgement_version": 1,
            },
            3: {
                "episode_id": 3,
                "node_id": 101,
                "alert_kind": "THRESHOLD",
                "metric_code": "temperature",
                "lifecycle_status": "CLOSED",
                "is_current": True,
                "current_revision_no": 1,
                "first_detected_at": NOW - timedelta(minutes=10, milliseconds=500),
                "acknowledgement_version": 2,
            },
            4: {
                "episode_id": 4,
                "node_id": 201,
                "alert_kind": "THRESHOLD",
                "metric_code": "temperature",
                "lifecycle_status": "CLOSED",
                "is_current": False,
                "current_revision_no": 2,
                "first_detected_at": NOW - timedelta(hours=2),
                "acknowledgement_version": 1,
            },
        },
        "revisions": {
            (1, 1): {
                "episode_id": 1,
                "revision_no": 1,
                "config_id": 10,
                "period_id": 20,
                "direction": "HIGH",
                "start_event_time": 1_763_600_000,
                "end_event_time": None,
                "start_backend_at": NOW - timedelta(hours=30),
                "end_backend_at": None,
                "closed_at": None,
                "end_reason": None,
                "start_measurement_id": 100,
                "end_measurement_id": None,
                "lower_snapshot": Decimal("18.00"),
                "upper_snapshot": Decimal("30.00"),
                "timeout_snapshot": None,
                "evaluated_at": NOW - timedelta(hours=30),
                "evaluation_generation": 1,
                "acknowledgement_version": 1,
                "threshold_metadata_json": encode_threshold_metadata(
                    is_current=True, transition="PROGRESS"
                ),
            },
            (1, 2): {
                "episode_id": 1,
                "revision_no": 2,
                "config_id": 11,
                "period_id": 20,
                "direction": "HIGH",
                "start_event_time": 1_763_600_000,
                "end_event_time": None,
                "start_backend_at": NOW - timedelta(hours=30),
                "end_backend_at": None,
                "closed_at": None,
                "end_reason": None,
                "start_measurement_id": 100,
                "end_measurement_id": None,
                "lower_snapshot": Decimal("18.00"),
                "upper_snapshot": Decimal("32.00"),
                "timeout_snapshot": None,
                "evaluated_at": NOW - timedelta(hours=29),
                "evaluation_generation": 2,
                "acknowledgement_version": 1,
                "threshold_metadata_json": encode_threshold_metadata(
                    is_current=True, transition="PROGRESS"
                ),
            },
            (2, 1): {
                "episode_id": 2,
                "revision_no": 1,
                "config_id": 12,
                "period_id": 21,
                "direction": None,
                "start_event_time": None,
                "end_event_time": None,
                "start_backend_at": NOW - timedelta(hours=1),
                "end_backend_at": NOW - timedelta(minutes=50),
                "closed_at": NOW - timedelta(minutes=50),
                "end_reason": "NEW_DATA_STORED",
                "start_measurement_id": 100,
                "end_measurement_id": 101,
                "lower_snapshot": None,
                "upper_snapshot": None,
                "timeout_snapshot": 5,
                "evaluated_at": NOW - timedelta(minutes=50),
                "evaluation_generation": 2,
                "acknowledgement_version": 1,
                "threshold_metadata_json": None,
            },
            (3, 1): {
                "episode_id": 3,
                "revision_no": 1,
                "config_id": 11,
                "period_id": 20,
                "direction": "LOW",
                "start_event_time": 1_763_639_000,
                "end_event_time": 1_763_639_100,
                "start_backend_at": NOW - timedelta(minutes=10, milliseconds=500),
                "end_backend_at": NOW - timedelta(minutes=9),
                "closed_at": NOW - timedelta(minutes=9),
                "end_reason": "RECOVERED",
                "start_measurement_id": 102,
                "end_measurement_id": 103,
                "lower_snapshot": Decimal("18.00"),
                "upper_snapshot": Decimal("32.00"),
                "timeout_snapshot": None,
                "evaluated_at": NOW - timedelta(minutes=9),
                "evaluation_generation": 3,
                "acknowledgement_version": 2,
                "threshold_metadata_json": encode_threshold_metadata(
                    is_current=True, transition="REPLAY_REWRITE"
                ),
            },
            (4, 2): {
                "episode_id": 4,
                "revision_no": 2,
                "config_id": 11,
                "period_id": 20,
                "direction": "HIGH",
                "start_event_time": 1_763_630_000,
                "end_event_time": None,
                "start_backend_at": NOW - timedelta(hours=2),
                "end_backend_at": NOW - timedelta(hours=1),
                "closed_at": NOW - timedelta(hours=1),
                "end_reason": "REPLAY_REPLACED",
                "start_measurement_id": 100,
                "end_measurement_id": None,
                "lower_snapshot": Decimal("18.00"),
                "upper_snapshot": Decimal("30.00"),
                "timeout_snapshot": None,
                "evaluated_at": NOW - timedelta(hours=1),
                "evaluation_generation": 3,
                "acknowledgement_version": 1,
                "threshold_metadata_json": encode_threshold_metadata(
                    is_current=False, transition="REPLAY_REPLACED"
                ),
            },
        },
        "acks": {
            (3, 1): {
                "episode_id": 3,
                "acknowledgement_version": 1,
                "acknowledged_revision_no": 1,
                "admin_id": 1,
                "username": "Admin",
                "acknowledged_at": NOW - timedelta(minutes=10),
                "note": "Old version ack",
            }
        },
        "replacements": [
            {
                "old_episode_id": 4,
                "old_revision_no": 1,
                "new_episode_id": 3,
                "new_revision_no": 1,
                "created_at": NOW - timedelta(minutes=9),
                "evaluation_generation": 3,
                "reason": "REPLACED",
            }
        ],
    }

    @contextmanager
    def fake_tx(database, settings_arg, cookie, csrf=None, *, write=False, **kwargs):
        if cookie != "valid-cookie":
            from app.modules.auth.service import invalid_session

            raise invalid_session()
        if write and csrf != "valid-csrf":
            from app.core.web import fail

            fail(403, "CSRF_INVALID")
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

    def _join_episode(eid: int):
        ep = state["episodes"].get(eid)
        if ep is None:
            return None
        node = state["nodes"][ep["node_id"]]
        farm = state["farms"][node["farm_id"]]
        rev = state["revisions"].get((eid, ep["current_revision_no"]), {})
        ack = state["acks"].get((eid, ep["acknowledgement_version"]))
        return {
            **deepcopy(ep),
            **deepcopy(rev),
            "device_code": node["device_code"],
            "node_name": node["name"],
            "zone_id": node["zone_id"],
            "zone_code": node["zone_code"],
            "zone_name": node["zone_name"],
            "zone_archived_at": node["zone_archived_at"],
            "farm_id": farm["farm_id"],
            "farm_code": farm["farm_code"],
            "farm_name": farm["name"],
            "farm_archived_at": farm["archived_at"],
            "acknowledged_revision_no": ack["acknowledged_revision_no"] if ack else None,
            "ack_admin_id": ack["admin_id"] if ack else None,
            "acknowledged_at": ack["acknowledged_at"] if ack else None,
            "ack_note": ack["note"] if ack else None,
            "ack_username": ack["username"] if ack else None,
        }

    def _list_alerts_page(
        _s,
        *,
        farm_id,
        node_id,
        view,
        kind,
        lifecycle,
        acknowledged,
        recovery_proven,
        from_dt,
        to_dt,
        boundary_detected_at,
        boundary_episode_id,
        limit,
    ):
        rows = []
        for eid in state["episodes"]:
            row = _join_episode(eid)
            assert row is not None
            if row["farm_id"] != farm_id or not row["is_current"]:
                continue
            if node_id is not None and row["node_id"] != node_id:
                continue
            if view == "active" and row["lifecycle_status"] != "OPEN":
                continue
            if view == "history" and lifecycle is not None and row["lifecycle_status"] != lifecycle:
                continue
            if kind is not None and row["alert_kind"] != kind:
                continue
            is_ack = row["acknowledged_at"] is not None
            if acknowledged is not None and is_ack != acknowledged:
                continue
            if recovery_proven is not None:
                if row["alert_kind"] != "THRESHOLD":
                    continue
                is_rec = (
                    row["lifecycle_status"] == "CLOSED" and row.get("end_reason") == "RECOVERED"
                )
                if is_rec != recovery_proven:
                    continue
            if from_dt is not None and row["first_detected_at"] < from_dt:
                continue
            if to_dt is not None and row["first_detected_at"] > to_dt:
                continue
            if boundary_detected_at is not None and boundary_episode_id is not None:
                if not (
                    row["first_detected_at"] < boundary_detected_at
                    or (
                        row["first_detected_at"] == boundary_detected_at
                        and row["episode_id"] < boundary_episode_id
                    )
                ):
                    continue
            rows.append(row)
        rows.sort(key=lambda r: (r["first_detected_at"], r["episode_id"]), reverse=True)
        return rows[:limit]

    monkeypatch.setattr(alerts_service_mod, "web_session_transaction", fake_tx)
    monkeypatch.setattr(
        alerts_service_mod.meas_repo, "get_farm_summary", lambda s, fid: s["farms"].get(int(fid))
    )
    monkeypatch.setattr(
        alerts_service_mod.meas_repo, "get_dashboard_node", lambda s, nid: s["nodes"].get(int(nid))
    )
    monkeypatch.setattr(
        alerts_service_mod.meas_repo,
        "list_farm_dashboard_nodes",
        lambda s, fid: [dict(n) for _, n in sorted(s["nodes"].items()) if n["farm_id"] == int(fid)],
    )
    monkeypatch.setattr(alerts_service_mod.repo, "list_alerts_page", _list_alerts_page)
    monkeypatch.setattr(
        alerts_service_mod.repo,
        "list_evaluation_states_for_nodes",
        lambda s, nids: {
            nid: dict(s["eval_states"][nid]) for nid in nids if nid in s["eval_states"]
        },
    )
    monkeypatch.setattr(
        alerts_service_mod.repo,
        "get_alert_episode_context",
        lambda _s, eid, lock_episode=False: _join_episode(eid),
    )
    monkeypatch.setattr(
        alerts_service_mod.repo,
        "get_evaluation_state",
        lambda s, nid: deepcopy(s["eval_states"].get(nid)),
    )
    monkeypatch.setattr(
        alerts_service_mod.repo,
        "list_audit_revisions",
        lambda s, *, episode_id, boundary_rev_no, limit: sorted(
            [
                deepcopy(r)
                for (eid, rno), r in s["revisions"].items()
                if eid == episode_id and (boundary_rev_no is None or rno < boundary_rev_no)
            ],
            key=lambda r: r["revision_no"],
            reverse=True,
        )[:limit],
    )
    monkeypatch.setattr(
        alerts_service_mod.repo,
        "list_audit_acknowledgements",
        lambda s, *, episode_id, exact_version, boundary_ack_ver, limit: sorted(
            [
                deepcopy(a)
                for (eid, ver), a in s["acks"].items()
                if eid == episode_id
                and (exact_version is None or ver == exact_version)
                and (boundary_ack_ver is None or ver < boundary_ack_ver)
            ],
            key=lambda a: a["acknowledgement_version"],
            reverse=True,
        )[:limit],
    )
    monkeypatch.setattr(
        alerts_service_mod.repo,
        "list_audit_replacements",
        lambda s, *, episode_id, boundary, limit: [
            deepcopy(r)
            for r in s["replacements"]
            if r["old_episode_id"] == episode_id or r["new_episode_id"] == episode_id
        ][:limit],
    )
    monkeypatch.setattr(
        alerts_service_mod.repo,
        "resolve_episode_node_id",
        lambda s, eid: s["episodes"][eid]["node_id"] if eid in s["episodes"] else None,
    )
    monkeypatch.setattr(
        alerts_service_mod.devices_repo,
        "resolve_node_parents",
        lambda s, nid: (
            {
                "node_id": int(nid),
                "zone_id": s["nodes"][int(nid)]["zone_id"],
                "farm_id": s["nodes"][int(nid)]["farm_id"],
                "gateway_id": s["nodes"][int(nid)]["gateway_id"],
            }
            if int(nid) in s["nodes"]
            else None
        ),
    )
    monkeypatch.setattr(
        alerts_service_mod.devices_repo,
        "resolve_gateway_farm_id",
        lambda _s, _gid: 1,
    )
    monkeypatch.setattr(alerts_service_mod.mc_repo, "lock_farms_ordered", lambda _s, _f: {})
    monkeypatch.setattr(alerts_service_mod.mc_repo, "lock_zones_ordered", lambda _s, _z: {})
    monkeypatch.setattr(alerts_service_mod.devices_repo, "lock_gateway", lambda _s, _g: {})
    monkeypatch.setattr(alerts_service_mod.devices_repo, "lock_node", lambda _s, _n: {})
    monkeypatch.setattr(
        alerts_service_mod.repo,
        "lock_alert_episode_by_id",
        lambda s, eid: deepcopy(s["episodes"].get(eid)),
    )
    monkeypatch.setattr(
        alerts_service_mod.repo,
        "get_alert_revision",
        lambda s, eid, rno: deepcopy(s["revisions"].get((eid, rno))),
    )
    monkeypatch.setattr(
        alerts_service_mod.repo,
        "get_alert_acknowledgement",
        lambda s, eid, ver: deepcopy(s["acks"].get((eid, ver))),
    )
    monkeypatch.setattr(alerts_service_mod.repo, "get_db_utc_now", lambda s: s["now"])

    def _insert_ack(
        s,
        *,
        episode_id,
        acknowledgement_version,
        acknowledged_revision_no,
        admin_id,
        acknowledged_at,
        note,
    ):
        s["acks"][(episode_id, acknowledgement_version)] = {
            "episode_id": episode_id,
            "acknowledgement_version": acknowledgement_version,
            "acknowledged_revision_no": acknowledged_revision_no,
            "admin_id": admin_id,
            "username": "Admin",
            "acknowledged_at": acknowledged_at,
            "note": note,
        }

    monkeypatch.setattr(alerts_service_mod.repo, "insert_alert_acknowledgement", _insert_ack)
    return app, state


def test_alerts_list_filters_ms_bounds_and_cursor(monkeypatch):
    app, _ = _fake_alerts_client(monkeypatch)
    with TestClient(app) as client:
        assert client.get("/api/v1/alerts?farm_id=1").status_code == 401
        client.cookies.set("hq_session", "valid-cookie")

        # Required farm_id, invalid combos, unapproved metric_code rejected.
        assert client.get("/api/v1/alerts").status_code == 400
        assert client.get("/api/v1/alerts?farm_id=1&metric_code=temperature").status_code == 400
        assert (
            client.get("/api/v1/alerts?farm_id=1&view=active&lifecycle=CLOSED").status_code == 400
        )
        assert (
            client.get("/api/v1/alerts?farm_id=1&kind=TIMEOUT&recovery_proven=true").status_code
            == 400
        )
        assert client.get("/api/v1/alerts?farm_id=999").status_code == 404
        assert client.get("/api/v1/alerts?farm_id=1&node_id=201").status_code == 400

        # Active view: no default 24h window -> returns 30h-old OPEN episode #1!
        r_active = client.get("/api/v1/alerts?farm_id=1&view=active")
        assert r_active.status_code == 200
        assert r_active.headers["cache-control"] == "no-store"
        body = r_active.json()
        assert body["range"] == {"from": None, "to": None}
        assert [it["id"] for it in body["items"]] == ["1"]
        assert "101" in body["evaluation_by_node"]
        assert "secret_internal" not in r_active.text

        # History view: default 24h excludes 30h-old #1; cursor keeps fixed range.
        r_h1 = client.get("/api/v1/alerts?farm_id=1&view=history&page_size=1")
        assert r_h1.status_code == 200
        h1 = r_h1.json()
        assert [it["id"] for it in h1["items"]] == ["3"]
        assert h1["items"][0]["recovery_proven"] is True
        assert h1["next_cursor"] is not None

        r_h2 = client.get(
            f"/api/v1/alerts?farm_id=1&view=history&page_size=1&cursor={h1['next_cursor']}"
        )
        assert r_h2.status_code == 200
        h2 = r_h2.json()
        assert [it["id"] for it in h2["items"]] == ["2"]
        assert h2["items"][0]["recovery_proven"] is None
        assert h2["range"] == h1["range"]
        assert h2["next_cursor"] is None

        # Sub-ms ceil/floor bounds around episode #3 (11:49:59.500Z):
        # from = .5001Z -> ceil to .501Z -> excludes #3!
        r_ceil = client.get(
            "/api/v1/alerts?farm_id=1&view=history&from=2026-11-21T11:49:59.5001Z&to=2026-11-21T12:00:00.000Z"
        )
        assert [it["id"] for it in r_ceil.json()["items"]] == []
        # to = .5009Z -> floor to .500Z -> includes #3!
        r_floor = client.get(
            "/api/v1/alerts?farm_id=1&view=history&from=2026-11-21T11:49:59.500Z&to=2026-11-21T11:49:59.5009Z"
        )
        assert [it["id"] for it in r_floor.json()["items"]] == ["3"]


def test_alert_detail_audit_pagination_and_versioned_acknowledgement(monkeypatch):
    app, state = _fake_alerts_client(monkeypatch)
    with TestClient(app) as client:
        client.cookies.set("hq_session", "valid-cookie")

        # Detail works on retired episode #4 (cross-farm context included, no checkpoint leaked).
        r_ret = client.get("/api/v1/alerts/4?audit_kind=replacements")
        assert r_ret.status_code == 200
        ret_body = r_ret.json()
        assert ret_body["episode"]["is_current"] is False
        assert ret_body["episode"]["farm"]["id"] == "2"
        assert ret_body["audit"]["kind"] == "replacements"
        assert len(ret_body["audit"]["items"]) == 1

        # Audit revisions pagination on episode #1 (2 revisions).
        r_rev1 = client.get("/api/v1/alerts/1?audit_kind=revisions&audit_page_size=1")
        assert r_rev1.status_code == 200
        assert [x["revision_no"] for x in r_rev1.json()["audit"]["items"]] == [2]
        cur = r_rev1.json()["audit"]["next_cursor"]
        r_rev2 = client.get(
            f"/api/v1/alerts/1?audit_kind=revisions&audit_page_size=1&audit_cursor={cur}"
        )
        assert [x["revision_no"] for x in r_rev2.json()["audit"]["items"]] == [1]

        # Exact acknowledgement lookup on episode #3 for old version 1 (lost response recovery).
        r_exact = client.get(
            "/api/v1/alerts/3?audit_kind=acknowledgements&acknowledgement_version=1"
        )
        assert r_exact.status_code == 200
        assert len(r_exact.json()["audit"]["items"]) == 1
        assert r_exact.json()["audit"]["items"][0]["note"] == "Old version ack"

        # POST ack: older technical revision (rev=1 while current=2) on same version=1 -> 201!
        r_ack = client.post(
            "/api/v1/alerts/1/acknowledgements",
            headers=WEB_HEADERS,
            content=json.dumps(
                {"acknowledgement_version": 1, "viewed_revision_no": 1, "note": "Checked"}
            ),
        )
        assert r_ack.status_code == 201
        assert r_ack.json()["current_state"]["acknowledged"] is True

        # Idempotent retry for same version -> 200 with real current_state!
        r_retry = client.post(
            "/api/v1/alerts/1/acknowledgements",
            headers=WEB_HEADERS,
            content=json.dumps({"acknowledgement_version": 1, "viewed_revision_no": 2}),
        )
        assert r_retry.status_code == 200
        assert r_retry.json()["acknowledgement"]["note"] == "Checked"

        # Viewed revision not belonging to episode -> 400; mismatched version -> 409!
        assert (
            client.post(
                "/api/v1/alerts/1/acknowledgements",
                headers=WEB_HEADERS,
                content=json.dumps({"acknowledgement_version": 1, "viewed_revision_no": 99}),
            ).status_code
            == 400
        )
        r_mismatch = client.post(
            "/api/v1/alerts/1/acknowledgements",
            headers=WEB_HEADERS,
            content=json.dumps({"acknowledgement_version": 2, "viewed_revision_no": 2}),
        )
        assert r_mismatch.status_code == 409
        assert r_mismatch.json()["error"]["code"] == "ALERT_VERSION_CONFLICT"
        assert "current_state" in r_mismatch.json()["error"]["details"]

        # Retired episode #4 unacknowledged -> 409 ALERT_VERSION_CONFLICT.
        r_ret_ack = client.post(
            "/api/v1/alerts/4/acknowledgements",
            headers=WEB_HEADERS,
            content=json.dumps({"acknowledgement_version": 1, "viewed_revision_no": 2}),
        )
        assert r_ret_ack.status_code == 409
        assert r_ret_ack.json()["error"]["details"]["current_state"]["is_current"] is False

        # Corrupted projection (OPEN episode pointing to NULL current_revision_no) -> 503.
        state["episodes"][1]["current_revision_no"] = None
        r_corrupt = client.get("/api/v1/alerts/1")
        assert r_corrupt.status_code == 503
        assert r_corrupt.json()["error"]["code"] == "STORAGE_UNAVAILABLE"
