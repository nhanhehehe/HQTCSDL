"""Opt-in SQL Server integration tests for G4 FL-11 alert APIs and FL-12 dashboard extensions.

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
from app.core.errors import WebError
from app.modules.alerts.service import AlertsService
from app.modules.alerts.threshold_engine import evaluate_threshold_node
from app.modules.areas.schemas import AreaCreate
from app.modules.areas.service import AreaService
from app.modules.auth import repository as auth_repo
from app.modules.auth.security import password_hasher
from app.modules.auth.service import AuthService
from app.modules.devices.schemas import GatewayCreate, NodeCreate
from app.modules.devices.service import DeviceService
from app.modules.ingestion.service import IngestionService
from app.modules.measurements.dashboard_service import DashboardService
from app.modules.monitoring_config.service import MonitoringConfigService

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
def sql_g4_api_env():
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
    tag = "testapi-" + uuid4().hex[:8]
    test_settings = base_settings.model_copy(
        update={
            "csrf_secret": base_settings.csrf_secret
            or SecretStr(f"testapi-csrf-{uuid4().hex}-padding"),
            "cursor_secret": base_settings.cursor_secret
            or SecretStr(f"testapi-cursor-{uuid4().hex}-padding"),
            "node_time_profiles": {
                f"{tag}-N1": NodeTimeProfile(
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
                    "WHERE a.username LIKE :p"
                ),
                {"p": f"{tag}%"},
            )
            s.execute(text("DELETE FROM dbo.AdminAccount WHERE username LIKE :p"), {"p": f"{tag}%"})
        engine.dispose()


def test_sql_g4_alerts_dashboard_snapshot_ack_races_and_logout(sql_g4_api_env):
    engine, settings, db_now_epoch, tag, (cookie_a, csrf_a), (cookie_b, csrf_b), created_farms = (
        sql_g4_api_env
    )
    db = _EngineDB(engine)
    area_svc = AreaService(db, settings)
    dev_svc = DeviceService(db, settings)
    mc_svc = MonitoringConfigService(db, settings)
    alerts_svc = AlertsService(db, settings)
    dash_svc = DashboardService(db, settings)
    auth_svc = AuthService(
        SimpleNamespace(sessions=sessionmaker(bind=engine, expire_on_commit=False)), settings
    )

    farm = area_svc.create_farm(
        cookie_a, csrf_a, AreaCreate(code=f"{tag}-F", name="API Farm", description=None)
    )
    created_farms.append(int(farm["id"]))
    zone = area_svc.create_zone(
        cookie_a, csrf_a, farm["id"], AreaCreate(code=f"{tag}-Z", name="API Zone", description=None)
    )
    gw = dev_svc.create(
        cookie_a,
        csrf_a,
        "gateways",
        GatewayCreate(farm_id=farm["id"], gateway_id=f"{tag}-GW", name="GW"),
    )
    cred = dev_svc.issue_credential(cookie_a, csrf_a, gateway_id=gw["id"])
    n1 = dev_svc.create(
        cookie_a,
        csrf_a,
        "nodes",
        NodeCreate(zone_id=zone["id"], device_id=f"{tag}-N1", name="N1"),
    )
    _link_node(engine, node_id=int(n1["id"]), gateway_id=int(gw["id"]))

    mc_svc.patch_monitoring(
        cookie_a,
        csrf_a,
        node_id=n1["id"],
        raw_body=_json_bytes(
            {
                "expected_threshold_config_id": None,
                "thresholds": {"temperature": {"enabled": True, "lower": 18.00, "upper": 30.00}},
            }
        ),
    )

    ing = IngestionService(
        db,
        settings,
        clock=lambda: datetime.fromtimestamp(db_now_epoch + 30, tz=UTC),
    )
    ing.ingest(
        api_key=cred["api_key"],
        payload={
            "gateway_id": gw["gateway_id"],
            "records": [
                _record(
                    device_id=f"{tag}-N1",
                    seq=1,
                    t_node=db_now_epoch + 10,
                    recv_time=db_now_epoch + 10,
                    temp_val=Decimal("35.00"),
                )
            ],
        },
    )

    SessionFactory = sessionmaker(bind=engine, expire_on_commit=False)
    with SessionFactory.begin() as s:
        evaluate_threshold_node(s, node_id=int(n1["id"]))

    # Dashboard G4 snapshot consistency: active_count == "1", active_alerts has episode #1.
    dash = dash_svc.get_dashboard(cookie_a, farm_id=farm["id"])
    assert dash["capabilities"] == {"monitoring": True, "alerts": True}
    assert dash["active_count"] == "1"
    assert dash["nodes"][0]["active_count"] == "1"
    ep_id = dash["nodes"][0]["active_alerts"][0]["id"]

    # Concurrent ack <-> ack race: one returns 201, the other returns 200 idempotently.
    results: list[tuple[int, dict]] = []
    barrier = threading.Barrier(2)

    def _ack_call(c, t, note):
        barrier.wait(timeout=5)
        st, body = alerts_svc.create_acknowledgement(
            c,
            t,
            episode_id=ep_id,
            raw_body=_json_bytes(
                {"acknowledgement_version": 1, "viewed_revision_no": 1, "note": note}
            ),
        )
        results.append((st, body))

    t1 = threading.Thread(target=_ack_call, args=(cookie_a, csrf_a, "from A"))
    t2 = threading.Thread(target=_ack_call, args=(cookie_b, csrf_b, "from B"))
    t1.start()
    t2.start()
    t1.join(timeout=15)
    t2.join(timeout=15)
    assert sorted(st for st, _ in results) == [200, 201]

    # Exact lookup after lost response via GET /alerts/{id}?audit_kind=acknowledgements.
    exact = alerts_svc.get_alert_detail(
        cookie_a,
        episode_id=ep_id,
        audit_kind="acknowledgements",
        acknowledgement_version_raw="1",
    )
    assert len(exact["audit"]["items"]) == 1

    # Replay rewrite bumps acknowledgement_version 1 -> 2; acking stale version 1 returns 200
    # with real current_state.acknowledgement_version == 2.
    ing.ingest(
        api_key=cred["api_key"],
        payload={
            "gateway_id": gw["gateway_id"],
            "records": [
                _record(
                    device_id=f"{tag}-N1",
                    seq=2,
                    t_node=db_now_epoch + 5,
                    recv_time=db_now_epoch + 15,
                    temp_val=Decimal("36.00"),
                )
            ],
        },
    )
    with SessionFactory.begin() as s:
        evaluate_threshold_node(s, node_id=int(n1["id"]))

    st_old, body_old = alerts_svc.create_acknowledgement(
        cookie_a,
        csrf_a,
        episode_id=ep_id,
        raw_body=_json_bytes({"acknowledgement_version": 1, "viewed_revision_no": 1}),
    )
    assert st_old == 200
    assert body_old["current_state"]["acknowledgement_version"] == 2
    assert body_old["current_state"]["acknowledged"] is False

    # Logout session B -> ack attempt with logged-out session must return 401.
    auth_svc.logout(cookie_b, csrf_b)
    with pytest.raises(WebError) as exc_unauth:
        alerts_svc.create_acknowledgement(
            cookie_b,
            csrf_b,
            episode_id=ep_id,
            raw_body=_json_bytes({"acknowledgement_version": 2, "viewed_revision_no": 2}),
        )
    assert exc_unauth.value.status == 401

    # Corrupted projection (NULL current_revision_no) -> 503 STORAGE_UNAVAILABLE on Dashboard.
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE dbo.AlertEpisode SET current_revision_no = NULL WHERE episode_id = :eid"),
            {"eid": int(ep_id)},
        )
    with pytest.raises(WebError) as exc_corrupt:
        dash_svc.get_dashboard(cookie_a, farm_id=farm["id"])
    assert exc_corrupt.value.status == 503
    assert exc_corrupt.value.code == "STORAGE_UNAVAILABLE"
