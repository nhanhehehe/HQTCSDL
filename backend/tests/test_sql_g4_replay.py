"""Opt-in SQL Server integration and concurrency tests for G4 FL-09 threshold replay.

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
from sqlalchemy.orm import sessionmaker

from app.core.config import NodeTimeProfile, Settings
from app.modules.alerts import threshold_engine
from app.modules.alerts.threshold_codec import parse_checkpoint_json
from app.modules.alerts.threshold_engine import evaluate_threshold_node
from app.modules.areas.schemas import AreaCreate
from app.modules.areas.service import AreaService
from app.modules.auth import repository as auth_repo
from app.modules.auth.security import password_hasher
from app.modules.auth.service import AuthService
from app.modules.devices.schemas import GatewayCreate, NodeCreate
from app.modules.devices.service import DeviceService
from app.modules.ingestion.service import IngestionService
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
    temp_val: Decimal = Decimal("28.50"),
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
            "light": {"value": Decimal("245.5"), "status": "OK"},
            "soilMoisture": {"value": Decimal("45.5"), "status": "OK"},
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


def _ingest_one(
    engine, settings, *, api_key: str, gateway_code: str, rec: dict, now_epoch: int
) -> dict:
    ing_svc = IngestionService(
        _EngineDB(engine),
        settings,
        clock=lambda: datetime.fromtimestamp(now_epoch, tz=UTC),
    )
    res = ing_svc.ingest(
        api_key=api_key,
        payload={
            "gateway_id": gateway_code,
            "records": [rec],
        },
    )
    assert res["results"][0]["status"] == "STORED"
    return res


@pytest.fixture
def sql_g4_replay_env():
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
    tag = "testreplay-" + uuid4().hex[:8]
    test_settings = base_settings.model_copy(
        update={
            "csrf_secret": base_settings.csrf_secret
            or SecretStr(f"testreplay-csrf-{uuid4().hex}-padding"),
            "cursor_secret": base_settings.cursor_secret
            or SecretStr(f"testreplay-cursor-{uuid4().hex}-padding"),
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


def _provision_two_nodes(engine, settings, auth, tag: str, created_farm_ids: list[int]):
    db = _EngineDB(engine)
    area_svc = AreaService(db, settings)
    dev_svc = DeviceService(db, settings)
    cookie, csrf = auth

    farm = area_svc.create_farm(
        cookie, csrf, AreaCreate(code=f"{tag}-F", name="Replay Farm", description=None)
    )
    created_farm_ids.append(int(farm["id"]))
    zone = area_svc.create_zone(
        cookie, csrf, farm["id"], AreaCreate(code=f"{tag}-Z", name="Replay Zone", description=None)
    )
    gw = dev_svc.create(
        cookie,
        csrf,
        "gateways",
        GatewayCreate(farm_id=farm["id"], gateway_id=f"{tag}-GW", name="GW"),
    )
    cred = dev_svc.issue_credential(cookie, csrf, gateway_id=gw["id"])
    n1 = dev_svc.create(
        cookie,
        csrf,
        "nodes",
        NodeCreate(zone_id=zone["id"], device_id=f"{tag}-N1", name="N1"),
    )
    n2 = dev_svc.create(
        cookie,
        csrf,
        "nodes",
        NodeCreate(zone_id=zone["id"], device_id=f"{tag}-N2", name="N2"),
    )
    _link_node(engine, node_id=int(n1["id"]), gateway_id=int(gw["id"]))
    _link_node(engine, node_id=int(n2["id"]), gateway_id=int(gw["id"]))
    return int(n1["id"]), int(n2["id"]), gw["gateway_id"], cred["api_key"]


def test_sql_replay_arrival_order_equivalence_and_disable_no_duplicate(
    sql_g4_replay_env, monkeypatch
):
    engine, settings, db_now_epoch, tag, auth, created_farm_ids = sql_g4_replay_env
    n1_id, n2_id, gw_code, api_key = _provision_two_nodes(
        engine, settings, auth, tag, created_farm_ids
    )
    mc_svc = MonitoringConfigService(_EngineDB(engine), settings)
    cookie, csrf = auth

    cfg_body = _json_bytes(
        {
            "expected_threshold_config_id": None,
            "thresholds": {"temperature": {"enabled": True, "lower": 18.00, "upper": 30.00}},
        }
    )
    from app.modules.monitoring_config import service as mc_service_mod

    real_db_utc_now = mc_service_mod.alerts_repo.get_db_utc_now
    enable_dt = datetime.fromtimestamp(db_now_epoch - 60, tz=UTC).replace(tzinfo=None)
    monkeypatch.setattr(mc_service_mod.alerts_repo, "get_db_utc_now", lambda _s: enable_dt)
    mon1 = mc_svc.patch_monitoring(cookie, csrf, node_id=str(n1_id), raw_body=cfg_body)
    mc_svc.patch_monitoring(cookie, csrf, node_id=str(n2_id), raw_body=cfg_body)
    monkeypatch.setattr(mc_service_mod.alerts_repo, "get_db_utc_now", real_db_utc_now)

    # N1 receives in-order: breach(t-30), normal(t-20), breach(t-10) before real SYSUTCDATETIME().
    t10 = db_now_epoch - 30
    t20 = db_now_epoch - 20
    t30 = db_now_epoch - 10
    clock_epoch = db_now_epoch
    _ingest_one(
        engine,
        settings,
        api_key=api_key,
        gateway_code=gw_code,
        now_epoch=clock_epoch,
        rec=_record(
            device_id=f"{tag}-N1", seq=1, t_node=t10, recv_time=t10, temp_val=Decimal("35.00")
        ),
    )
    _ingest_one(
        engine,
        settings,
        api_key=api_key,
        gateway_code=gw_code,
        now_epoch=clock_epoch,
        rec=_record(
            device_id=f"{tag}-N1", seq=2, t_node=t20, recv_time=t20, temp_val=Decimal("25.00")
        ),
    )
    _ingest_one(
        engine,
        settings,
        api_key=api_key,
        gateway_code=gw_code,
        now_epoch=clock_epoch,
        rec=_record(
            device_id=f"{tag}-N1", seq=3, t_node=t30, recv_time=t30, temp_val=Decimal("36.00")
        ),
    )

    # N2 receives out-of-order across two replay passes: first t+10 & t+30, then late t+20 (SPLIT).
    _ingest_one(
        engine,
        settings,
        api_key=api_key,
        gateway_code=gw_code,
        now_epoch=clock_epoch,
        rec=_record(
            device_id=f"{tag}-N2", seq=1, t_node=t10, recv_time=t10, temp_val=Decimal("35.00")
        ),
    )
    _ingest_one(
        engine,
        settings,
        api_key=api_key,
        gateway_code=gw_code,
        now_epoch=clock_epoch,
        rec=_record(
            device_id=f"{tag}-N2", seq=3, t_node=t30, recv_time=t30, temp_val=Decimal("36.00")
        ),
    )

    SessionFactory = sessionmaker(bind=engine, expire_on_commit=False)
    with SessionFactory.begin() as s:
        evaluate_threshold_node(s, node_id=n1_id)
    with SessionFactory.begin() as s:
        evaluate_threshold_node(s, node_id=n2_id)

    _ingest_one(
        engine,
        settings,
        api_key=api_key,
        gateway_code=gw_code,
        now_epoch=clock_epoch,
        rec=_record(
            device_id=f"{tag}-N2", seq=2, t_node=t20, recv_time=t20, temp_val=Decimal("25.00")
        ),
    )
    with SessionFactory.begin() as s:
        res_split = evaluate_threshold_node(s, node_id=n2_id)
    assert res_split.replacements_created == 2

    with engine.connect() as conn:
        proj_sql = text(
            "SELECT e.lifecycle_status, r.direction, r.start_event_time, "
            "r.end_event_time, r.end_reason "
            "FROM dbo.AlertEpisode e "
            "JOIN dbo.AlertRevision r "
            "ON r.episode_id = e.episode_id AND r.revision_no = e.current_revision_no "
            "WHERE e.node_id = :nid AND e.is_current = 1 ORDER BY r.start_event_time ASC"
        )
        n1_proj = conn.execute(proj_sql, {"nid": n1_id}).all()
        n2_proj = conn.execute(proj_sql, {"nid": n2_id}).all()
    assert n1_proj == n2_proj
    assert len(n1_proj) == 2

    # Required SQL test: OPEN episode on N1 -> disable threshold -> replay does not duplicate.
    disable_body = _json_bytes(
        {
            "expected_threshold_config_id": mon1["threshold_config_id"],
            "thresholds": {"temperature": {"enabled": False}},
        }
    )
    mc_svc.patch_monitoring(cookie, csrf, node_id=str(n1_id), raw_body=disable_body)
    with SessionFactory.begin() as s:
        res_dis = evaluate_threshold_node(s, node_id=n1_id)
    assert res_dis.episodes_updated == 0
    assert res_dis.episodes_created == 0
    assert res_dis.episodes_retired == 0
    with engine.connect() as conn:
        open_rev_count = conn.execute(
            text(
                "SELECT COUNT(*) FROM dbo.AlertRevision r "
                "JOIN dbo.AlertEpisode e ON e.episode_id = r.episode_id "
                "WHERE e.node_id = :nid AND r.end_reason = 'THRESHOLD_DISABLED'"
            ),
            {"nid": n1_id},
        ).scalar_one()
        ckpt_raw = conn.execute(
            text("SELECT threshold_checkpoint_json FROM dbo.EvaluationState WHERE node_id = :nid"),
            {"nid": n1_id},
        ).scalar_one()
    assert open_rev_count == 1
    assert parse_checkpoint_json(ckpt_raw) is not None


def test_sql_replay_fault_rollback_and_retry_idempotency(sql_g4_replay_env, monkeypatch):
    engine, settings, db_now_epoch, tag, auth, created_farm_ids = sql_g4_replay_env
    n1_id, _, gw_code, api_key = _provision_two_nodes(engine, settings, auth, tag, created_farm_ids)
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
    _ingest_one(
        engine,
        settings,
        api_key=api_key,
        gateway_code=gw_code,
        now_epoch=db_now_epoch + 20,
        rec=_record(
            device_id=f"{tag}-N1",
            seq=1,
            t_node=db_now_epoch + 10,
            recv_time=db_now_epoch + 10,
            temp_val=Decimal("35.00"),
        ),
    )

    real_update_ckpt = threshold_engine.repo.update_threshold_checkpoint

    def _boom(*_args, **_kwargs):
        raise RuntimeError("injected checkpoint fault")

    monkeypatch.setattr(threshold_engine.repo, "update_threshold_checkpoint", _boom)
    SessionFactory = sessionmaker(bind=engine, expire_on_commit=False)
    with pytest.raises(RuntimeError, match="injected checkpoint fault"):
        with SessionFactory.begin() as s:
            evaluate_threshold_node(s, node_id=n1_id)

    with engine.connect() as conn:
        assert (
            conn.execute(
                text("SELECT COUNT(*) FROM dbo.AlertEpisode WHERE node_id = :nid"), {"nid": n1_id}
            ).scalar_one()
            == 0
        )
        assert (
            conn.execute(
                text(
                    "SELECT threshold_checkpoint_json FROM dbo.EvaluationState WHERE node_id = :nid"
                ),
                {"nid": n1_id},
            ).scalar_one()
            is None
        )

    monkeypatch.setattr(threshold_engine.repo, "update_threshold_checkpoint", real_update_ckpt)
    with SessionFactory.begin() as s:
        res1 = evaluate_threshold_node(s, node_id=n1_id)
    with SessionFactory.begin() as s:
        res2 = evaluate_threshold_node(s, node_id=n1_id)

    assert res1.episodes_created == 1
    assert res2.episodes_created == 0
    assert res2.episodes_updated == 0
    assert res2.replacements_created == 0


def test_sql_replay_concurrent_with_ingestion(sql_g4_replay_env):
    engine, settings, db_now_epoch, tag, auth, created_farm_ids = sql_g4_replay_env
    n1_id, _, gw_code, api_key = _provision_two_nodes(engine, settings, auth, tag, created_farm_ids)
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

    _ingest_one(
        engine,
        settings,
        api_key=api_key,
        gateway_code=gw_code,
        now_epoch=db_now_epoch + 20,
        rec=_record(
            device_id=f"{tag}-N1",
            seq=1,
            t_node=db_now_epoch + 10,
            recv_time=db_now_epoch + 10,
            temp_val=Decimal("35.00"),
        ),
    )

    errors: list[BaseException] = []
    barrier = threading.Barrier(2)
    SessionFactory = sessionmaker(bind=engine, expire_on_commit=False)

    def _run_replay_worker():
        try:
            barrier.wait(timeout=5)
            with SessionFactory.begin() as s:
                evaluate_threshold_node(s, node_id=n1_id)
        except BaseException as exc:
            errors.append(exc)

    def _run_ingest_worker():
        try:
            barrier.wait(timeout=5)
            _ingest_one(
                engine,
                settings,
                api_key=api_key,
                gateway_code=gw_code,
                now_epoch=db_now_epoch + 30,
                rec=_record(
                    device_id=f"{tag}-N1",
                    seq=2,
                    t_node=db_now_epoch + 20,
                    recv_time=db_now_epoch + 20,
                    temp_val=Decimal("25.00"),
                ),
            )
        except BaseException as exc:
            errors.append(exc)

    t1 = threading.Thread(target=_run_replay_worker)
    t2 = threading.Thread(target=_run_ingest_worker)
    t1.start()
    t2.start()
    t1.join(timeout=15)
    t2.join(timeout=15)
    assert errors == []

    # Final replay reconciles both measurements cleanly with 1 closed episode.
    with SessionFactory.begin() as s:
        evaluate_threshold_node(s, node_id=n1_id)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT lifecycle_status FROM dbo.AlertEpisode "
                "WHERE node_id = :nid AND is_current = 1"
            ),
            {"nid": n1_id},
        ).all()
    assert len(rows) == 1 and rows[0][0] == "CLOSED"
