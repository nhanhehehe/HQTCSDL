"""Opt-in SQL Server integration tests for G4 monitoring worker (FL-09 + FL-10).

Requires HQ_RUN_SQL_TESTS=1 and HQ_EXPECT_TEST_DATABASE=<disposable_test_db>.
Reads ONLY HQ_AUTH_TEST_DATABASE_URL (never falls back to HQ_DATABASE_URL).
"""

import json
import os
import time
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pyodbc
import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.core.config import NodeTimeProfile, Settings
from app.modules.alerts import worker as worker_mod
from app.modules.alerts.worker import MonitoringWorker
from app.modules.areas.schemas import AreaCreate
from app.modules.areas.service import AreaService
from app.modules.auth import repository as auth_repo
from app.modules.auth.security import password_hasher
from app.modules.auth.service import AuthService
from app.modules.devices.schemas import GatewayCreate, NodeCreate
from app.modules.devices.service import DeviceService
from app.modules.ingestion.service import IngestionService
from app.modules.monitoring_config.service import MonitoringConfigService

pyodbc.pooling = False

pytestmark = pytest.mark.skipif(
    os.environ.get("HQ_RUN_SQL_TESTS") != "1", reason="SQL writes require explicit opt-in"
)


class _EngineDB:
    def __init__(self, engine):
        self.engine = engine

    def sessions(self):
        return sessionmaker(bind=self.engine, expire_on_commit=False)()


def _record(*, device_id: str, seq: int, t_node: int, recv_time: int, temp_val: Decimal) -> dict:
    return {
        "device_id": device_id,
        "boot_id": 1,
        "seq": seq,
        "t_node": t_node,
        "time_status": "VALID",
        "recv_time": recv_time,
        "rssi": Decimal("-80.0"),
        "measurements": {
            "temperature": {"value": temp_val, "status": "OK"},
            "airHumidity": {"value": Decimal("65.00"), "status": "OK"},
            "pressure": {"value": Decimal("1010.00"), "status": "OK"},
            "light": {"value": Decimal("300.0"), "status": "OK"},
            "soilMoisture": {"value": Decimal("45.0"), "status": "OK"},
        },
    }


def _link_node(engine, *, node_id: int, gateway_id: int) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE dbo.Node SET gateway_id = :gid WHERE node_id = :nid"),
            {"gid": gateway_id, "nid": node_id},
        )


def _json_bytes(payload: dict) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


@pytest.fixture
def sql_g4_worker_env():
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
    tag = "testwkr-" + uuid4().hex[:8]
    test_settings = base_settings.model_copy(
        update={
            "csrf_secret": base_settings.csrf_secret
            or SecretStr(f"testwkr-csrf-{uuid4().hex}-padding"),
            "cursor_secret": base_settings.cursor_secret
            or SecretStr(f"testwkr-cursor-{uuid4().hex}-padding"),
            "monitoring_enabled": True,
            "monitoring_tick_seconds": 5,
            "monitoring_retry_seconds": 15,
            "monitoring_shutdown_timeout_seconds": 10,
            "monitoring_command_timeout_seconds": 15,
            "monitoring_lock_timeout_ms": 5000,
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
    auth_svc = AuthService(SimpleNamespace(sessions=SessionFactory), test_settings)
    dto, cookie, _ = auth_svc.login(tag, "fixture-password", None)

    try:
        yield engine, test_settings, db_now_epoch, tag, (cookie, dto.csrf_token), created_farm_ids
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
                    "WHERE a.username = :u"
                ),
                {"u": tag},
            )
            s.execute(text("DELETE FROM dbo.AdminAccount WHERE username = :u"), {"u": tag})
        engine.dispose()


def _provision_nodes(engine, settings, auth, tag: str, created_farm_ids: list[int]):
    db = _EngineDB(engine)
    area_svc = AreaService(db, settings)
    dev_svc = DeviceService(db, settings)
    cookie, csrf = auth

    farm = area_svc.create_farm(
        cookie, csrf, AreaCreate(code=f"{tag}-F", name="Worker Farm", description=None)
    )
    created_farm_ids.append(int(farm["id"]))
    zone = area_svc.create_zone(
        cookie, csrf, farm["id"], AreaCreate(code=f"{tag}-Z", name="Worker Zone", description=None)
    )
    gw = dev_svc.create(
        cookie,
        csrf,
        "gateways",
        GatewayCreate(farm_id=farm["id"], gateway_id=f"{tag}-GW", name="GW"),
    )
    cred = dev_svc.issue_credential(cookie, csrf, gateway_id=gw["id"])
    n1 = dev_svc.create(
        cookie, csrf, "nodes", NodeCreate(zone_id=zone["id"], device_id=f"{tag}-N1", name="N1")
    )
    n2 = dev_svc.create(
        cookie, csrf, "nodes", NodeCreate(zone_id=zone["id"], device_id=f"{tag}-N2", name="N2")
    )
    _link_node(engine, node_id=int(n1["id"]), gateway_id=int(gw["id"]))
    _link_node(engine, node_id=int(n2["id"]), gateway_id=int(gw["id"]))
    return int(n1["id"]), int(n2["id"]), gw["gateway_id"], cred["api_key"]


def test_sql_worker_restart_recovery_fault_and_node_isolation(sql_g4_worker_env, monkeypatch):
    engine, settings, db_now_epoch, tag, auth, created_farm_ids = sql_g4_worker_env
    n1_id, n2_id, gw_code, api_key = _provision_nodes(engine, settings, auth, tag, created_farm_ids)
    mc_svc = MonitoringConfigService(_EngineDB(engine), settings)
    cookie, csrf = auth
    cfg_body = _json_bytes(
        {
            "expected_threshold_config_id": None,
            "expected_timeout_config_id": None,
            "timeout_minutes": 5,
            "thresholds": {"temperature": {"enabled": True, "lower": 18.00, "upper": 30.00}},
        }
    )
    mc_svc.patch_monitoring(cookie, csrf, node_id=str(n1_id), raw_body=cfg_body)
    mc_svc.patch_monitoring(cookie, csrf, node_id=str(n2_id), raw_body=cfg_body)

    # Ingest a breaching sample on N1 after ACK2, before worker starts.
    ing = IngestionService(
        _EngineDB(engine),
        settings,
        clock=lambda: datetime.fromtimestamp(db_now_epoch + 20, tz=UTC),
    )
    res = ing.ingest(
        api_key=api_key,
        payload={
            "gateway_id": gw_code,
            "records": [
                _record(
                    device_id=f"{tag}-N1",
                    seq=1,
                    t_node=db_now_epoch + 5,
                    recv_time=db_now_epoch + 5,
                    temp_val=Decimal("35.00"),
                )
            ],
        },
    )
    assert res["results"][0]["status"] == "STORED"

    # Worker 1 starts and runs tick -> evaluates N1 and N2, records tick duration.
    resource = f"hqtcsdl:test:{tag}"
    w1 = MonitoringWorker(_EngineDB(engine), settings, applock_resource=resource)
    try:
        t0 = time.perf_counter()
        stats1 = w1.run_tick()
        tick_ms = (time.perf_counter() - t0) * 1000.0
        print(f"SQL worker tick duration: {tick_ms:.2f} ms (nodes={stats1.nodes_scanned})")
        assert stats1.executed and stats1.nodes_scanned >= 2
        assert w1.health_status() == "running"

        with engine.connect() as conn:
            st1 = (
                conn.execute(
                    text(
                        "SELECT desired_generation, completed_generation, full_replay_required, "
                        "last_success_at, last_error_code, timeout_last_error_code "
                        "FROM dbo.EvaluationState WHERE node_id = :nid"
                    ),
                    {"nid": n1_id},
                )
                .mappings()
                .one()
            )
            ep_count = conn.execute(
                text(
                    "SELECT COUNT(*) FROM dbo.AlertEpisode "
                    "WHERE node_id = :nid AND alert_kind = 'THRESHOLD' "
                    "AND lifecycle_status = 'OPEN'"
                ),
                {"nid": n1_id},
            ).scalar_one()
        assert st1["completed_generation"] == st1["desired_generation"]
        assert not st1["full_replay_required"]
        assert st1["last_success_at"] is not None
        assert st1["last_error_code"] is None and st1["timeout_last_error_code"] is None
        assert ep_count == 1
        first_success_at = st1["last_success_at"]

        # Tick with no new data (desired == completed): TIMEOUT still advances last_success_at.
        stats_idle = w1.run_tick()
        assert stats_idle.executed
        with engine.connect() as conn:
            second_success_at = conn.execute(
                text("SELECT last_success_at FROM dbo.EvaluationState WHERE node_id = :nid"),
                {"nid": n1_id},
            ).scalar_one()
        assert second_success_at is not None and second_success_at >= first_success_at

        # Fault injection on N1 TIMEOUT while desired == completed -> isolated from N2.
        real_eval_to = worker_mod.evaluate_timeout_node

        def _fault_n1_timeout(session, *, node_id, settings):
            if node_id == n1_id:
                raise RuntimeError("injected sql fault")
            return real_eval_to(session, node_id=node_id, settings=settings)

        monkeypatch.setattr(worker_mod, "evaluate_timeout_node", _fault_n1_timeout)
        w1.run_tick()
        with engine.connect() as conn:
            err_row = (
                conn.execute(
                    text(
                        "SELECT timeout_last_error_code, timeout_retry_after, last_error_code "
                        "FROM dbo.EvaluationState WHERE node_id = :nid"
                    ),
                    {"nid": n1_id},
                )
                .mappings()
                .one()
            )
        assert err_row["timeout_last_error_code"] == "TIMEOUT_EVALUATION_FAILED"
        assert err_row["timeout_retry_after"] is not None
        assert err_row["last_error_code"] is None

        # Restore TIMEOUT and expire backoff on N1 -> worker restart recovers cleanly.
        monkeypatch.setattr(worker_mod, "evaluate_timeout_node", real_eval_to)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE dbo.EvaluationState SET timeout_retry_after = :past "
                    "WHERE node_id = :nid"
                ),
                {
                    "past": datetime.fromtimestamp(db_now_epoch - 60, tz=UTC).replace(tzinfo=None),
                    "nid": n1_id,
                },
            )
    finally:
        w1.stop()
        w1._close_connection_on_worker_thread()

    # Restart new worker instance after fault & recovery.
    w2 = MonitoringWorker(_EngineDB(engine), settings, applock_resource=resource)
    try:
        w2.run_tick()
        with engine.connect() as conn:
            rec_row = (
                conn.execute(
                    text(
                        "SELECT timeout_last_error_code, timeout_retry_after, last_success_at "
                        "FROM dbo.EvaluationState WHERE node_id = :nid"
                    ),
                    {"nid": n1_id},
                )
                .mappings()
                .one()
            )
        assert rec_row["timeout_last_error_code"] is None
        assert rec_row["timeout_retry_after"] is None
        assert rec_row["last_success_at"] >= second_success_at
    finally:
        w2.stop()
        w2._close_connection_on_worker_thread()


def test_sql_worker_two_connection_applock_takeover_and_session_loss(sql_g4_worker_env):
    engine, settings, db_now_epoch, tag, auth, created_farm_ids = sql_g4_worker_env
    n1_id, _, gw_code, api_key = _provision_nodes(engine, settings, auth, tag, created_farm_ids)
    mc_svc = MonitoringConfigService(_EngineDB(engine), settings)
    cookie, csrf = auth
    mc_svc.patch_monitoring(
        cookie,
        csrf,
        node_id=str(n1_id),
        raw_body=_json_bytes(
            {
                "expected_threshold_config_id": None,
                "thresholds": {"temperature": {"enabled": True, "lower": 18.00, "upper": 30.00}},
            }
        ),
    )
    ing = IngestionService(
        _EngineDB(engine),
        settings,
        clock=lambda: datetime.fromtimestamp(db_now_epoch + 20, tz=UTC),
    )
    ing.ingest(
        api_key=api_key,
        payload={
            "gateway_id": gw_code,
            "records": [
                _record(
                    device_id=f"{tag}-N1",
                    seq=1,
                    t_node=db_now_epoch + 5,
                    recv_time=db_now_epoch + 5,
                    temp_val=Decimal("35.00"),
                )
            ],
        },
    )

    resource = f"hqtcsdl:test:{tag}"
    url = settings.auth_test_database_url.get_secret_value()
    e1 = create_engine(url, pool_pre_ping=True, hide_parameters=True, connect_args={"timeout": 5})
    e2 = create_engine(url, pool_pre_ping=True, hide_parameters=True, connect_args={"timeout": 5})
    w1 = MonitoringWorker(_EngineDB(e1), settings, applock_resource=resource)
    w2 = MonitoringWorker(_EngineDB(e2), settings, applock_resource=resource)
    try:
        # w1 takes exclusive session applock; w2 must get lock_unavailable and not execute.
        assert w1.run_tick().executed is True
        assert w1.health_status() == "running"

        s2 = w2.run_tick()
        assert s2.executed is False
        assert w2.health_status() == "lock_unavailable"

        # Release w1 cleanly -> w2 takes over on next tick, no duplicate episodes created.
        w1._close_connection_on_worker_thread()
        assert w2.run_tick().executed is True
        assert w2.health_status() == "running"

        # Drop w2's underlying DBAPI socket so SQL Server tears down its SPID and session applock.
        assert w2._conn is not None
        w2._conn.connection.driver_connection.close()
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            with e1.connect() as chk:
                if (
                    chk.execute(
                        text("SELECT APPLOCK_TEST('public', :res, 'Exclusive', 'Session')"),
                        {"res": resource},
                    ).scalar()
                    == 1
                ):
                    break

        # w1 acquires fresh session + applock and takes over; w2 detects loss & stops writing.
        assert w1.run_tick().executed is True
        assert w1.health_status() == "running"
        assert w2.run_tick().executed is False
        assert w2.health_status() == "lock_unavailable"

        with engine.connect() as conn:
            ep_count = conn.execute(
                text(
                    "SELECT COUNT(*) FROM dbo.AlertEpisode "
                    "WHERE node_id = :nid AND alert_kind = 'THRESHOLD' AND is_current = 1"
                ),
                {"nid": n1_id},
            ).scalar_one()
        assert ep_count == 1
    finally:
        w1._close_connection_on_worker_thread()
        w2._drop_connection()
        e1.dispose()
        e2.dispose()
