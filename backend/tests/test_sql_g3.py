"""Opt-in SQL Server integration and concurrency tests for G3 (FL-07, FL-12, FL-13).

Requires HQ_RUN_SQL_TESTS=1 and HQ_EXPECT_TEST_DATABASE=<disposable_test_db>.
Reads ONLY HQ_AUTH_TEST_DATABASE_URL (never falls back to HQ_DATABASE_URL).
"""

import csv
import io
import os
import threading
import time
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import NodeTimeProfile, Settings
from app.core.errors import WebError
from app.modules.areas import repository as areas_repo
from app.modules.areas.schemas import AreaCreate, AreaLifecycleAction
from app.modules.areas.service import AreaService
from app.modules.auth import repository as auth_repo
from app.modules.auth.security import password_hasher
from app.modules.auth.service import AuthService
from app.modules.devices import repository as devices_repo
from app.modules.devices.schemas import GatewayCreate, NodeCreate, ProfilePatch
from app.modules.devices.service import DeviceService
from app.modules.ingestion import repository as ingestion_repo
from app.modules.ingestion.canonical import CANONICAL_VERSION, build_canonical_v1
from app.modules.ingestion.service import IngestionService
from app.modules.ingestion.validator import validate_record_static
from app.modules.measurements.dashboard_service import DashboardService
from app.modules.measurements.repository import METRIC_ORDER
from app.modules.measurements.service import MeasurementsService
from app.modules.reports.csv_builder import CSV_HEADER
from app.modules.reports.schemas import ReportCreateRequest, ReportCsvRequest
from app.modules.reports.service import ReportService

pytestmark = pytest.mark.skipif(
    os.environ.get("HQ_RUN_SQL_TESTS") != "1", reason="SQL writes require explicit opt-in"
)


class _EngineDB:
    def __init__(self, engine):
        self.engine = engine

    def sessions(self):
        return sessionmaker(bind=self.engine, expire_on_commit=False)()


def _epoch_iso(epoch_sec: int) -> str:
    return (
        datetime.fromtimestamp(epoch_sec, tz=UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _record(
    *,
    device_id: str,
    boot_id: int = 1,
    seq: int = 1,
    t_node: int | None,
    time_status: str = "VALID",
    recv_time: int,
    rssi: Decimal | None = Decimal("-82.5"),
    temp: Decimal = Decimal("28.5"),
    soil_val: Decimal | None = None,
    soil_status: str = "READ_ERROR",
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
            "soilMoisture": {"value": soil_val, "status": soil_status},
        },
    }


def _link_node(engine, *, node_id: int, gateway_id: int) -> None:
    """SQL fixture technical provisioning per G2-D04."""
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE dbo.Node SET gateway_id = :gid WHERE node_id = :nid"),
            {"gid": gateway_id, "nid": node_id},
        )


def _parse_csv_bytes(raw_bytes: bytes) -> tuple[list[str], list[list[str]]]:
    assert raw_bytes.startswith(b"\xef\xbb\xbf")
    text_body = raw_bytes.decode("utf-8-sig")
    assert "\r\n" in text_body
    rows = list(csv.reader(io.StringIO(text_body, newline=""), strict=True))
    return rows[0], rows[1:]


@pytest.fixture
def sql_g3_env():
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
            == "fee0fa015af7"
        )
        conn.rollback()

    db_now_epoch = int(db_now.replace(tzinfo=UTC).timestamp())
    tag = "testg3-" + uuid4().hex[:10]
    test_settings = base_settings.model_copy(
        update={
            "csrf_secret": base_settings.csrf_secret
            or SecretStr(f"testg3-csrf-{uuid4().hex}-padding"),
            "cursor_secret": SecretStr(f"testg3-cursor-{uuid4().hex}-padding"),
            "report_secret": SecretStr(f"testg3-report-{uuid4().hex}-padding"),
            "report_snapshot_ttl_seconds": 900,
            "node_time_profiles": {
                f"{tag}-N1": NodeTimeProfile(
                    sampling_interval_seconds=60,
                    normal_delivery_budget_seconds=60,
                ),
                f"{tag}-N2": NodeTimeProfile(
                    sampling_interval_seconds=60,
                    normal_delivery_budget_seconds=60,
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
        yield engine, test_settings, db_now_epoch, tag, cookie, dto.csrf_token, created_farm_ids
    finally:
        with SessionFactory.begin() as s:
            if created_farm_ids:
                fids = ",".join(str(int(x)) for x in created_farm_ids)
                s.execute(
                    text(
                        "UPDATE nr SET last_measurement_id = NULL, last_new_persisted_at = NULL "
                        "FROM dbo.NodeRuntime nr "
                        "JOIN dbo.Node n ON n.node_id = nr.node_id "
                        "JOIN dbo.Zone z ON z.zone_id = n.zone_id "
                        f"WHERE z.farm_id IN ({fids})"
                    )
                )
                s.execute(
                    text(
                        "DELETE mv FROM dbo.MeasurementValue mv "
                        "JOIN dbo.Measurement m ON m.measurement_id = mv.measurement_id "
                        "JOIN dbo.Node n ON n.node_id = m.node_id "
                        "JOIN dbo.Zone z ON z.zone_id = n.zone_id "
                        f"WHERE z.farm_id IN ({fids})"
                    )
                )
                s.execute(
                    text(
                        "DELETE m FROM dbo.Measurement m "
                        "JOIN dbo.Node n ON n.node_id = m.node_id "
                        "JOIN dbo.Zone z ON z.zone_id = n.zone_id "
                        f"WHERE z.farm_id IN ({fids})"
                    )
                )
                s.execute(
                    text(
                        "DELETE c FROM dbo.GatewayCredential c "
                        "JOIN dbo.Gateway g ON g.gateway_id = c.gateway_id "
                        f"WHERE g.farm_id IN ({fids})"
                    )
                )
                s.execute(
                    text(
                        "DELETE es FROM dbo.EvaluationState es "
                        "JOIN dbo.Node n ON n.node_id = es.node_id "
                        "JOIN dbo.Zone z ON z.zone_id = n.zone_id "
                        f"WHERE z.farm_id IN ({fids})"
                    )
                )
                s.execute(
                    text(
                        "DELETE nr FROM dbo.NodeRuntime nr "
                        "JOIN dbo.Node n ON n.node_id = nr.node_id "
                        "JOIN dbo.Zone z ON z.zone_id = n.zone_id "
                        f"WHERE z.farm_id IN ({fids})"
                    )
                )
                s.execute(
                    text(
                        "DELETE n FROM dbo.Node n "
                        "JOIN dbo.Zone z ON z.zone_id = n.zone_id "
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
                    "WHERE a.username LIKE 'testg3-%'"
                )
            )
            s.execute(text("DELETE FROM dbo.AdminAccount WHERE username LIKE 'testg3-%'"))
        engine.dispose()


def test_sql_g3_e2e_ingest_to_fl07_fl12_fl13_and_archived_farm(sql_g3_env):
    engine, settings, db_now_epoch, tag, cookie, csrf, created_farm_ids = sql_g3_env
    db = _EngineDB(engine)
    area_svc = AreaService(db, settings)
    dev_svc = DeviceService(db, settings)
    ing_svc = IngestionService(
        db, settings, clock=lambda: datetime.fromtimestamp(db_now_epoch + 5, tz=UTC)
    )
    meas_svc = MeasurementsService(db, settings)
    dash_svc = DashboardService(db, settings)
    rep_svc = ReportService(db, settings)

    farm1 = area_svc.create_farm(cookie, csrf, AreaCreate(code=f"{tag}-F1", name="G3 Farm 1"))
    created_farm_ids.append(int(farm1["id"]))
    zone1 = area_svc.create_zone(
        cookie, csrf, farm1["id"], AreaCreate(code=f"{tag}-Z1", name="G3 Zone 1")
    )
    gw1 = dev_svc.create(
        cookie,
        csrf,
        "gateways",
        GatewayCreate(gateway_id=f"{tag}-GW1", name="G3 GW1", farm_id=farm1["id"]),
    )
    node1 = dev_svc.create(
        cookie,
        csrf,
        "nodes",
        NodeCreate(device_id=f"{tag}-N1", name="G3 N1", zone_id=zone1["id"]),
    )
    node2 = dev_svc.create(
        cookie,
        csrf,
        "nodes",
        NodeCreate(device_id=f"{tag}-N2", name="G3 N2", zone_id=zone1["id"]),
    )
    node3 = dev_svc.create(
        cookie,
        csrf,
        "nodes",
        NodeCreate(device_id=f"{tag}-N3", name="G3 N3", zone_id=zone1["id"]),
    )
    for node_dto in (node1, node2, node3):
        _link_node(engine, node_id=int(node_dto["id"]), gateway_id=int(gw1["id"]))
    cred1 = dev_svc.issue_credential(cookie, csrf, gateway_id=gw1["id"])
    api_key = cred1["api_key"]

    farm2 = area_svc.create_farm(cookie, csrf, AreaCreate(code=f"{tag}-F2", name="G3 Farm 2"))
    created_farm_ids.append(int(farm2["id"]))
    zone2 = area_svc.create_zone(
        cookie, csrf, farm2["id"], AreaCreate(code=f"{tag}-Z2", name="G3 Zone 2")
    )
    node_other = dev_svc.create(
        cookie,
        csrf,
        "nodes",
        NodeCreate(device_id=f"{tag}-NO", name="G3 Other Node", zone_id=zone2["id"]),
    )
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE dbo.Node SET receiving_enabled = 0 WHERE node_id = :nid"),
            {"nid": int(node_other["id"])},
        )
    area_svc.archive_zone(
        cookie,
        csrf,
        zone2["id"],
        AreaLifecycleAction(expected_row_version=zone2["row_version"]),
    )
    area_svc.archive_farm(
        cookie,
        csrf,
        farm2["id"],
        AreaLifecycleAction(expected_row_version=farm2["row_version"]),
    )

    ing_res = ing_svc.ingest(
        api_key=api_key,
        payload={
            "gateway_id": gw1["gateway_id"],
            "records": [
                _record(
                    device_id=node1["device_id"],
                    seq=1,
                    t_node=db_now_epoch - 30,
                    recv_time=db_now_epoch - 5,
                    temp=Decimal("28.5"),
                    soil_val=None,
                    soil_status="READ_ERROR",
                ),
                _record(
                    device_id=node1["device_id"],
                    seq=2,
                    t_node=db_now_epoch - 1800,
                    recv_time=db_now_epoch - 5,
                    temp=Decimal("20.0"),
                    soil_val=Decimal("0"),
                    soil_status="OK",
                ),
                _record(
                    device_id=node1["device_id"],
                    seq=3,
                    t_node=None,
                    time_status="UNKNOWN",
                    recv_time=db_now_epoch - 5,
                ),
                _record(
                    device_id=node1["device_id"],
                    seq=4,
                    t_node=None,
                    time_status="UNKNOWN",
                    recv_time=db_now_epoch - 4,
                ),
                _record(
                    device_id=node2["device_id"],
                    seq=1,
                    t_node=db_now_epoch - 600,
                    recv_time=db_now_epoch - 5,
                    temp=Decimal("24.0"),
                ),
                _record(
                    device_id=node3["device_id"],
                    seq=1,
                    t_node=None,
                    time_status="UNKNOWN",
                    recv_time=db_now_epoch - 5,
                ),
            ],
        },
    )
    assert [r["status"] for r in ing_res["results"]] == ["STORED"] * 6

    # FL-07: current stays latest event_time (seq=1), not late record (seq=2)
    cur_n1 = meas_svc.get_current(cookie, node_id=node1["id"])
    assert cur_n1["state"] == "AVAILABLE"
    assert cur_n1["measurement"]["identity"]["seq"] == "1"
    assert cur_n1["measurement"]["t_node"] == str(db_now_epoch - 30)
    assert list(cur_n1["measurement"]["measurements"].keys()) == list(METRIC_ORDER)

    # FL-07: known history DESC + chart ASC sharing view_token
    from_iso = _epoch_iso(db_now_epoch - 3600)
    to_iso = _epoch_iso(db_now_epoch + 60)
    known_page = meas_svc.list_measurements(
        cookie,
        node_id=node1["id"],
        time_mode="known",
        from_raw=from_iso,
        to_raw=to_iso,
        page_size=10,
        cursor=None,
        view_token=None,
    )
    assert known_page["state"] == "AVAILABLE"
    assert [it["identity"]["seq"] for it in known_page["items"]] == ["1", "2"]
    assert list(known_page["items"][0]["measurements"].keys()) == list(METRIC_ORDER)

    chart_page = meas_svc.get_chart(
        cookie,
        node_id=node1["id"],
        from_raw=None,
        to_raw=None,
        page_size=10,
        cursor=None,
        view_token=known_page["view_token"],
    )
    assert chart_page["state"] == "AVAILABLE"
    assert [pt["identity"]["seq"] for pt in chart_page["points"]] == ["2", "1"]
    assert list(chart_page["points"][0]["measurements"].keys()) == list(METRIC_ORDER)
    assert chart_page["range"] == known_page["range"]

    # FL-07: unknown stream isolated and paginated via persisted_at keyset boundary
    unk1 = meas_svc.list_measurements(
        cookie,
        node_id=node1["id"],
        time_mode="unknown",
        from_raw=None,
        to_raw=None,
        page_size=1,
        cursor=None,
        view_token=None,
    )
    assert unk1["state"] == "AVAILABLE"
    assert len(unk1["items"]) == 1 and unk1["next_cursor"] is not None
    unk2 = meas_svc.list_measurements(
        cookie,
        node_id=node1["id"],
        time_mode="unknown",
        from_raw=None,
        to_raw=None,
        page_size=1,
        cursor=unk1["next_cursor"],
        view_token=unk1["view_token"],
    )
    assert unk2["state"] == "AVAILABLE"
    assert len(unk2["items"]) == 1 and unk2["next_cursor"] is None
    assert {it["identity"]["seq"] for it in unk1["items"] + unk2["items"]} == {"3", "4"}

    assert meas_svc.get_current(cookie, node_id=node3["id"])["state"] == "NO_EVENT_TIME"
    assert meas_svc.get_current(cookie, node_id=node_other["id"])["state"] == "NO_DATA"

    # FL-12 (G3): Farm scope, Node scope, cross-farm 400, archived Farm/Zone 200
    dash_farm1 = dash_svc.get_dashboard(cookie, farm_id=farm1["id"])
    assert dash_farm1["capabilities"] == {"monitoring": False, "alerts": False}
    assert dash_farm1["active_count"] is None
    assert dash_farm1["scope"]["kind"] == "FARM"
    assert dash_farm1["scope"]["farm"]["archived_at"] is None
    by_nid = {n["profile"]["id"]: n for n in dash_farm1["nodes"]}
    assert set(by_nid.keys()) == {node1["id"], node2["id"], node3["id"]}
    for n_view in by_nid.values():
        assert n_view["monitoring"] is None
        assert n_view["evaluation"] is None
        assert n_view["active_alerts"] is None
        assert n_view["active_count"] is None

    assert by_nid[node1["id"]]["current"]["state"] == "AVAILABLE"
    assert by_nid[node1["id"]]["freshness"]["state"] == "FRESH"
    assert by_nid[node1["id"]]["freshness"]["stale_after_seconds"] == 120
    assert list(by_nid[node1["id"]]["current"]["measurement"]["measurements"].keys()) == list(
        METRIC_ORDER
    )

    assert by_nid[node2["id"]]["current"]["state"] == "AVAILABLE"
    assert by_nid[node2["id"]]["freshness"]["state"] == "STALE"
    assert by_nid[node2["id"]]["freshness"]["stale_after_seconds"] == 120

    assert by_nid[node3["id"]]["current"]["state"] == "NO_EVENT_TIME"
    assert by_nid[node3["id"]]["freshness"]["state"] == "NO_EVENT_TIME"
    assert by_nid[node3["id"]]["freshness"]["age_seconds"] is None
    assert by_nid[node3["id"]]["freshness"]["stale_after_seconds"] is None

    dash_n1 = dash_svc.get_dashboard(cookie, farm_id=farm1["id"], node_id=node1["id"])
    assert dash_n1["scope"]["kind"] == "NODE"
    assert [n["profile"]["id"] for n in dash_n1["nodes"]] == [node1["id"]]

    with pytest.raises(WebError) as cross_err:
        dash_svc.get_dashboard(cookie, farm_id=farm1["id"], node_id=node_other["id"])
    assert cross_err.value.status == 400
    assert cross_err.value.code == "INPUT_INVALID"

    dash_archived = dash_svc.get_dashboard(cookie, farm_id=farm2["id"])
    assert dash_archived["scope"]["farm"]["archived_at"] is not None
    assert dash_archived["nodes"][0]["profile"]["zone"]["archived_at"] is not None

    # FL-13: Report excludes UNKNOWN rows, computes exact half-up stats + valid zero, matches CSV
    report = rep_svc.create_report(
        cookie,
        csrf,
        ReportCreateRequest.model_validate(
            {"node_id": node1["id"], "from": from_iso, "to": to_iso}
        ),
    )
    assert report["N_total"] == "2"
    assert [r["metric_code"] for r in report["rows"]] == list(METRIC_ORDER)
    row_by_metric = {r["metric_code"]: r for r in report["rows"]}
    assert row_by_metric["temperature"] == {
        "metric_code": "temperature",
        "unit": "°C",
        "n_valid": "2",
        "n_invalid": "0",
        "min": "20",
        "max": "28.5",
        "avg": "24.25",
    }
    assert row_by_metric["soilMoisture"] == {
        "metric_code": "soilMoisture",
        "unit": "% tương đối",
        "n_valid": "1",
        "n_invalid": "1",
        "min": "0",
        "max": "0",
        "avg": "0",
    }
    assert (
        row_by_metric["soilMoisture"]["unit"]
        == cur_n1["measurement"]["measurements"]["soilMoisture"]["unit"]
    )

    csv_bytes, filename = rep_svc.export_csv(
        cookie, csrf, ReportCsvRequest(snapshot_token=report["snapshot_token"])
    )
    assert filename.startswith(f"measurements_node_{node1['id']}_")
    header, data_rows = _parse_csv_bytes(csv_bytes)
    assert header == list(CSV_HEADER)
    assert len(data_rows) == 5
    assert [r[9] for r in data_rows] == list(METRIC_ORDER)
    for web_row, csv_row in zip(report["rows"], data_rows, strict=True):
        assert csv_row[8] == report["N_total"]
        assert csv_row[9] == web_row["metric_code"]
        assert csv_row[10] == web_row["unit"]
        assert csv_row[11] == web_row["n_valid"]
        assert csv_row[12] == web_row["n_invalid"]
        assert csv_row[13] == (web_row["min"] or "")
        assert csv_row[14] == (web_row["max"] or "")
        assert csv_row[15] == (web_row["avg"] or "")


def test_sql_g3_fl07_two_connection_cap_capture_race(sql_g3_env):
    engine, settings, db_now_epoch, tag, cookie, csrf, created_farm_ids = sql_g3_env
    db = _EngineDB(engine)
    area_svc = AreaService(db, settings)
    dev_svc = DeviceService(db, settings)
    ing_svc = IngestionService(
        db, settings, clock=lambda: datetime.fromtimestamp(db_now_epoch + 5, tz=UTC)
    )
    meas_svc = MeasurementsService(db, settings)

    farm = area_svc.create_farm(cookie, csrf, AreaCreate(code=f"{tag}-RF", name="G3 Race Farm"))
    created_farm_ids.append(int(farm["id"]))
    zone = area_svc.create_zone(
        cookie, csrf, farm["id"], AreaCreate(code=f"{tag}-RZ", name="G3 Race Zone")
    )
    gw = dev_svc.create(
        cookie,
        csrf,
        "gateways",
        GatewayCreate(gateway_id=f"{tag}-RGW", name="G3 Race GW", farm_id=farm["id"]),
    )
    node = dev_svc.create(
        cookie,
        csrf,
        "nodes",
        NodeCreate(device_id=f"{tag}-RC", name="G3 Race Node", zone_id=zone["id"]),
    )
    _link_node(engine, node_id=int(node["id"]), gateway_id=int(gw["id"]))
    cred = dev_svc.issue_credential(cookie, csrf, gateway_id=gw["id"])
    api_key = cred["api_key"]

    res_init = ing_svc.ingest(
        api_key=api_key,
        payload={
            "gateway_id": gw["gateway_id"],
            "records": [
                _record(
                    device_id=node["device_id"],
                    seq=1,
                    t_node=db_now_epoch - 300,
                    recv_time=db_now_epoch - 5,
                ),
                _record(
                    device_id=node["device_id"],
                    seq=2,
                    t_node=db_now_epoch - 200,
                    recv_time=db_now_epoch - 5,
                ),
            ],
        },
    )
    assert [r["status"] for r in res_init["results"]] == ["STORED", "STORED"]

    a_holding_lock = threading.Event()
    b_starting = threading.Event()
    a_commit_start: dict[str, float] = {}
    writer_errors: list[BaseException] = []

    def connection_a_writer() -> None:
        try:
            with engine.connect().execution_options(isolation_level="READ COMMITTED") as conn:
                with conn.begin(), Session(bind=conn) as s_a:
                    s_a.execute(text("SET XACT_ABORT ON"))
                    areas_repo.get_farm(s_a, farm["id"], lock=True)
                    areas_repo.get_zone(s_a, zone["id"], lock=True)
                    devices_repo.lock_gateway(s_a, gw["id"])
                    devices_repo.lock_node(s_a, node["id"])
                    rec_m2 = _record(
                        device_id=node["device_id"],
                        seq=3,
                        t_node=db_now_epoch - 60,
                        recv_time=db_now_epoch - 5,
                        temp=Decimal("29.0"),
                    )
                    validated, err = validate_record_static(rec_m2)
                    assert err is None and validated is not None
                    _, orig_hash = build_canonical_v1(validated)
                    backend_at = datetime.fromtimestamp(db_now_epoch + 5, tz=UTC).replace(
                        tzinfo=None
                    )
                    inserted = ingestion_repo.insert_measurement(
                        s_a,
                        node_id=int(node["id"]),
                        gateway_id=int(gw["id"]),
                        record=validated,
                        backend_received_at=backend_at,
                        canonical_version=CANONICAL_VERSION,
                        original_hash=orig_hash,
                    )
                    ingestion_repo.insert_measurement_values(
                        s_a, int(inserted["measurement_id"]), validated
                    )
                    a_holding_lock.set()
                    assert b_starting.wait(timeout=5)
                    time.sleep(0.25)
                    a_commit_start["val"] = time.monotonic()
        except BaseException as exc:  # pragma: no cover - surfaced in main thread
            writer_errors.append(exc)
            a_holding_lock.set()

    writer = threading.Thread(target=connection_a_writer)
    writer.start()

    from_iso = _epoch_iso(db_now_epoch - 600)
    to_iso = _epoch_iso(db_now_epoch + 60)
    assert a_holding_lock.wait(timeout=5)
    b_start_ts = time.monotonic()
    b_starting.set()
    page1 = meas_svc.list_measurements(
        cookie,
        node_id=node["id"],
        time_mode="known",
        from_raw=from_iso,
        to_raw=to_iso,
        page_size=1,
        cursor=None,
        view_token=None,
    )
    b_done_ts = time.monotonic()
    writer.join(timeout=10)
    assert not writer_errors

    assert b_done_ts >= a_commit_start["val"]
    assert b_done_ts - b_start_ts >= 0.20
    assert [it["identity"]["seq"] for it in page1["items"]] == ["3"]
    assert page1["next_cursor"] is not None

    # Ingest M3 strictly after cap inside [from, to] (E07-03)
    res_after = ing_svc.ingest(
        api_key=api_key,
        payload={
            "gateway_id": gw["gateway_id"],
            "records": [
                _record(
                    device_id=node["device_id"],
                    seq=4,
                    t_node=db_now_epoch - 100,
                    recv_time=db_now_epoch - 5,
                )
            ],
        },
    )
    assert res_after["results"][0]["status"] == "STORED"

    page2 = meas_svc.list_measurements(
        cookie,
        node_id=node["id"],
        time_mode="known",
        from_raw=None,
        to_raw=None,
        page_size=1,
        cursor=page1["next_cursor"],
        view_token=page1["view_token"],
    )
    assert [it["identity"]["seq"] for it in page2["items"]] == ["2"]
    assert page2["next_cursor"] is not None

    page3 = meas_svc.list_measurements(
        cookie,
        node_id=node["id"],
        time_mode="known",
        from_raw=None,
        to_raw=None,
        page_size=1,
        cursor=page2["next_cursor"],
        view_token=page1["view_token"],
    )
    assert [it["identity"]["seq"] for it in page3["items"]] == ["1"]
    assert page3["next_cursor"] is None

    chart = meas_svc.get_chart(
        cookie,
        node_id=node["id"],
        from_raw=None,
        to_raw=None,
        page_size=10,
        cursor=None,
        view_token=page1["view_token"],
    )
    assert [pt["identity"]["seq"] for pt in chart["points"]] == ["1", "2", "3"]


def test_sql_g3_fl13_snapshot_isolation_and_csv_stability(sql_g3_env):
    engine, settings, db_now_epoch, tag, cookie, csrf, created_farm_ids = sql_g3_env
    db = _EngineDB(engine)
    area_svc = AreaService(db, settings)
    dev_svc = DeviceService(db, settings)
    ing_svc = IngestionService(
        db, settings, clock=lambda: datetime.fromtimestamp(db_now_epoch + 5, tz=UTC)
    )
    rep_svc = ReportService(db, settings)

    farm = area_svc.create_farm(cookie, csrf, AreaCreate(code=f"{tag}-SF", name="G3 Snap Farm"))
    created_farm_ids.append(int(farm["id"]))
    zone = area_svc.create_zone(
        cookie, csrf, farm["id"], AreaCreate(code=f"{tag}-SZ", name="G3 Snap Zone")
    )
    gw = dev_svc.create(
        cookie,
        csrf,
        "gateways",
        GatewayCreate(gateway_id=f"{tag}-SGW", name="G3 Snap GW", farm_id=farm["id"]),
    )
    node = dev_svc.create(
        cookie,
        csrf,
        "nodes",
        NodeCreate(device_id=f"{tag}-SN", name="Initial G3 Node", zone_id=zone["id"]),
    )
    _link_node(engine, node_id=int(node["id"]), gateway_id=int(gw["id"]))
    node_rv = dev_svc.detail(cookie, "nodes", node["id"])["row_version"]
    cred = dev_svc.issue_credential(cookie, csrf, gateway_id=gw["id"])
    api_key = cred["api_key"]

    res_init = ing_svc.ingest(
        api_key=api_key,
        payload={
            "gateway_id": gw["gateway_id"],
            "records": [
                _record(
                    device_id=node["device_id"],
                    seq=1,
                    t_node=db_now_epoch - 300,
                    recv_time=db_now_epoch - 5,
                    temp=Decimal("20.0"),
                ),
                _record(
                    device_id=node["device_id"],
                    seq=2,
                    t_node=db_now_epoch - 120,
                    recv_time=db_now_epoch - 5,
                    temp=Decimal("30.0"),
                ),
            ],
        },
    )
    assert [r["status"] for r in res_init["results"]] == ["STORED", "STORED"]

    from_iso = _epoch_iso(db_now_epoch - 600)
    to_iso = _epoch_iso(db_now_epoch + 60)
    report1 = rep_svc.create_report(
        cookie,
        csrf,
        ReportCreateRequest.model_validate({"node_id": node["id"], "from": from_iso, "to": to_iso}),
    )
    assert report1["N_total"] == "2"
    assert report1["rows"][0]["avg"] == "25"

    # Mutate dataset and rename Node AFTER snapshot report1 (E13-04)
    res_new = ing_svc.ingest(
        api_key=api_key,
        payload={
            "gateway_id": gw["gateway_id"],
            "records": [
                _record(
                    device_id=node["device_id"],
                    seq=3,
                    t_node=db_now_epoch - 60,
                    recv_time=db_now_epoch - 5,
                    temp=Decimal("40.0"),
                )
            ],
        },
    )
    assert res_new["results"][0]["status"] == "STORED"
    dev_svc.patch(
        cookie,
        csrf,
        "nodes",
        node["id"],
        ProfilePatch(name="Renamed G3 Node", expected_row_version=node_rv),
    )

    csv_bytes, _ = rep_svc.export_csv(
        cookie, csrf, ReportCsvRequest(snapshot_token=report1["snapshot_token"])
    )
    _, data_rows = _parse_csv_bytes(csv_bytes)
    temp_csv = data_rows[0]
    assert temp_csv[8] == "2"
    assert temp_csv[11] == "2"
    assert temp_csv[14] == "30"
    assert temp_csv[15] == "25"

    report2 = rep_svc.create_report(
        cookie,
        csrf,
        ReportCreateRequest.model_validate({"node_id": node["id"], "from": from_iso, "to": to_iso}),
    )
    assert report2["N_total"] == "3"
    assert report2["context"]["node"]["name"] == "Renamed G3 Node"
    assert report2["rows"][0]["max"] == "40"
    assert report2["rows"][0]["avg"] == "30"


def test_sql_g3_fl13_logout_blocks_csv_export(sql_g3_env):
    engine, settings, db_now_epoch, tag, cookie, csrf, created_farm_ids = sql_g3_env
    db = _EngineDB(engine)
    area_svc = AreaService(db, settings)
    dev_svc = DeviceService(db, settings)
    rep_svc = ReportService(db, settings)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    auth_svc = AuthService(SimpleNamespace(sessions=session_factory), settings)

    farm = area_svc.create_farm(cookie, csrf, AreaCreate(code=f"{tag}-LF", name="G3 Logout Farm"))
    created_farm_ids.append(int(farm["id"]))
    zone = area_svc.create_zone(
        cookie, csrf, farm["id"], AreaCreate(code=f"{tag}-LZ", name="G3 Logout Zone")
    )
    node = dev_svc.create(
        cookie,
        csrf,
        "nodes",
        NodeCreate(device_id=f"{tag}-LN", name="G3 Logout Node", zone_id=zone["id"]),
    )

    report = rep_svc.create_report(
        cookie,
        csrf,
        ReportCreateRequest.model_validate(
            {
                "node_id": node["id"],
                "from": _epoch_iso(db_now_epoch - 600),
                "to": _epoch_iso(db_now_epoch),
            }
        ),
    )

    auth_svc.logout(cookie, csrf)

    with pytest.raises(WebError) as exc_info:
        rep_svc.export_csv(cookie, csrf, ReportCsvRequest(snapshot_token=report["snapshot_token"]))
    assert exc_info.value.status == 401
    assert exc_info.value.code == "SESSION_INVALID"
