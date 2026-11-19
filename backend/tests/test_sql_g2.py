"""Opt-in SQL Server integration and two-connection concurrency tests for G2.

Requires HQ_RUN_SQL_TESTS=1 and HQ_EXPECT_TEST_DATABASE=<disposable_test_db>.
"""

import os
import threading
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.core.errors import IngestionError
from app.modules.areas import repository as areas_repo
from app.modules.areas.schemas import AreaCreate, AreaLifecycleAction
from app.modules.areas.service import AreaService
from app.modules.auth import repository as auth_repo
from app.modules.auth.security import password_hasher
from app.modules.auth.service import AuthService
from app.modules.devices import repository as devices_repo
from app.modules.devices.schemas import GatewayCreate, NodeCreate
from app.modules.devices.service import DeviceService
from app.modules.ingestion.service import IngestionService

pytestmark = pytest.mark.skipif(
    os.environ.get("HQ_RUN_SQL_TESTS") != "1", reason="SQL writes require explicit opt-in"
)

NOW_EPOCH = 1_794_384_000


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
    t_node: int | None = NOW_EPOCH - 10,
    time_status: str = "VALID",
    recv_time: int = NOW_EPOCH - 5,
    rssi: Decimal | None = Decimal("-82.5"),
    temp: Decimal = Decimal("28.5"),
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
            "airHumidity": {"value": Decimal("75.24"), "status": "OK"},
            "pressure": {"value": Decimal("1008.42"), "status": "OK"},
            "light": {"value": Decimal("245.5"), "status": "OK"},
            "soilMoisture": {"value": None, "status": "READ_ERROR"},
        },
    }


@pytest.fixture
def sql_g2_env():
    settings = Settings()
    secret = settings.auth_test_database_url or settings.database_url
    expected = os.environ.get("HQ_EXPECT_TEST_DATABASE")
    if secret is None or not expected:
        pytest.fail(
            "Configure HQ_AUTH_TEST_DATABASE_URL (or HQ_DATABASE_URL) and HQ_EXPECT_TEST_DATABASE"
        )
    engine = create_engine(
        secret.get_secret_value(), hide_parameters=True, connect_args={"timeout": 5}
    )
    with engine.connect() as conn:
        identity_row = conn.execute(text("SELECT DB_NAME(), SUSER_SNAME()")).one()
        if identity_row[0] != expected or "test" not in identity_row[0].lower():
            pytest.fail("Refusing writes: expected disposable test DB does not match")
        assert (
            conn.execute(text("SELECT version_num FROM dbo.alembic_version")).scalar_one()
            == "fee0fa015af7"
        )
        conn.rollback()

    tag = "testg2-" + uuid4().hex[:10]
    created_farm_ids: list[int] = []
    SessionFactory = sessionmaker(bind=engine, expire_on_commit=False)

    with SessionFactory.begin() as s:
        auth_repo.create_admin(s, tag, password_hasher.hash("fixture-password"))
    auth_svc = AuthService(SimpleNamespace(sessions=SessionFactory), settings)
    dto, cookie, _ = auth_svc.login(tag, "fixture-password", None)

    try:
        yield engine, settings, tag, cookie, dto.csrf_token, created_farm_ids
    finally:
        with SessionFactory.begin() as s:
            if created_farm_ids:
                fids = ",".join(str(int(x)) for x in created_farm_ids)
                # Clear NodeRuntime pointer to Measurement before deleting Measurements.
                s.execute(
                    text(
                        f"UPDATE nr SET last_measurement_id = NULL, last_new_persisted_at = NULL "
                        f"FROM dbo.NodeRuntime nr "
                        f"JOIN dbo.Node n ON n.node_id = nr.node_id "
                        f"JOIN dbo.Zone z ON z.zone_id = n.zone_id "
                        f"WHERE z.farm_id IN ({fids})"
                    )
                )
                s.execute(
                    text(
                        f"DELETE mv FROM dbo.MeasurementValue mv "
                        f"JOIN dbo.Measurement m ON m.measurement_id = mv.measurement_id "
                        f"JOIN dbo.Node n ON n.node_id = m.node_id "
                        f"JOIN dbo.Zone z ON z.zone_id = n.zone_id "
                        f"WHERE z.farm_id IN ({fids})"
                    )
                )
                s.execute(
                    text(
                        f"DELETE m FROM dbo.Measurement m "
                        f"JOIN dbo.Node n ON n.node_id = m.node_id "
                        f"JOIN dbo.Zone z ON z.zone_id = n.zone_id "
                        f"WHERE z.farm_id IN ({fids})"
                    )
                )
                s.execute(
                    text(
                        f"DELETE c FROM dbo.GatewayCredential c "
                        f"JOIN dbo.Gateway g ON g.gateway_id = c.gateway_id "
                        f"WHERE g.farm_id IN ({fids})"
                    )
                )
                s.execute(
                    text(
                        f"DELETE es FROM dbo.EvaluationState es "
                        f"JOIN dbo.Node n ON n.node_id = es.node_id "
                        f"JOIN dbo.Zone z ON z.zone_id = n.zone_id "
                        f"WHERE z.farm_id IN ({fids})"
                    )
                )
                s.execute(
                    text(
                        f"DELETE nr FROM dbo.NodeRuntime nr "
                        f"JOIN dbo.Node n ON n.node_id = nr.node_id "
                        f"JOIN dbo.Zone z ON z.zone_id = n.zone_id "
                        f"WHERE z.farm_id IN ({fids})"
                    )
                )
                s.execute(
                    text(
                        f"DELETE n FROM dbo.Node n "
                        f"JOIN dbo.Zone z ON z.zone_id = n.zone_id "
                        f"WHERE z.farm_id IN ({fids})"
                    )
                )
                s.execute(text(f"DELETE FROM dbo.Gateway WHERE farm_id IN ({fids})"))
                s.execute(text(f"DELETE FROM dbo.Zone WHERE farm_id IN ({fids})"))
                s.execute(text(f"DELETE FROM dbo.Farm WHERE farm_id IN ({fids})"))
            s.execute(
                text(
                    "DELETE s FROM dbo.AdminSession s "
                    "JOIN dbo.AdminAccount a ON a.admin_id = s.admin_id "
                    "WHERE a.username LIKE 'testg2-%'"
                )
            )
            s.execute(text("DELETE FROM dbo.AdminAccount WHERE username LIKE 'testg2-%'"))
        engine.dispose()


def _link_node(engine, *, node_id: int, gateway_id: int) -> None:
    """SQL fixture technical provisioning per G2-D04."""
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE dbo.Node SET gateway_id = :gid WHERE node_id = :nid"),
            {"gid": gateway_id, "nid": node_id},
        )


def test_sql_g2_happy_path_metadata_runtime_rollback_and_cross_farm(sql_g2_env):
    engine, settings, tag, cookie, csrf, created_farm_ids = sql_g2_env
    db = _EngineDB(engine)
    area_svc = AreaService(db, settings)
    dev_svc = DeviceService(db, settings)
    ing_svc = IngestionService(
        db, settings, clock=lambda: datetime.fromtimestamp(NOW_EPOCH, tz=UTC)
    )

    farm1 = area_svc.create_farm(cookie, csrf, AreaCreate(code=f"{tag}-F1", name="G2 Farm 1"))
    created_farm_ids.append(int(farm1["id"]))
    zone1 = area_svc.create_zone(
        cookie, csrf, farm1["id"], AreaCreate(code=f"{tag}-Z1", name="G2 Zone 1")
    )
    gw1 = dev_svc.create(
        cookie,
        csrf,
        "gateways",
        GatewayCreate(gateway_id=f"{tag}-GW1", name="G2 GW1", farm_id=farm1["id"]),
    )
    node1 = dev_svc.create(
        cookie,
        csrf,
        "nodes",
        NodeCreate(device_id=f"{tag}-N1", name="G2 N1", zone_id=zone1["id"]),
    )
    _link_node(engine, node_id=int(node1["id"]), gateway_id=int(gw1["id"]))
    node1_rv_before = dev_svc.detail(cookie, "nodes", node1["id"])["row_version"]
    gw1_rv_before = dev_svc.detail(cookie, "gateways", gw1["id"])["row_version"]

    cred1 = dev_svc.issue_credential(cookie, csrf, gateway_id=gw1["id"])
    api_key = cred1["api_key"]

    # 1) New VALID, late VALID, UNKNOWN/null, and duplicate with different recv_time/rssi
    res = ing_svc.ingest(
        api_key=api_key,
        payload={
            "gateway_id": gw1["gateway_id"],
            "records": [
                _record(device_id=node1["device_id"], seq=1, t_node=NOW_EPOCH - 10),
                _record(device_id=node1["device_id"], seq=2, t_node=NOW_EPOCH - 300),
                _record(device_id=node1["device_id"], seq=3, t_node=None, time_status="UNKNOWN"),
                _record(
                    device_id=node1["device_id"],
                    seq=1,
                    t_node=NOW_EPOCH - 10,
                    recv_time=NOW_EPOCH,
                    rssi=Decimal("-60.0"),
                ),
            ],
        },
    )
    assert [r["status"] for r in res["results"]] == [
        "STORED",
        "STORED",
        "STORED",
        "ALREADY_EXISTS",
    ]

    with engine.connect() as conn:
        m_count = conn.execute(
            text("SELECT COUNT_BIG(*) FROM dbo.Measurement WHERE node_id = :nid"),
            {"nid": int(node1["id"])},
        ).scalar_one()
        v_count = conn.execute(
            text(
                "SELECT COUNT_BIG(*) FROM dbo.MeasurementValue mv "
                "JOIN dbo.Measurement m ON m.measurement_id = mv.measurement_id "
                "WHERE m.node_id = :nid"
            ),
            {"nid": int(node1["id"])},
        ).scalar_one()
        assert m_count == 3 and v_count == 15

        ev = (
            conn.execute(
                text(
                    "SELECT desired_generation, completed_generation, full_replay_required, "
                    "replay_from_t_node FROM dbo.EvaluationState WHERE node_id = :nid"
                ),
                {"nid": int(node1["id"])},
            )
            .mappings()
            .one()
        )
        assert ev["desired_generation"] == 3
        assert ev["completed_generation"] == 0
        assert bool(ev["full_replay_required"]) is True
        assert ev["replay_from_t_node"] == NOW_EPOCH - 300

    # F13-AC07: G1 profile details reflect last_new_persisted_at; row_ver unchanged
    node1_after = dev_svc.detail(cookie, "nodes", node1["id"])
    gw1_after = dev_svc.detail(cookie, "gateways", gw1["id"])
    assert node1_after["last_new_persisted_at"] is not None
    assert gw1_after["last_new_persisted_at"] is not None
    assert node1_after["row_version"] == node1_rv_before
    assert gw1_after["row_version"] == gw1_rv_before

    # Duplicate retry must NOT change last_new_persisted_at or desired_generation
    res_dup = ing_svc.ingest(
        api_key=api_key,
        payload={
            "gateway_id": gw1["gateway_id"],
            "records": [_record(device_id=node1["device_id"], seq=1, t_node=NOW_EPOCH - 10)],
        },
    )
    assert res_dup["results"][0]["status"] == "ALREADY_EXISTS"
    assert (
        dev_svc.detail(cookie, "nodes", node1["id"])["last_new_persisted_at"]
        == node1_after["last_new_persisted_at"]
    )

    # 2) E06-01: missing EvaluationState rolls back Measurement + 5 Values atomically
    node_fault = dev_svc.create(
        cookie,
        csrf,
        "nodes",
        NodeCreate(device_id=f"{tag}-NF", name="G2 Fault Node", zone_id=zone1["id"]),
    )
    _link_node(engine, node_id=int(node_fault["id"]), gateway_id=int(gw1["id"]))
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM dbo.EvaluationState WHERE node_id = :nid"),
            {"nid": int(node_fault["id"])},
        )
    res_fault = ing_svc.ingest(
        api_key=api_key,
        payload={
            "gateway_id": gw1["gateway_id"],
            "records": [_record(device_id=node_fault["device_id"], seq=1)],
        },
    )
    assert res_fault["results"][0]["status"] == "RETRY_LATER"
    assert res_fault["results"][0]["error"]["code"] == "STORAGE_UNAVAILABLE"
    with engine.connect() as conn:
        assert (
            conn.execute(
                text("SELECT COUNT_BIG(*) FROM dbo.Measurement WHERE node_id = :nid"),
                {"nid": int(node_fault["id"])},
            ).scalar_one()
            == 0
        )

    # 3) F03-AC11: cross-farm Node (Farm 2 Zone linked to Farm 1 GW) -> CONFIGURATION_INVALID
    farm2 = area_svc.create_farm(cookie, csrf, AreaCreate(code=f"{tag}-F2", name="G2 Farm 2"))
    created_farm_ids.append(int(farm2["id"]))
    zone2 = area_svc.create_zone(
        cookie, csrf, farm2["id"], AreaCreate(code=f"{tag}-Z2", name="G2 Zone 2")
    )
    node_cross = dev_svc.create(
        cookie,
        csrf,
        "nodes",
        NodeCreate(device_id=f"{tag}-NX", name="G2 Cross Node", zone_id=zone2["id"]),
    )
    _link_node(engine, node_id=int(node_cross["id"]), gateway_id=int(gw1["id"]))
    res_cross = ing_svc.ingest(
        api_key=api_key,
        payload={
            "gateway_id": gw1["gateway_id"],
            "records": [_record(device_id=node_cross["device_id"], seq=1)],
        },
    )
    assert res_cross["results"][0]["status"] == "RETRY_LATER"
    assert res_cross["results"][0]["error"]["code"] == "CONFIGURATION_INVALID"


def test_sql_g2_two_connection_races(sql_g2_env):
    engine, settings, tag, cookie, csrf, created_farm_ids = sql_g2_env
    db = _EngineDB(engine)
    area_svc = AreaService(db, settings)
    dev_svc = DeviceService(db, settings)
    ing_svc = IngestionService(
        db, settings, clock=lambda: datetime.fromtimestamp(NOW_EPOCH, tz=UTC)
    )

    farm = area_svc.create_farm(cookie, csrf, AreaCreate(code=f"{tag}-RF", name="Race Farm"))
    created_farm_ids.append(int(farm["id"]))
    zone = area_svc.create_zone(
        cookie, csrf, farm["id"], AreaCreate(code=f"{tag}-RZ", name="Race Zone")
    )
    gw = dev_svc.create(
        cookie,
        csrf,
        "gateways",
        GatewayCreate(gateway_id=f"{tag}-RGW", name="Race GW", farm_id=farm["id"]),
    )
    node = dev_svc.create(
        cookie,
        csrf,
        "nodes",
        NodeCreate(device_id=f"{tag}-RN", name="Race Node", zone_id=zone["id"]),
    )
    _link_node(engine, node_id=int(node["id"]), gateway_id=int(gw["id"]))
    cred_a = dev_svc.issue_credential(cookie, csrf, gateway_id=gw["id"])
    cred_b = dev_svc.issue_credential(cookie, csrf, gateway_id=gw["id"])

    # Race 1: Concurrent duplicate on same identity (DB-T02, F19-AC10)
    b1 = threading.Barrier(2)
    r1: list[str] = []

    def send_dup():
        b1.wait()
        out = ing_svc.ingest(
            api_key=cred_a["api_key"],
            payload={
                "gateway_id": gw["gateway_id"],
                "records": [_record(device_id=node["device_id"], boot_id=10, seq=1)],
            },
        )
        r1.append(out["results"][0]["status"])

    t1, t2 = threading.Thread(target=send_dup), threading.Thread(target=send_dup)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert sorted(r1) == ["ALREADY_EXISTS", "STORED"]

    # Race 2: Concurrent conflict on same identity with different temp (F19-AC11)
    b2 = threading.Barrier(2)
    r2: list[str] = []

    def send_conf(temp_val: Decimal):
        b2.wait()
        out = ing_svc.ingest(
            api_key=cred_a["api_key"],
            payload={
                "gateway_id": gw["gateway_id"],
                "records": [_record(device_id=node["device_id"], boot_id=10, seq=2, temp=temp_val)],
            },
        )
        item = out["results"][0]
        r2.append(item["error"]["code"] if item["status"] == "REJECTED" else item["status"])

    t3 = threading.Thread(target=send_conf, args=(Decimal("25.0"),))
    t4 = threading.Thread(target=send_conf, args=(Decimal("30.0"),))
    t3.start()
    t4.start()
    t3.join()
    t4.join()
    assert sorted(r2) == ["RECORD_CONFLICT", "STORED"]

    # Race 3: Key rotation & revoke cred_a vs ingestion batch (F13-KAC03/06, E06-03)
    b3 = threading.Barrier(2)
    r3: list[str] = []

    def send_batch():
        b3.wait()
        try:
            out = ing_svc.ingest(
                api_key=cred_a["api_key"],
                payload={
                    "gateway_id": gw["gateway_id"],
                    "records": [
                        _record(device_id=node["device_id"], boot_id=20, seq=i) for i in range(1, 6)
                    ],
                },
            )
            r3.append("200:" + str(len(out["results"])))
        except IngestionError as exc:
            r3.append(f"{exc.status}:{exc.code}")

    def revoke_a():
        b3.wait()
        dev_svc.revoke_credential(
            cookie, csrf, gateway_id=gw["id"], credential_id=cred_a["credential"]["id"]
        )
        r3.append("revoked")

    t5, t6 = threading.Thread(target=send_batch), threading.Thread(target=revoke_a)
    t5.start()
    t6.start()
    t5.join()
    t6.join()
    assert "revoked" in r3
    assert any(x in ("200:5", "403:GATEWAY_FORBIDDEN") for x in r3)
    # Re-sending full batch with cred_b succeeds cleanly (ALREADY_EXISTS + STORED)
    retry_b = ing_svc.ingest(
        api_key=cred_b["api_key"],
        payload={
            "gateway_id": gw["gateway_id"],
            "records": [
                _record(device_id=node["device_id"], boot_id=20, seq=i) for i in range(1, 6)
            ],
        },
    )
    assert all(x["ack"] is True for x in retry_b["results"])

    # Race 4: Disable Node receiving_enabled (canonical lock order) vs new record (F19-AC13/14)
    b4 = threading.Barrier(2)
    r4: list[str] = []

    def disable_node():
        b4.wait()
        with engine.connect().execution_options(isolation_level="READ COMMITTED") as conn:
            with conn.begin():
                conn.execute(text("SET XACT_ABORT ON"))
                areas_repo.get_farm(conn, farm["id"], lock=True)
                areas_repo.get_zone(conn, zone["id"], lock=True)
                devices_repo.lock_gateway(conn, gw["id"])
                devices_repo.lock_node(conn, node["id"])
                conn.execute(
                    text("UPDATE dbo.Node SET receiving_enabled = 0 WHERE node_id = :nid"),
                    {"nid": int(node["id"])},
                )
        r4.append("disabled")

    def ingest_new_during_disable():
        b4.wait()
        out = ing_svc.ingest(
            api_key=cred_b["api_key"],
            payload={
                "gateway_id": gw["gateway_id"],
                "records": [_record(device_id=node["device_id"], boot_id=30, seq=1)],
            },
        )
        item = out["results"][0]
        r4.append(item["error"]["code"] if item["status"] != "STORED" else "STORED")

    t7 = threading.Thread(target=disable_node)
    t8 = threading.Thread(target=ingest_new_during_disable)
    t7.start()
    t8.start()
    t7.join()
    t8.join()
    assert sorted(r4) in (["STORED", "disabled"], ["RECEIVING_DISABLED", "disabled"])

    # Duplicate already stored MUST still return ALREADY_EXISTS when Node is disabled (F19-AC14)
    dup_disabled = ing_svc.ingest(
        api_key=cred_b["api_key"],
        payload={
            "gateway_id": gw["gateway_id"],
            "records": [_record(device_id=node["device_id"], boot_id=10, seq=1)],
        },
    )
    assert dup_disabled["results"][0]["status"] == "ALREADY_EXISTS"

    # Race 5: Archive Zone (Node disabled) vs ingestion (new -> config/perm, dup -> ALREADY_EXISTS)
    z_current = area_svc.get_zone(cookie, zone["id"])
    b5 = threading.Barrier(2)
    r5: list[str] = []

    def archive_z():
        b5.wait()
        area_svc.archive_zone(
            cookie,
            csrf,
            zone["id"],
            AreaLifecycleAction(expected_row_version=z_current["row_version"]),
        )
        r5.append("archived")

    def ingest_during_archive():
        b5.wait()
        out = ing_svc.ingest(
            api_key=cred_b["api_key"],
            payload={
                "gateway_id": gw["gateway_id"],
                "records": [
                    _record(device_id=node["device_id"], boot_id=10, seq=1),  # duplicate
                    _record(device_id=node["device_id"], boot_id=40, seq=1),  # new
                ],
            },
        )
        r5.append(f"{out['results'][0]['status']}|{out['results'][1]['error']['code']}")

    t9, t10 = threading.Thread(target=archive_z), threading.Thread(target=ingest_during_archive)
    t9.start()
    t10.start()
    t9.join()
    t10.join()
    assert "archived" in r5
    assert any(
        x in ("ALREADY_EXISTS|RECEIVING_DISABLED", "ALREADY_EXISTS|CONFIGURATION_INVALID")
        for x in r5
    )
