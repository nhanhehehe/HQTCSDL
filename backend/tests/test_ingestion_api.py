import json
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError, OperationalError

from app.core.config import Settings
from app.main import create_app
from app.modules.devices.credentials import generate_gateway_key
from app.modules.ingestion.service import IngestionService

NOW_EPOCH = 1_794_384_000


def _sample_record(
    *,
    device_id: str = "NODE-01",
    boot_id: int = 15,
    seq: int = 1,
    t_node: int | None = NOW_EPOCH - 5,
    time_status: str = "VALID",
    recv_time: int = NOW_EPOCH - 2,
    rssi: float | None = -85,
    temp: float | None = 28.5,
    soil_status: str = "OK",
    soil_value: float | None = 45.5,
) -> dict:
    return {
        "device_id": device_id,
        "boot_id": boot_id,
        "seq": seq,
        "t_node": t_node,
        "time_status": time_status,
        "recv_time": recv_time,
        "rssi": rssi,
        "measurements": {
            "temperature": {"value": temp, "status": "OK"},
            "airHumidity": {"value": 75.24, "status": "OK"},
            "pressure": {"value": 1008.42, "status": "OK"},
            "light": {"value": 0, "status": "OK"},
            "soilMoisture": {"value": soil_value, "status": soil_status},
        },
    }


def _fake_ingestion_app(monkeypatch, *, max_body_bytes: int = 65536):
    key_active, digest_active = generate_gateway_key()
    key_revoked, digest_revoked = generate_gateway_key()
    key_gw2, digest_gw2 = generate_gateway_key()

    state = {
        "farms": {
            1: {"farm_id": 1, "archived_at": None},
            2: {"farm_id": 2, "archived_at": None},
        },
        "zones": {
            10: {"zone_id": 10, "farm_id": 1, "archived_at": None},
            20: {"zone_id": 20, "farm_id": 2, "archived_at": None},
        },
        "gateways": {
            100: {
                "gateway_id": 100,
                "gateway_code": "GW-01",
                "farm_id": 1,
                "receiving_enabled": True,
            },
            200: {
                "gateway_id": 200,
                "gateway_code": "GW-02",
                "farm_id": 2,
                "receiving_enabled": True,
            },
        },
        "credentials": {
            1: {
                "credential_id": 1,
                "gateway_id": 100,
                "key_hash": digest_active,
                "revoked_at": None,
                "gateway_code": "GW-01",
                "farm_id": 1,
            },
            2: {
                "credential_id": 2,
                "gateway_id": 100,
                "key_hash": digest_revoked,
                "revoked_at": datetime(2026, 11, 18, 12, 0, 0),
                "gateway_code": "GW-01",
                "farm_id": 1,
            },
            3: {
                "credential_id": 3,
                "gateway_id": 200,
                "key_hash": digest_gw2,
                "revoked_at": None,
                "gateway_code": "GW-02",
                "farm_id": 2,
            },
        },
        "nodes": {
            1001: {
                "node_id": 1001,
                "device_code": "NODE-01",
                "zone_id": 10,
                "zone_farm_id": 1,
                "gateway_id": 100,
                "receiving_enabled": True,
            },
            1002: {
                "node_id": 1002,
                "device_code": "NODE-02",
                "zone_id": 10,
                "zone_farm_id": 1,
                "gateway_id": None,  # unlinked
                "receiving_enabled": True,
            },
            1003: {
                "node_id": 1003,
                "device_code": "NODE-X",
                "zone_id": 20,  # farm 2, linked to GW-01 in farm 1 (cross-farm)
                "zone_farm_id": 2,
                "gateway_id": 100,
                "receiving_enabled": True,
            },
        },
        "measurements": {},  # (node_id, boot_id, seq) -> header dict
        "values": {},  # measurement_id -> list[dict]
        "next_mid": 1,
        "runtimes": {1001: {"last_measurement_id": None, "last_new_persisted_at": None}},
        "eval_states": {
            1001: {
                "desired_generation": 0,
                "completed_generation": 0,
                "full_replay_required": False,
                "replay_from_t_node": None,
            }
        },
        "locks": [],
        "hooks": {},
    }

    settings = Settings(_env_file=None, ingestion_max_body_bytes=max_body_bytes)
    app = create_app(settings)
    app.state.ingestion = IngestionService(
        app.state.database,
        settings,
        clock=lambda: datetime.fromtimestamp(NOW_EPOCH, tz=UTC),
        sleep_fn=lambda _: None,
    )

    @contextmanager
    def fake_tx(self, *, write: bool = True):
        # Snapshot mutable dicts so rollbacks truly discard uncommitted writes.
        snap_m = dict(state["measurements"])
        snap_v = {k: list(v) for k, v in state["values"].items()}
        snap_rt = {k: dict(v) for k, v in state["runtimes"].items()}
        snap_ev = {k: dict(v) for k, v in state["eval_states"].items()}
        try:
            yield state
        except Exception:
            state["measurements"] = snap_m
            state["values"] = snap_v
            state["runtimes"] = snap_rt
            state["eval_states"] = snap_ev
            raise

    monkeypatch.setattr(IngestionService, "_record_transaction", fake_tx)
    monkeypatch.setattr(
        "app.modules.ingestion.auth.lookup_credential_by_hash",
        lambda s, h: next((c for c in s["credentials"].values() if c["key_hash"] == h), None),
    )
    monkeypatch.setattr(
        "app.modules.areas.repository.get_farm",
        lambda s, fid, lock=False: (
            s["locks"].append(("farm", int(fid), lock)) or s["farms"].get(int(fid))
        ),
    )
    monkeypatch.setattr(
        "app.modules.areas.repository.get_zone",
        lambda s, zid, lock=False: (
            s["locks"].append(("zone", int(zid), lock)) or s["zones"].get(int(zid))
        ),
    )
    monkeypatch.setattr(
        "app.modules.devices.repository.lock_gateway",
        lambda s, gid: (
            s["locks"].append(("gateway", int(gid), True)) or s["gateways"].get(int(gid))
        ),
    )
    monkeypatch.setattr(
        "app.modules.devices.repository.lock_node",
        lambda s, nid: s["locks"].append(("node", int(nid), True)) or s["nodes"].get(int(nid)),
    )
    monkeypatch.setattr(
        "app.modules.devices.repository.get_credential",
        lambda s, *, gateway_id, credential_id, lock=False: (
            s["credentials"].get(int(credential_id))
            if s["credentials"].get(int(credential_id), {}).get("gateway_id") == int(gateway_id)
            else None
        ),
    )
    monkeypatch.setattr(
        "app.modules.ingestion.repository.resolve_node_for_ingestion",
        lambda s, code: next((n for n in s["nodes"].values() if n["device_code"] == code), None),
    )
    monkeypatch.setattr(
        "app.modules.ingestion.repository.seek_measurement_locked",
        lambda s, *, node_id, boot_id, seq: s["measurements"].get((node_id, boot_id, seq)),
    )
    monkeypatch.setattr(
        "app.modules.ingestion.repository.get_measurement_values",
        lambda s, mid: s["values"].get(mid, []),
    )
    monkeypatch.setattr(
        "app.modules.ingestion.repository.get_max_event_time",
        lambda s, nid: max(
            (
                m["t_node"]
                for m in s["measurements"].values()
                if m["node_id"] == nid and m["t_node"] is not None
            ),
            default=None,
        ),
    )

    def fake_insert_m(
        s,
        *,
        node_id,
        gateway_id,
        record,
        backend_received_at,
        canonical_version,
        original_hash,
    ):
        hook = s["hooks"].get("insert_measurement")
        if hook:
            hook(s, record)
        mid = s["next_mid"]
        s["next_mid"] += 1
        persisted_at = datetime(2026, 11, 19, 4, 0, mid)
        header = {
            "measurement_id": mid,
            "node_id": node_id,
            "gateway_id": gateway_id,
            "boot_id": record.boot_id,
            "seq": record.seq,
            "t_node": record.t_node,
            "time_status": record.time_status,
            "recv_time": record.recv_time,
            "rssi": record.rssi,
            "backend_received_at": backend_received_at,
            "persisted_at": persisted_at,
            "canonical_version": canonical_version,
            "original_hash": original_hash,
        }
        s["measurements"][(node_id, record.boot_id, record.seq)] = header
        return {"measurement_id": mid, "persisted_at": persisted_at}

    def fake_insert_v(s, mid, record):
        s["values"][mid] = [
            {
                "metric_code": code,
                "value": m.value,
                "read_status": m.status,
            }
            for code, m in record.measurements.items()
        ]
        return len(s["values"][mid])

    def fake_upd_rt(s, *, node_id, measurement_id, persisted_at):
        if node_id not in s["runtimes"]:
            return 0
        s["runtimes"][node_id]["last_measurement_id"] = measurement_id
        s["runtimes"][node_id]["last_new_persisted_at"] = persisted_at
        return 1

    def fake_upd_ev(s, *, node_id, time_status, t_node, prev_max_t_node):
        if node_id not in s["eval_states"]:
            return 0
        ev = s["eval_states"][node_id]
        ev["desired_generation"] += 1
        if time_status == "VALID" and t_node is not None:
            ev["full_replay_required"] = True
            if prev_max_t_node is not None and t_node <= prev_max_t_node:
                cur = ev["replay_from_t_node"]
                ev["replay_from_t_node"] = t_node if cur is None or t_node < cur else cur
        return 1

    monkeypatch.setattr("app.modules.ingestion.repository.insert_measurement", fake_insert_m)
    monkeypatch.setattr("app.modules.ingestion.repository.insert_measurement_values", fake_insert_v)
    monkeypatch.setattr("app.modules.ingestion.repository.update_node_runtime", fake_upd_rt)
    monkeypatch.setattr("app.modules.ingestion.repository.update_evaluation_state", fake_upd_ev)

    return app, state, {"active": key_active, "revoked": key_revoked, "gw2": key_gw2}


def test_d04_request_level_errors_and_body_limit(monkeypatch):
    app, _, keys = _fake_ingestion_app(monkeypatch, max_body_bytes=65536)
    with TestClient(app) as client:
        # Wrong method -> D04 JSON envelope (never HTML or problem+json)
        r_method = client.get("/api/v1/ingestion/measurements")
        assert r_method.status_code in {404, 405}
        assert r_method.headers["content-type"].startswith("application/json")
        assert r_method.json()["error"]["code"] == "INVALID_REQUEST"
        assert r_method.json()["retryable"] is False

        # Malformed JSON -> 400 INVALID_REQUEST
        r_bad = client.post(
            "/api/v1/ingestion/measurements",
            content=b"{not-json",
            headers={"Content-Type": "application/json", "X-API-Key": keys["active"]},
        )
        assert r_bad.status_code == 400
        assert r_bad.json()["error"]["code"] == "INVALID_REQUEST"
        assert r_bad.json()["retryable"] is False

        # Extra envelope key (G2-D02a) -> 400 INVALID_REQUEST
        r_extra = client.post(
            "/api/v1/ingestion/measurements",
            json={"gateway_id": "GW-01", "records": [_sample_record()], "extra": 1},
            headers={"X-API-Key": keys["active"]},
        )
        assert r_extra.status_code == 400
        assert r_extra.json()["error"]["code"] == "INVALID_REQUEST"

        # Empty records & 51 records -> 400 INVALID_REQUEST (NOT 413!)
        for count in (0, 51):
            r_cnt = client.post(
                "/api/v1/ingestion/measurements",
                json={
                    "gateway_id": "GW-01",
                    "records": [_sample_record(seq=i + 1) for i in range(count)],
                },
                headers={"X-API-Key": keys["active"]},
            )
            assert r_cnt.status_code == 400
            assert r_cnt.json()["error"]["code"] == "INVALID_REQUEST"

        # G2-D01: > 65536 bytes -> 413 PAYLOAD_TOO_LARGE
        oversized = b'{"gateway_id":"GW-01","records":[' + (b" " * 65537) + b"]}"
        r_413 = client.post(
            "/api/v1/ingestion/measurements",
            content=oversized,
            headers={"Content-Type": "application/json", "X-API-Key": keys["active"]},
        )
        assert r_413.status_code == 413
        assert r_413.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"
        assert r_413.json()["retryable"] is False

        # G2-D02b: missing/invalid key -> 401 AUTHENTICATION_FAILED
        r_401 = client.post(
            "/api/v1/ingestion/measurements",
            json={"gateway_id": "GW-01", "records": [_sample_record()]},
        )
        assert r_401.status_code == 401
        assert r_401.json()["error"]["code"] == "AUTHENTICATION_FAILED"
        assert r_401.json()["retryable"] is False

        # Revoked key or gateway_id mismatch -> 403 GATEWAY_FORBIDDEN
        r_rev = client.post(
            "/api/v1/ingestion/measurements",
            json={"gateway_id": "GW-01", "records": [_sample_record()]},
            headers={"X-API-Key": keys["revoked"]},
        )
        assert r_rev.status_code == 403
        assert r_rev.json()["error"]["code"] == "GATEWAY_FORBIDDEN"

        r_mis = client.post(
            "/api/v1/ingestion/measurements",
            json={"gateway_id": "GW-01", "records": [_sample_record()]},
            headers={"X-API-Key": keys["gw2"]},
        )
        assert r_mis.status_code == 403
        assert r_mis.json()["error"]["code"] == "GATEWAY_FORBIDDEN"
        assert keys["gw2"] not in r_mis.text


def test_mixed_batch_partial_ack_duplicate_conflict_and_g2_d03_order(monkeypatch):
    app, state, keys = _fake_ingestion_app(monkeypatch)
    with TestClient(app) as client:
        # 1) Mixed batch: valid, null, seq=bool, duplicate, conflict, unlinked node
        batch = [
            _sample_record(seq=1),
            None,
            {**_sample_record(seq=2), "seq": True},
            _sample_record(seq=1, recv_time=NOW_EPOCH - 1, rssi=-70),  # duplicate (metadata diff)
            _sample_record(seq=1, temp=29.0),  # conflict
            _sample_record(device_id="NODE-02", seq=1),  # unlinked -> INVALID_SOURCE
        ]
        res = client.post(
            "/api/v1/ingestion/measurements",
            json={"gateway_id": "GW-01", "records": batch},
            headers={"X-API-Key": keys["active"]},
        )
        assert res.status_code == 200
        assert res.headers["cache-control"] == "no-store"
        items = res.json()["results"]
        assert [x["index"] for x in items] == [0, 1, 2, 3, 4, 5]
        assert items[0]["status"] == "STORED" and items[0]["ack"] is True
        assert items[1]["status"] == "REJECTED" and "device_id" not in items[1]
        assert (
            items[2]["status"] == "REJECTED" and "seq" not in items[2] and items[2]["boot_id"] == 15
        )
        assert items[3]["status"] == "ALREADY_EXISTS" and items[3]["ack"] is True
        assert items[4]["status"] == "REJECTED" and items[4]["error"]["code"] == "RECORD_CONFLICT"
        assert items[5]["status"] == "REJECTED" and items[5]["error"]["code"] == "INVALID_SOURCE"
        assert state["eval_states"][1001]["desired_generation"] == 1
        assert state["eval_states"][1001]["full_replay_required"] is True

        # 2) G2-D03 order on NEW record: D06 -> Configuration -> Receiving permissions
        # Archive Zone AND disable Node receiving AND violate D06 (+121s): D06 must win!
        state["zones"][10]["archived_at"] = datetime(2026, 11, 19, 1, 0, 0)
        state["nodes"][1001]["receiving_enabled"] = False
        r_d03 = client.post(
            "/api/v1/ingestion/measurements",
            json={
                "gateway_id": "GW-01",
                "records": [
                    _sample_record(
                        seq=1
                    ),  # duplicate still ALREADY_EXISTS despite archived + disabled!
                    _sample_record(
                        seq=2, t_node=NOW_EPOCH + 121
                    ),  # D06 wins -> REJECTED/INVALID_RECORD
                    _sample_record(seq=3),  # valid D06, archived zone -> CONFIGURATION_INVALID
                ],
            },
            headers={"X-API-Key": keys["active"]},
        )
        assert r_d03.status_code == 200
        r_items = r_d03.json()["results"]
        assert r_items[0]["status"] == "ALREADY_EXISTS" and r_items[0]["ack"] is True
        assert (
            r_items[1]["status"] == "REJECTED" and r_items[1]["error"]["code"] == "INVALID_RECORD"
        )
        assert (
            r_items[2]["status"] == "RETRY_LATER"
            and r_items[2]["error"]["code"] == "CONFIGURATION_INVALID"
        )

        # Unarchive Zone while receiving still disabled -> RECEIVING_DISABLED
        state["zones"][10]["archived_at"] = None
        r_perm = client.post(
            "/api/v1/ingestion/measurements",
            json={"gateway_id": "GW-01", "records": [_sample_record(seq=3)]},
            headers={"X-API-Key": keys["active"]},
        )
        assert r_perm.json()["results"][0]["status"] == "RETRY_LATER"
        assert r_perm.json()["results"][0]["error"]["code"] == "RECEIVING_DISABLED"

        # Cross-farm Node (Farm 2 Zone, Farm 1 GW) -> CONFIGURATION_INVALID + sorted Farm locks
        state["locks"].clear()
        r_cross = client.post(
            "/api/v1/ingestion/measurements",
            json={"gateway_id": "GW-01", "records": [_sample_record(device_id="NODE-X", seq=1)]},
            headers={"X-API-Key": keys["active"]},
        )
        assert r_cross.json()["results"][0]["error"]["code"] == "CONFIGURATION_INVALID"
        farm_locks = [x[1] for x in state["locks"] if x[0] == "farm"]
        assert farm_locks == [1, 2]


def test_e06_01_missing_runtime_rolls_back_and_e06_03_mid_batch_revoke(monkeypatch):
    app, state, keys = _fake_ingestion_app(monkeypatch)
    with TestClient(app) as client:
        # E06-01: missing NodeRuntime row rolls back Measurement/Values -> STORAGE_UNAVAILABLE
        del state["runtimes"][1001]
        r_missing = client.post(
            "/api/v1/ingestion/measurements",
            json={"gateway_id": "GW-01", "records": [_sample_record(seq=10)]},
            headers={"X-API-Key": keys["active"]},
        )
        assert r_missing.status_code == 200
        item = r_missing.json()["results"][0]
        assert item["status"] == "RETRY_LATER" and item["error"]["code"] == "STORAGE_UNAVAILABLE"
        assert (1001, 15, 10) not in state["measurements"]

        # Restore runtime, test E06-03: revoke key after record 0 commits -> request fails 403!
        state["runtimes"][1001] = {"last_measurement_id": None, "last_new_persisted_at": None}

        def revoke_after_first(s, record):
            if record.seq == 20:
                s["credentials"][1]["revoked_at"] = datetime(2026, 11, 19, 4, 5, 0)

        state["hooks"]["insert_measurement"] = revoke_after_first
        r_mid = client.post(
            "/api/v1/ingestion/measurements",
            json={
                "gateway_id": "GW-01",
                "records": [_sample_record(seq=20), _sample_record(seq=21)],
            },
            headers={"X-API-Key": keys["active"]},
        )
        assert r_mid.status_code == 403
        assert r_mid.json()["error"]["code"] == "GATEWAY_FORBIDDEN"
        assert "results" not in r_mid.json()
        # Record 20 committed before revoke; record 21 was NOT stored.
        assert (1001, 15, 20) in state["measurements"]
        assert (1001, 15, 21) not in state["measurements"]


def test_deadlock_1205_retry_and_uq_2601_reread(monkeypatch):
    app, state, keys = _fake_ingestion_app(monkeypatch)
    attempts = {"count": 0}

    def fail_twice_1205(s, record):
        attempts["count"] += 1
        if attempts["count"] <= 2:
            raise OperationalError("stmt", {}, Exception("40001 (1205) Deadlock victim"))

    state["hooks"]["insert_measurement"] = fail_twice_1205
    with TestClient(app) as client:
        r_dl = client.post(
            "/api/v1/ingestion/measurements",
            json={"gateway_id": "GW-01", "records": [_sample_record(seq=30)]},
            headers={"X-API-Key": keys["active"]},
        )
        assert r_dl.status_code == 200
        assert r_dl.json()["results"][0]["status"] == "STORED"
        assert attempts["count"] == 3

        # Simulate UQ 2601 race where another tx already inserted the same record
        def simulate_uq(s, record):
            # Pre-populate committed row, then raise 2601
            mid = 999
            s["measurements"][(1001, record.boot_id, record.seq)] = {
                "measurement_id": mid,
                "node_id": 1001,
                "gateway_id": 100,
                "boot_id": record.boot_id,
                "seq": record.seq,
                "t_node": record.t_node,
                "time_status": record.time_status,
                "recv_time": record.recv_time,
                "rssi": record.rssi,
                "canonical_version": 1,
            }
            s["values"][mid] = [
                {"metric_code": k, "value": m.value, "read_status": m.status}
                for k, m in record.measurements.items()
            ]
            raise IntegrityError(
                "stmt",
                {},
                Exception("2627 Violation of UNIQUE KEY constraint UQ_Measurement_identity"),
            )

        # Keep committed row outside snapshot rollback for UQ simulation
        real_tx = app.state.ingestion._record_transaction

        @contextmanager
        def tx_keeping_external(write=True):
            try:
                with real_tx(write=write) as s:
                    yield s
            except IntegrityError:
                # Re-insert the "other transaction's" committed row after local rollback
                rec = _sample_record(seq=31)
                state["measurements"][(1001, 15, 31)] = {
                    "measurement_id": 999,
                    "node_id": 1001,
                    "gateway_id": 100,
                    "boot_id": 15,
                    "seq": 31,
                    "t_node": rec["t_node"],
                    "time_status": "VALID",
                    "recv_time": rec["recv_time"],
                    "rssi": Decimal("-85"),
                    "canonical_version": 1,
                }
                state["values"][999] = [
                    {"metric_code": "temperature", "value": Decimal("28.50"), "read_status": "OK"},
                    {"metric_code": "airHumidity", "value": Decimal("75.24"), "read_status": "OK"},
                    {"metric_code": "pressure", "value": Decimal("1008.42"), "read_status": "OK"},
                    {"metric_code": "light", "value": Decimal("0.0"), "read_status": "OK"},
                    {"metric_code": "soilMoisture", "value": Decimal("45.5"), "read_status": "OK"},
                ]
                raise

        monkeypatch.setattr(app.state.ingestion, "_record_transaction", tx_keeping_external)
        state["hooks"]["insert_measurement"] = simulate_uq
        r_uq = client.post(
            "/api/v1/ingestion/measurements",
            json={"gateway_id": "GW-01", "records": [_sample_record(seq=31)]},
            headers={"X-API-Key": keys["active"]},
        )
        assert r_uq.status_code == 200
        assert r_uq.json()["results"][0]["status"] == "ALREADY_EXISTS"
        assert r_uq.json()["results"][0]["ack"] is True


def test_unknown_time_status_updates_runtime_without_full_replay(monkeypatch):
    app, state, keys = _fake_ingestion_app(monkeypatch)
    with TestClient(app) as client:
        r = client.post(
            "/api/v1/ingestion/measurements",
            content=json.dumps(
                {
                    "gateway_id": "GW-01",
                    "records": [_sample_record(seq=40, t_node=None, time_status="UNKNOWN")],
                }
            ),
            headers={"Content-Type": "application/json", "X-API-Key": keys["active"]},
        )
        assert r.status_code == 200
        assert r.json()["results"][0]["status"] == "STORED"
        assert state["runtimes"][1001]["last_new_persisted_at"] is not None
        assert state["eval_states"][1001]["desired_generation"] == 1
        assert state["eval_states"][1001]["full_replay_required"] is False
        assert state["eval_states"][1001]["replay_from_t_node"] is None
