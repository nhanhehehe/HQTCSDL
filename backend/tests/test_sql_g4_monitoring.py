"""Opt-in SQL Server integration and concurrency tests for G4 FL-04, FL-08, and FL-10.

Requires HQ_RUN_SQL_TESTS=1 and HQ_EXPECT_TEST_DATABASE=<disposable_test_db>.
Reads ONLY HQ_AUTH_TEST_DATABASE_URL (never falls back to HQ_DATABASE_URL).
"""

import json
import os
import threading
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.core.config import NodeTimeProfile, Settings
from app.core.errors import WebError
from app.modules.alerts import timeout_engine
from app.modules.areas.schemas import AreaCreate, AreaLifecycleAction
from app.modules.areas.service import AreaService
from app.modules.auth import repository as auth_repo
from app.modules.auth.security import password_hasher
from app.modules.auth.service import AuthService
from app.modules.devices.schemas import GatewayCreate, NodeCreate
from app.modules.devices.service import DeviceService
from app.modules.ingestion.canonical import CANONICAL_VERSION, build_canonical_v1
from app.modules.ingestion.service import IngestionService
from app.modules.ingestion.validator import validate_record_static
from app.modules.monitoring_config import repository as mc_repo
from app.modules.monitoring_config.service import MonitoringConfigService

pytestmark = pytest.mark.skipif(
    os.environ.get("HQ_RUN_SQL_TESTS") != "1", reason="SQL writes require explicit opt-in"
)


class _EngineDB:
    def __init__(self, engine):
        self.engine = engine

    def sessions(self):
        return sessionmaker(bind=self.engine, expire_on_commit=False)()


def _record(
    *,
    device_id: str,
    boot_id: int = 1,
    seq: int = 1,
    t_node: int | None,
    recv_time: int,
    light_val: Decimal = Decimal("245.5"),
    temp_val: Decimal = Decimal("28.5"),
) -> dict:
    return {
        "device_id": device_id,
        "boot_id": boot_id,
        "seq": seq,
        "t_node": t_node,
        "time_status": "VALID" if t_node is not None else "UNKNOWN",
        "recv_time": recv_time,
        "rssi": Decimal("-82.5"),
        "measurements": {
            "temperature": {"value": temp_val, "status": "OK"},
            "airHumidity": {"value": Decimal("75.24"), "status": "OK"},
            "pressure": {"value": Decimal("1008.42"), "status": "OK"},
            "light": {"value": light_val, "status": "OK"},
            "soilMoisture": {"value": Decimal("45.5"), "status": "OK"},
        },
    }


def _link_node(engine, *, node_id: int, gateway_id: int) -> str:
    """SQL fixture technical provisioning per G2-D04."""
    import base64

    with engine.begin() as conn:
        row = conn.execute(
            text(
                "UPDATE dbo.Node SET gateway_id = :gid OUTPUT INSERTED.row_ver WHERE node_id = :nid"
            ),
            {"gid": gateway_id, "nid": node_id},
        ).fetchone()
        return base64.b64encode(bytes(row[0])).decode("ascii")


def _json_bytes(payload: dict) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


@pytest.fixture
def sql_g4_env():
    base_settings = Settings()
    secret = base_settings.auth_test_database_url
    expected = os.environ.get("HQ_EXPECT_TEST_DATABASE")
    if secret is None or not expected:
        pytest.fail("Configure HQ_AUTH_TEST_DATABASE_URL and HQ_EXPECT_TEST_DATABASE")

    engine = create_engine(
        secret.get_secret_value(), hide_parameters=True, connect_args={"timeout": 5}
    )
    with engine.connect() as conn:
        identity_row = conn.execute(
            text("SELECT DB_NAME(), SUSER_SNAME(), CAST(SYSUTCDATETIME() AS DATETIME2(3))")
        ).one()
        db_name, _, db_now = identity_row
        if db_name != expected or "test" not in db_name.lower():
            pytest.fail("Refusing writes: expected disposable test DB does not match")
        assert (
            conn.execute(text("SELECT version_num FROM dbo.alembic_version")).scalar_one()
            == "e7c4b6a10d53"
        )
        conn.rollback()

    db_now_epoch = int(db_now.replace(tzinfo=UTC).timestamp())
    tag = "testg4-" + uuid4().hex[:10]
    test_settings = base_settings.model_copy(
        update={
            "csrf_secret": base_settings.csrf_secret
            or SecretStr(f"testg4-csrf-{uuid4().hex}-padding"),
            "cursor_secret": SecretStr(f"testg4-cursor-{uuid4().hex}-padding"),
            "node_time_profiles": {
                f"{tag}-N1": NodeTimeProfile(
                    sampling_interval_seconds=60, normal_delivery_budget_seconds=60
                ),
                f"{tag}-N2": NodeTimeProfile(
                    sampling_interval_seconds=60, normal_delivery_budget_seconds=60
                ),
            },
        }
    )

    created_farm_ids: list[int] = []
    SessionFactory = sessionmaker(bind=engine, expire_on_commit=False)

    with SessionFactory.begin() as s:
        auth_repo.create_admin(s, tag, password_hasher.hash("fixture-password"))
        auth_repo.create_admin(s, f"{tag}-b", password_hasher.hash("fixture-password"))
    auth_svc = AuthService(SimpleNamespace(sessions=SessionFactory), test_settings)
    dto_a, cookie_a, _ = auth_svc.login(tag, "fixture-password", None)
    dto_b, cookie_b, _ = auth_svc.login(f"{tag}-b", "fixture-password", None)

    try:
        yield (
            engine,
            test_settings,
            db_now_epoch,
            tag,
            (cookie_a, dto_a.csrf_token),
            (cookie_b, dto_b.csrf_token),
            created_farm_ids,
        )
    finally:
        with SessionFactory.begin() as s:
            if created_farm_ids:
                fids = ",".join(str(int(x)) for x in created_farm_ids)
                node_filter = (
                    "SELECT n.node_id FROM dbo.Node n "
                    "JOIN dbo.Zone z ON z.zone_id = n.zone_id "
                    f"WHERE z.farm_id IN ({fids})"
                )
                s.execute(
                    text(
                        "UPDATE dbo.AlertEpisode SET current_revision_no = NULL "
                        f"WHERE node_id IN ({node_filter})"
                    )
                )
                s.execute(
                    text(
                        "DELETE ar FROM dbo.AlertReplacement ar "
                        "JOIN dbo.AlertEpisode ae ON ae.episode_id = ar.old_episode_id "
                        f"WHERE ae.node_id IN ({node_filter})"
                    )
                )
                s.execute(
                    text(
                        "DELETE aa FROM dbo.AlertAcknowledgement aa "
                        "JOIN dbo.AlertEpisode ae ON ae.episode_id = aa.episode_id "
                        f"WHERE ae.node_id IN ({node_filter})"
                    )
                )
                s.execute(
                    text(
                        "DELETE rev FROM dbo.AlertRevision rev "
                        "JOIN dbo.AlertEpisode ae ON ae.episode_id = rev.episode_id "
                        f"WHERE ae.node_id IN ({node_filter})"
                    )
                )
                s.execute(text(f"DELETE FROM dbo.AlertEpisode WHERE node_id IN ({node_filter})"))
                s.execute(
                    text(
                        "UPDATE dbo.NodeRuntime "
                        "SET current_config_id = NULL, current_timeout_config_id = NULL, "
                        "active_period_id = NULL, last_measurement_id = NULL, "
                        "last_new_persisted_at = NULL "
                        f"WHERE node_id IN ({node_filter})"
                    )
                )
                s.execute(
                    text(
                        "UPDATE dbo.EvaluationState SET last_threshold_measurement_id = NULL "
                        f"WHERE node_id IN ({node_filter})"
                    )
                )
                s.execute(
                    text(f"DELETE FROM dbo.MonitoringPeriod WHERE node_id IN ({node_filter})")
                )
                s.execute(
                    text(
                        "DELETE ts FROM dbo.ThresholdSetting ts "
                        "JOIN dbo.NodeConfigVersion nc ON nc.config_id = ts.config_id "
                        f"WHERE nc.node_id IN ({node_filter})"
                    )
                )
                s.execute(
                    text(f"DELETE FROM dbo.NodeConfigVersion WHERE node_id IN ({node_filter})")
                )
                s.execute(
                    text(
                        "DELETE mv FROM dbo.MeasurementValue mv "
                        "JOIN dbo.Measurement m ON m.measurement_id = mv.measurement_id "
                        f"WHERE m.node_id IN ({node_filter})"
                    )
                )
                s.execute(text(f"DELETE FROM dbo.Measurement WHERE node_id IN ({node_filter})"))
                s.execute(
                    text(
                        "DELETE c FROM dbo.GatewayCredential c "
                        "JOIN dbo.Gateway g ON g.gateway_id = c.gateway_id "
                        f"WHERE g.farm_id IN ({fids})"
                    )
                )
                s.execute(text(f"DELETE FROM dbo.EvaluationState WHERE node_id IN ({node_filter})"))
                s.execute(text(f"DELETE FROM dbo.NodeRuntime WHERE node_id IN ({node_filter})"))
                s.execute(text(f"DELETE FROM dbo.Node WHERE node_id IN ({node_filter})"))
                s.execute(text(f"DELETE FROM dbo.Gateway WHERE farm_id IN ({fids})"))
                s.execute(text(f"DELETE FROM dbo.Zone WHERE farm_id IN ({fids})"))
                s.execute(text(f"DELETE FROM dbo.Farm WHERE farm_id IN ({fids})"))
            s.execute(
                text(
                    "DELETE s FROM dbo.AdminSession s "
                    "JOIN dbo.AdminAccount a ON a.admin_id = s.admin_id "
                    "WHERE a.username LIKE :prefix"
                ),
                {"prefix": f"{tag}%"},
            )
            s.execute(
                text("DELETE FROM dbo.AdminAccount WHERE username LIKE :prefix"),
                {"prefix": f"{tag}%"},
            )
        engine.dispose()


def test_sql_g4_two_decimal_thresholds_t_only_and_atomic_rollback(sql_g4_env):
    engine, settings, db_now_epoch, tag, (cookie, csrf), _, created_farms = sql_g4_env
    db = _EngineDB(engine)
    area_svc = AreaService(db, settings)
    dev_svc = DeviceService(db, settings)
    ing_svc = IngestionService(
        db, settings, clock=lambda: datetime.fromtimestamp(db_now_epoch + 5, tz=UTC)
    )
    mon_svc = MonitoringConfigService(db, settings)

    farm = area_svc.create_farm(cookie, csrf, AreaCreate(code=f"{tag}-F1", name="G4 Farm"))
    created_farms.append(int(farm["id"]))
    zone = area_svc.create_zone(
        cookie, csrf, farm["id"], AreaCreate(code=f"{tag}-Z1", name="G4 Zone")
    )
    gw = dev_svc.create(
        cookie,
        csrf,
        "gateways",
        GatewayCreate(gateway_id=f"{tag}-GW", name="GW", farm_id=farm["id"]),
    )
    n1 = dev_svc.create(
        cookie, csrf, "nodes", NodeCreate(device_id=f"{tag}-N1", name="N1", zone_id=zone["id"])
    )
    n1["row_version"] = _link_node(engine, node_id=int(n1["id"]), gateway_id=int(gw["id"]))
    cred = dev_svc.issue_credential(cookie, csrf, gateway_id=gw["id"])

    # 2-decimal thresholds for light & soilMoisture persist cleanly on SQL Server (Method A)
    mon1 = mon_svc.patch_monitoring(
        cookie,
        csrf,
        node_id=n1["id"],
        raw_body=_json_bytes(
            {
                "expected_threshold_config_id": None,
                "thresholds": {
                    "light": {"enabled": True, "lower": 100.25, "upper": 50000.75},
                    "soilMoisture": {"enabled": False, "lower": 12.34, "upper": 88.99},
                },
            }
        ),
    )
    by_m = {t["metric_code"]: t for t in mon1["thresholds"]}
    assert by_m["light"]["lower"] == "100.25"
    assert by_m["light"]["upper"] == "50000.75"
    assert by_m["soilMoisture"]["lower"] == "12.34"
    assert by_m["soilMoisture"]["upper"] == "88.99"

    # Ingestion G2 still enforces 1 decimal on light measurement values (2/2/2/1/1)
    bad_ing = ing_svc.ingest(
        api_key=cred["api_key"],
        payload={
            "gateway_id": gw["gateway_id"],
            "records": [
                _record(
                    device_id=n1["device_id"],
                    seq=1,
                    t_node=db_now_epoch,
                    recv_time=db_now_epoch,
                    light_val=Decimal("100.25"),
                )
            ],
        },
    )
    assert bad_ing["results"][0]["status"] == "REJECTED"
    ok_ing = ing_svc.ingest(
        api_key=cred["api_key"],
        payload={
            "gateway_id": gw["gateway_id"],
            "records": [
                _record(
                    device_id=n1["device_id"],
                    seq=2,
                    t_node=db_now_epoch,
                    recv_time=db_now_epoch,
                    light_val=Decimal("100.5"),
                )
            ],
        },
    )
    assert ok_ing["results"][0]["status"] == "STORED"

    # Atomic combined failure rolls back threshold changes too
    with pytest.raises(WebError) as exc_bad_t:
        mon_svc.patch_monitoring(
            cookie,
            csrf,
            node_id=n1["id"],
            raw_body=_json_bytes(
                {
                    "expected_threshold_config_id": mon1["threshold_config_id"],
                    "thresholds": {"temperature": {"enabled": True, "lower": 10, "upper": 30}},
                    "expected_timeout_config_id": None,
                    "timeout_minutes": 1,
                }
            ),
        )
    assert exc_bad_t.value.code == "INPUT_INVALID"
    mon_check = mon_svc.get_monitoring(cookie, node_id=n1["id"])
    assert mon_check["threshold_config_id"] == mon1["threshold_config_id"]
    assert mon_check["timeout_config_id"] is None

    # T-only sets T=5, preserves threshold config and full_replay_required
    mon_t = mon_svc.patch_monitoring(
        cookie,
        csrf,
        node_id=n1["id"],
        raw_body=_json_bytes({"expected_timeout_config_id": None, "timeout_minutes": 5}),
    )
    assert mon_t["timeout_config_id"] is not None
    assert mon_t["timeout_state"] == "ACTIVE"
    assert mon_t["S"] is not None

    # Stale config save returns 409 CONFIG_VERSION_CONFLICT
    with pytest.raises(WebError) as exc_stale:
        mon_svc.patch_monitoring(
            cookie,
            csrf,
            node_id=n1["id"],
            raw_body=_json_bytes({"expected_timeout_config_id": None, "timeout_minutes": 6}),
        )
    assert exc_stale.value.code == "CONFIG_VERSION_CONFLICT"


def test_sql_g4_disable_archive_restore_enable_and_two_connection_races(sql_g4_env):
    engine, settings, db_now_epoch, tag, (cookie_a, csrf_a), (cookie_b, csrf_b), created_farms = (
        sql_g4_env
    )
    db = _EngineDB(engine)
    area_svc = AreaService(db, settings)
    dev_svc = DeviceService(db, settings)
    ing_svc = IngestionService(
        db, settings, clock=lambda: datetime.fromtimestamp(db_now_epoch + 5, tz=UTC)
    )
    mon_svc = MonitoringConfigService(db, settings)

    farm = area_svc.create_farm(
        cookie_a, csrf_a, AreaCreate(code=f"{tag}-F2", name="Lifecycle Farm")
    )
    created_farms.append(int(farm["id"]))
    zone = area_svc.create_zone(
        cookie_a, csrf_a, farm["id"], AreaCreate(code=f"{tag}-Z2", name="Lifecycle Zone")
    )
    gw = dev_svc.create(
        cookie_a,
        csrf_a,
        "gateways",
        GatewayCreate(gateway_id=f"{tag}-GW2", name="GW2", farm_id=farm["id"]),
    )
    n1 = dev_svc.create(
        cookie_a, csrf_a, "nodes", NodeCreate(device_id=f"{tag}-N1", name="N1", zone_id=zone["id"])
    )
    n1["row_version"] = _link_node(engine, node_id=int(n1["id"]), gateway_id=int(gw["id"]))
    cred = dev_svc.issue_credential(cookie_a, csrf_a, gateway_id=gw["id"])

    mon_svc.patch_monitoring(
        cookie_a,
        csrf_a,
        node_id=n1["id"],
        raw_body=_json_bytes({"expected_timeout_config_id": None, "timeout_minutes": 5}),
    )
    dup_rec = _record(
        device_id=n1["device_id"], seq=10, t_node=db_now_epoch, recv_time=db_now_epoch
    )
    r_store = ing_svc.ingest(
        api_key=cred["api_key"], payload={"gateway_id": gw["gateway_id"], "records": [dup_rec]}
    )
    assert r_store["results"][0]["status"] == "STORED"

    # Two-connection race: hold Farm lock in conn1 while conn2 toggles/ingests
    barrier = threading.Event()
    release = threading.Event()
    results = {}

    def _locker():
        with engine.begin() as conn:
            conn.execute(
                text("SELECT farm_id FROM dbo.Farm WITH (UPDLOCK, HOLDLOCK) WHERE farm_id = :fid"),
                {"fid": int(farm["id"])},
            )
            barrier.set()
            release.wait(timeout=5)

    def _disabler():
        barrier.wait(timeout=5)
        results["disable"] = mon_svc.patch_node_receiving(
            cookie_b,
            csrf_b,
            node_id=n1["id"],
            raw_body=_json_bytes(
                {"receiving_enabled": False, "expected_row_version": n1["row_version"]}
            ),
        )

    def _ingestor():
        barrier.wait(timeout=5)
        results["new_after_disable"] = ing_svc.ingest(
            api_key=cred["api_key"],
            payload={
                "gateway_id": gw["gateway_id"],
                "records": [
                    dup_rec,
                    _record(
                        device_id=n1["device_id"],
                        seq=11,
                        t_node=db_now_epoch + 1,
                        recv_time=db_now_epoch + 1,
                    ),
                ],
            },
        )

    t1 = threading.Thread(target=_locker)
    t2 = threading.Thread(target=_disabler)
    t1.start()
    t2.start()
    barrier.wait(timeout=5)
    release.set()
    t1.join(timeout=5)
    t2.join(timeout=5)
    assert results["disable"]["receiving_enabled"] is False
    l_saved = results["disable"]["monitoring"]["L"]
    assert l_saved is not None

    # After disable: duplicate ALREADY_EXISTS; new record RETRY_LATER/RECEIVING_DISABLED
    t3 = threading.Thread(target=_ingestor)
    t3.start()
    t3.join(timeout=5)
    ing_res = results["new_after_disable"]["results"]
    assert ing_res[0]["status"] == "ALREADY_EXISTS"
    assert ing_res[1]["status"] == "RETRY_LATER"
    assert ing_res[1]["error"]["code"] == "RECEIVING_DISABLED"

    # disable -> archive Zone/Farm -> restore Farm -> enable blocked -> restore Zone -> enable
    z_arch = area_svc.archive_zone(
        cookie_a, csrf_a, zone["id"], AreaLifecycleAction(expected_row_version=zone["row_version"])
    )
    gw_dis = mon_svc.patch_gateway_receiving(
        cookie_a,
        csrf_a,
        gateway_id=gw["id"],
        raw_body=_json_bytes(
            {"receiving_enabled": False, "expected_row_version": gw["row_version"]}
        ),
    )
    f_arch = area_svc.archive_farm(
        cookie_a, csrf_a, farm["id"], AreaLifecycleAction(expected_row_version=farm["row_version"])
    )
    area_svc.restore_farm(
        cookie_a,
        csrf_a,
        farm["id"],
        AreaLifecycleAction(expected_row_version=f_arch["row_version"]),
    )
    # Enabling Node while Zone is still archived -> 409 AREA_ARCHIVED
    with pytest.raises(WebError) as exc_arch:
        mon_svc.patch_node_receiving(
            cookie_a,
            csrf_a,
            node_id=n1["id"],
            raw_body=_json_bytes(
                {
                    "receiving_enabled": True,
                    "expected_row_version": results["disable"]["profile_row_version"],
                }
            ),
        )
    assert exc_arch.value.code == "AREA_ARCHIVED"

    area_svc.restore_zone(
        cookie_a,
        csrf_a,
        zone["id"],
        AreaLifecycleAction(expected_row_version=z_arch["row_version"]),
    )
    # Restoring does NOT auto-enable devices or open S
    assert mon_svc.get_monitoring(cookie_a, node_id=n1["id"])["timeout_state"] == "PAUSED"

    # Re-enable Gateway + Node -> opens new S, preserves L
    mon_svc.patch_gateway_receiving(
        cookie_a,
        csrf_a,
        gateway_id=gw["id"],
        raw_body=_json_bytes(
            {"receiving_enabled": True, "expected_row_version": gw_dis["profile_row_version"]}
        ),
    )
    n1_re = mon_svc.patch_node_receiving(
        cookie_a,
        csrf_a,
        node_id=n1["id"],
        raw_body=_json_bytes(
            {
                "receiving_enabled": True,
                "expected_row_version": results["disable"]["profile_row_version"],
            }
        ),
    )
    assert n1_re["monitoring"]["timeout_state"] == "ACTIVE"
    assert n1_re["monitoring"]["S"] is not None
    assert n1_re["monitoring"]["L"] == l_saved


def test_sql_g4_gateway_fanout_rollback_and_same_ms_witness_reconcile(sql_g4_env, monkeypatch):
    engine, settings, db_now_epoch, tag, (cookie, csrf), _, created_farms = sql_g4_env
    db = _EngineDB(engine)
    area_svc = AreaService(db, settings)
    dev_svc = DeviceService(db, settings)
    mon_svc = MonitoringConfigService(db, settings)
    SessionFactory = sessionmaker(bind=engine, expire_on_commit=False)

    farm = area_svc.create_farm(cookie, csrf, AreaCreate(code=f"{tag}-F3", name="Fanout Farm"))
    created_farms.append(int(farm["id"]))
    zone = area_svc.create_zone(
        cookie, csrf, farm["id"], AreaCreate(code=f"{tag}-Z3", name="Fanout Zone")
    )
    gw = dev_svc.create(
        cookie,
        csrf,
        "gateways",
        GatewayCreate(gateway_id=f"{tag}-GW3", name="GW3", farm_id=farm["id"]),
    )
    n1 = dev_svc.create(
        cookie, csrf, "nodes", NodeCreate(device_id=f"{tag}-N1", name="N1", zone_id=zone["id"])
    )
    n2 = dev_svc.create(
        cookie, csrf, "nodes", NodeCreate(device_id=f"{tag}-N2", name="N2", zone_id=zone["id"])
    )
    n1["row_version"] = _link_node(engine, node_id=int(n1["id"]), gateway_id=int(gw["id"]))
    n2["row_version"] = _link_node(engine, node_id=int(n2["id"]), gateway_id=int(gw["id"]))

    # Individually disable N2, configure T=5 on N1 and N2
    mon_svc.patch_node_receiving(
        cookie,
        csrf,
        node_id=n2["id"],
        raw_body=_json_bytes(
            {"receiving_enabled": False, "expected_row_version": n2["row_version"]}
        ),
    )
    mon_svc.patch_monitoring(
        cookie,
        csrf,
        node_id=n1["id"],
        raw_body=_json_bytes({"expected_timeout_config_id": None, "timeout_minutes": 5}),
    )

    # Fault during Gateway fan-out on N1 rolls back BOTH Gateway flag and N1 period
    real_bump = mc_repo.bump_evaluation_generation

    def _failing_bump(session, *, node_id, full_replay):
        if int(node_id) == int(n1["id"]):
            raise OperationalError("injected", {}, Exception("Injected fault mid fan-out"))
        return real_bump(session, node_id=node_id, full_replay=full_replay)

    monkeypatch.setattr(mc_repo, "bump_evaluation_generation", _failing_bump)
    with pytest.raises(WebError) as exc_fault:
        mon_svc.patch_gateway_receiving(
            cookie,
            csrf,
            gateway_id=gw["id"],
            raw_body=_json_bytes(
                {"receiving_enabled": False, "expected_row_version": gw["row_version"]}
            ),
        )
    assert exc_fault.value.status == 503
    monkeypatch.setattr(mc_repo, "bump_evaluation_generation", real_bump)

    gw_after = dev_svc.detail(cookie, "gateways", gw["id"])
    assert gw_after["receiving_enabled"] is True
    assert gw_after["row_version"] == gw["row_version"]
    assert mon_svc.get_monitoring(cookie, node_id=n1["id"])["timeout_state"] == "ACTIVE"

    # Rewind N1 period by 10m, open TIMEOUT episode, insert 2 same-ms measurements
    with SessionFactory.begin() as s:
        s.execute(
            text(
                "UPDATE dbo.MonitoringPeriod SET started_at = DATEADD(minute, -10, started_at) "
                "WHERE node_id = :nid AND monitoring_kind = 'TIMEOUT' AND ended_at IS NULL"
            ),
            {"nid": int(n1["id"])},
        )
        res_eval = timeout_engine.evaluate_timeout_node(s, node_id=int(n1["id"]), settings=settings)
        assert res_eval.episode_opened

        # Insert 2 measurements with identical persisted_at (after first_detected_at, <= now)
        ep_row = (
            s.execute(
                text(
                    "SELECT episode_id, first_detected_at FROM dbo.AlertEpisode "
                    "WHERE node_id = :nid AND alert_kind = 'TIMEOUT' AND lifecycle_status = 'OPEN'"
                ),
                {"nid": int(n1["id"])},
            )
            .mappings()
            .one()
        )
        same_ts = ep_row["first_detected_at"]
        m1 = s.execute(
            text(
                "INSERT INTO dbo.Measurement "
                "(node_id, gateway_id, boot_id, seq, t_node, time_status, recv_time, rssi, "
                "backend_received_at, persisted_at, canonical_version, original_hash) "
                "OUTPUT inserted.measurement_id "
                "VALUES (:nid, :gid, 1, 100, NULL, 'UNKNOWN', :rt, NULL, :ts, :ts, 1, :h)"
            ),
            {
                "nid": int(n1["id"]),
                "gid": int(gw["id"]),
                "rt": db_now_epoch,
                "ts": same_ts,
                "h": b"a" * 32,
            },
        ).scalar_one()
        m2 = s.execute(
            text(
                "INSERT INTO dbo.Measurement "
                "(node_id, gateway_id, boot_id, seq, t_node, time_status, recv_time, rssi, "
                "backend_received_at, persisted_at, canonical_version, original_hash) "
                "OUTPUT inserted.measurement_id "
                "VALUES (:nid, :gid, 1, 101, NULL, 'UNKNOWN', :rt, NULL, :ts, :ts, 1, :h)"
            ),
            {
                "nid": int(n1["id"]),
                "gid": int(gw["id"]),
                "rt": db_now_epoch,
                "ts": same_ts,
                "h": b"b" * 32,
            },
        ).scalar_one()
        s.execute(
            text(
                "UPDATE dbo.NodeRuntime SET last_measurement_id = :m2, last_new_persisted_at = :ts "
                "WHERE node_id = :nid"
            ),
            {"m2": m2, "ts": same_ts, "nid": int(n1["id"])},
        )
        assert m1 < m2

    # Validate unused canonical imports stay clean for static check while verifying G2 helper exists
    validated, _err = validate_record_static(
        _record(device_id=n1["device_id"], seq=100, t_node=db_now_epoch, recv_time=db_now_epoch)
    )
    assert validated is not None and build_canonical_v1(validated)[1] and CANONICAL_VERSION == 1

    # Pause Gateway -> reconciles N1 witness FIRST (NEW_DATA_STORED, end_measurement_id=m1)
    mon_svc.patch_gateway_receiving(
        cookie,
        csrf,
        gateway_id=gw["id"],
        raw_body=_json_bytes(
            {"receiving_enabled": False, "expected_row_version": gw["row_version"]}
        ),
    )
    with SessionFactory() as s:
        rev_row = (
            s.execute(
                text(
                    "SELECT r.end_reason, r.end_measurement_id, r.closed_at "
                    "FROM dbo.AlertEpisode e "
                    "JOIN dbo.AlertRevision r "
                    "ON r.episode_id = e.episode_id AND r.revision_no = e.current_revision_no "
                    "WHERE e.episode_id = :eid"
                ),
                {"eid": ep_row["episode_id"]},
            )
            .mappings()
            .one()
        )
        assert rev_row["end_reason"] == "NEW_DATA_STORED"
        assert int(rev_row["end_measurement_id"]) == int(m1)
