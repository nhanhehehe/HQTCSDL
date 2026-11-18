"""Opt-in SQL Server integration and two-connection concurrency tests for G1.

Requires HQ_RUN_SQL_TESTS=1 and HQ_EXPECT_TEST_DATABASE=<disposable_test_db>.
"""

import os
import threading
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from app.core.config import Settings
from app.core.errors import WebError
from app.modules.areas.schemas import AreaCreate, AreaLifecycleAction, AreaPatch
from app.modules.areas.service import AreaService
from app.modules.auth import repository as auth_repo
from app.modules.auth.security import password_hasher
from app.modules.auth.service import AuthService
from app.modules.devices.credentials import verify_gateway_key
from app.modules.devices.schemas import GatewayCreate, NodeCreate, ProfilePatch
from app.modules.devices.service import DeviceService

pytestmark = pytest.mark.skipif(
    os.environ.get("HQ_RUN_SQL_TESTS") != "1", reason="SQL writes require explicit opt-in"
)


class _EngineDB:
    def __init__(self, engine):
        self.engine = engine

    def sessions(self):
        from sqlalchemy.orm import sessionmaker

        return sessionmaker(bind=self.engine, expire_on_commit=False)()


@pytest.fixture
def sql_g1_env():
    settings = Settings()
    secret = settings.database_url
    expected = os.environ.get("HQ_EXPECT_TEST_DATABASE")
    if secret is None or not expected:
        pytest.fail("Configure HQ_DATABASE_URL and HQ_EXPECT_TEST_DATABASE")
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

    tag = "testg1-" + uuid4().hex[:12]
    created_farm_ids: list[int] = []

    from sqlalchemy.orm import sessionmaker

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
                    "WHERE a.username LIKE 'testg1-%'"
                )
            )
            s.execute(text("DELETE FROM dbo.AdminAccount WHERE username LIKE 'testg1-%'"))
        engine.dispose()


def test_sql_g1_lifecycle_profiles_credentials_and_metrics(sql_g1_env):
    engine, settings, tag, cookie, csrf, created_farm_ids = sql_g1_env
    db = _EngineDB(engine)
    area_svc = AreaService(db, settings)
    dev_svc = DeviceService(db, settings)

    # Metrics read-only catalog
    metrics = dev_svc.list_metrics(cookie)["items"]
    assert [m["code"] for m in metrics] == [
        "temperature",
        "airHumidity",
        "pressure",
        "light",
        "soilMoisture",
    ]

    # Farm/Zone CRUD + Unicode BIN2 case-sensitivity
    f1 = area_svc.create_farm(cookie, csrf, AreaCreate(code=f"{tag}-F", name="Farm G1"))
    created_farm_ids.append(int(f1["id"]))
    f2 = area_svc.create_farm(cookie, csrf, AreaCreate(code=f"{tag}-f", name="Farm g1 lower"))
    created_farm_ids.append(int(f2["id"]))

    # No-op keeps row_version; stale raises 409 even on same value
    f1_noop = area_svc.patch_farm(
        cookie, csrf, f1["id"], AreaPatch(name="Farm G1", expected_row_version=f1["row_version"])
    )
    assert f1_noop["row_version"] == f1["row_version"]
    f1_v2 = area_svc.patch_farm(
        cookie,
        csrf,
        f1["id"],
        AreaPatch(name="Farm G1 v2", expected_row_version=f1["row_version"]),
    )
    assert f1_v2["row_version"] != f1["row_version"]
    with pytest.raises(WebError) as exc_stale:
        area_svc.patch_farm(
            cookie,
            csrf,
            f1["id"],
            AreaPatch(name="Farm G1 v2", expected_row_version=f1["row_version"]),
        )
    assert exc_stale.value.code == "VERSION_CONFLICT"

    z1 = area_svc.create_zone(cookie, csrf, f1["id"], AreaCreate(code=f"{tag}-Z", name="Zone G1"))
    gw = dev_svc.create(
        cookie,
        csrf,
        "gateways",
        GatewayCreate(gateway_id=f"{tag}-GW", name="Gateway 1", farm_id=f1["id"]),
    )
    node = dev_svc.create(
        cookie,
        csrf,
        "nodes",
        NodeCreate(device_id=f"{tag}-N", name="Node 1", zone_id=z1["id"]),
    )
    assert node["gateway"] is None

    # Profile patch works in canonical lock order
    node_v2 = dev_svc.patch(
        cookie,
        csrf,
        "nodes",
        node["id"],
        ProfilePatch(name="Node 1 Renamed", expected_row_version=node["row_version"]),
    )
    assert node_v2["name"] == "Node 1 Renamed"

    # Gateway Credentials: issue 2, 3rd blocked, verify, revoke, repeat revoke
    c1 = dev_svc.issue_credential(cookie, csrf, gateway_id=gw["id"])
    c2 = dev_svc.issue_credential(cookie, csrf, gateway_id=gw["id"])
    with pytest.raises(WebError) as exc_quota:
        dev_svc.issue_credential(cookie, csrf, gateway_id=gw["id"])
    assert exc_quota.value.code == "CREDENTIAL_LIMIT_REACHED"

    with engine.connect() as c:
        assert verify_gateway_key(c, gateway_code=gw["gateway_id"], api_key=c1["api_key"])[0]
        assert verify_gateway_key(c, gateway_code=gw["gateway_id"], api_key=c2["api_key"])[0]

    rev1 = dev_svc.revoke_credential(
        cookie, csrf, gateway_id=gw["id"], credential_id=c1["credential"]["id"]
    )
    rev1_again = dev_svc.revoke_credential(
        cookie, csrf, gateway_id=gw["id"], credential_id=c1["credential"]["id"]
    )
    assert rev1["revoked_at"] == rev1_again["revoked_at"]

    with engine.connect() as c:
        ok1, st1, code1 = verify_gateway_key(
            c, gateway_code=gw["gateway_id"], api_key=c1["api_key"]
        )
        assert (ok1, st1, code1) == (False, 403, "GATEWAY_FORBIDDEN")
        assert verify_gateway_key(c, gateway_code=gw["gateway_id"], api_key=c2["api_key"])[0]

    # Archive blocked while enabled devices exist -> 409 AREA_IN_USE
    with pytest.raises(WebError) as exc_in_use:
        area_svc.archive_zone(
            cookie, csrf, z1["id"], AreaLifecycleAction(expected_row_version=z1["row_version"])
        )
    assert exc_in_use.value.code == "AREA_IN_USE"

    # Disable devices in test fixture (simulating FL-04) -> archive/restore Farm & Zone.
    with engine.begin() as c:
        c.execute(
            text("UPDATE dbo.Node SET receiving_enabled = 0 WHERE node_id = :id"),
            {"id": int(node["id"])},
        )
        c.execute(
            text("UPDATE dbo.Gateway SET receiving_enabled = 0 WHERE gateway_id = :id"),
            {"id": int(gw["id"])},
        )

    z1_arch = area_svc.archive_zone(
        cookie, csrf, z1["id"], AreaLifecycleAction(expected_row_version=z1["row_version"])
    )
    assert z1_arch["archived_at"] is not None
    f1_arch = area_svc.archive_farm(
        cookie, csrf, f1["id"], AreaLifecycleAction(expected_row_version=f1_v2["row_version"])
    )
    assert f1_arch["archived_at"] is not None

    with pytest.raises(WebError) as exc_arch:
        area_svc.restore_zone(
            cookie,
            csrf,
            z1["id"],
            AreaLifecycleAction(expected_row_version=z1_arch["row_version"]),
        )
    assert exc_arch.value.code == "AREA_ARCHIVED"

    f1_res = area_svc.restore_farm(
        cookie, csrf, f1["id"], AreaLifecycleAction(expected_row_version=f1_arch["row_version"])
    )
    z1_res = area_svc.restore_zone(
        cookie, csrf, z1["id"], AreaLifecycleAction(expected_row_version=z1_arch["row_version"])
    )
    assert f1_res["archived_at"] is None
    assert z1_res["archived_at"] is None


def test_sql_g1_two_connection_races(sql_g1_env):
    engine, settings, tag, cookie, csrf, created_farm_ids = sql_g1_env
    db = _EngineDB(engine)
    area_svc = AreaService(db, settings)
    dev_svc = DeviceService(db, settings)

    farm = area_svc.create_farm(cookie, csrf, AreaCreate(code=f"{tag}-R", name="Race Farm"))
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

    # Race 1: Two concurrent credential issues when 1 already active -> max 2 active
    dev_svc.issue_credential(cookie, csrf, gateway_id=gw["id"])
    barrier1 = threading.Barrier(2)
    results1: list[str] = []

    def worker_issue():
        barrier1.wait()
        try:
            dev_svc.issue_credential(cookie, csrf, gateway_id=gw["id"])
            results1.append("201")
        except WebError as exc:
            results1.append(exc.code)

    t1 = threading.Thread(target=worker_issue)
    t2 = threading.Thread(target=worker_issue)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert sorted(results1) == ["201", "CREDENTIAL_LIMIT_REACHED"]

    # Race 2: Archive Zone vs Create Node in same Zone -> never active child under archived parent
    barrier2 = threading.Barrier(2)
    results2: list[str] = []

    def worker_archive():
        barrier2.wait()
        try:
            area_svc.archive_zone(
                cookie,
                csrf,
                zone["id"],
                AreaLifecycleAction(expected_row_version=zone["row_version"]),
            )
            results2.append("archived")
        except WebError as exc:
            results2.append(f"arch:{exc.code}")

    def worker_create_node():
        barrier2.wait()
        try:
            dev_svc.create(
                cookie,
                csrf,
                "nodes",
                NodeCreate(device_id=f"{tag}-RN", name="Race Node", zone_id=zone["id"]),
            )
            results2.append("created")
        except WebError as exc:
            results2.append(f"node:{exc.code}")

    t3 = threading.Thread(target=worker_archive)
    t4 = threading.Thread(target=worker_create_node)
    t3.start()
    t4.start()
    t3.join()
    t4.join()
    assert sorted(results2) in (["arch:AREA_IN_USE", "created"], ["archived", "node:AREA_ARCHIVED"])
