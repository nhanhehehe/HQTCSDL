"""Opt-in SQL Server checks; schema must already be migrated by the operator.

HQ_RUN_SQL_TESTS=1 and HQ_EXPECT_TEST_DATABASE must explicitly name the disposable
test target. Inserts remain inside an outer transaction and are rolled back.
No migrations, seed, password replacement, commits or DELETE cleanup are run here.
"""

import os
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.modules.auth import repository as repo
from app.modules.auth.security import password_hasher, token_hash
from app.modules.auth.service import AuthService

pytestmark = pytest.mark.skipif(
    os.environ.get("HQ_RUN_SQL_TESTS") != "1", reason="SQL writes require explicit opt-in"
)


@pytest.fixture
def sql_fixture():
    settings = Settings()
    secret = settings.auth_test_database_url
    expected = os.environ.get("HQ_EXPECT_TEST_DATABASE")
    if secret is None or not expected:
        pytest.fail("Configure HQ_AUTH_TEST_DATABASE_URL and HQ_EXPECT_TEST_DATABASE")
    engine = create_engine(
        secret.get_secret_value(), hide_parameters=True, connect_args={"timeout": 3}
    )
    with engine.connect() as connection:
        identity = connection.execute(text("SELECT DB_NAME(), SUSER_SNAME()")).one()
        print(f"SQL test database={identity[0]}; login={identity[1]}")
        if identity[0] != expected or "test" not in identity[0].lower():
            pytest.fail("Refusing writes: expected disposable test DB does not match")
        assert connection.execute(
            text("SELECT version_num FROM dbo.alembic_version")
        ).scalar_one() == ("b15e2c03a001")
        connection.rollback()
        transaction = connection.begin()
        try:
            tag = "test-" + uuid4().hex

            def insert(sql, **params):
                return connection.execute(text(sql), params).scalar_one()

            farm = insert(
                "INSERT dbo.Farm(farm_code,name) OUTPUT inserted.farm_id VALUES(:tag,:tag)", tag=tag
            )
            zone = insert(
                "INSERT dbo.Zone(farm_id,zone_code,name) OUTPUT inserted.zone_id "
                "VALUES(:farm,:tag,:tag)",
                farm=farm,
                tag=tag,
            )
            gateway = insert(
                "INSERT dbo.Gateway(farm_id,gateway_code,name) OUTPUT inserted.gateway_id "
                "VALUES(:farm,:tag,:tag)",
                farm=farm,
                tag=tag,
            )
            node = insert(
                "INSERT dbo.Node(zone_id,gateway_id,device_code,name) OUTPUT inserted.node_id "
                "VALUES(:zone,:gateway,:tag,:tag)",
                zone=zone,
                gateway=gateway,
                tag=tag,
            )
            connection.execute(
                text("INSERT dbo.NodeRuntime(node_id) VALUES(:node)"), {"node": node}
            )
            connection.execute(
                text(
                    "INSERT dbo.EvaluationState "
                    "(node_id,desired_generation,completed_generation,full_replay_required) "
                    "VALUES(:node,0,0,0)"
                ),
                {"node": node},
            )
            factory = sessionmaker(
                bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
            )
            with factory.begin() as session:
                repo.create_admin(session, tag, password_hasher.hash("fixture-password"))
            yield connection, factory, settings, tag, node, gateway
        finally:
            transaction.rollback()
    engine.dispose()


def test_sql_auth_persistence_rollback_and_expiry(sql_fixture):
    connection, factory, settings, username, _, _ = sql_fixture
    service = AuthService(type("DB", (), {"sessions": factory})(), settings)
    dto, cookie, _ = service.login(username, "fixture-password", None)
    assert dto.admin.username == username
    digest = token_hash(cookie)
    row = connection.execute(
        text(
            "SELECT created_at, expires_at, token_hash FROM dbo.AdminSession "
            "WHERE token_hash=:digest"
        ),
        {"digest": digest},
    ).one()
    assert row.expires_at - row.created_at == timedelta(hours=2)
    assert row.token_hash == digest
    assert service.session(cookie) == dto
    service.logout(cookie, dto.csrf_token)
    first = connection.execute(
        text("SELECT revoked_at FROM dbo.AdminSession WHERE token_hash=:digest"), {"digest": digest}
    ).scalar_one()
    service.logout(cookie, None)
    assert (
        connection.execute(
            text("SELECT revoked_at FROM dbo.AdminSession WHERE token_hash=:digest"),
            {"digest": digest},
        ).scalar_one()
        == first
    )
    # Simulate a failure after rotation insert: the enclosing savepoint restores both changes.
    with pytest.raises(RuntimeError), factory.begin() as session:
        repo.create_session(session, int(dto.admin.id), b"z" * 32)
        raise RuntimeError("injected before commit")
    assert (
        connection.execute(
            text("SELECT COUNT(*) FROM dbo.AdminSession WHERE token_hash=:digest"),
            {"digest": b"z" * 32},
        ).scalar_one()
        == 0
    )


def test_sql_m2_identity_values_and_runtime(sql_fixture):
    connection, _, _, _, node, gateway = sql_fixture
    params = {"node": node, "gateway": gateway, "digest": b"m" * 32}
    measurement = connection.execute(
        text(
            "INSERT dbo.Measurement(node_id,gateway_id,boot_id,seq,t_node,time_status,recv_time,"
            "backend_received_at,persisted_at,canonical_version,original_hash) "
            "OUTPUT inserted.measurement_id VALUES(:node,:gateway,0,1,NULL,'UNKNOWN',0,"
            "SYSUTCDATETIME(),SYSUTCDATETIME(),1,:digest)"
        ),
        params,
    ).scalar_one()
    for metric, value in (
        ("temperature", Decimal("20.25")),
        ("airHumidity", Decimal("50.00")),
        ("pressure", Decimal("1000.00")),
        ("light", None),
        ("soilMoisture", None),
    ):
        connection.execute(
            text(
                "INSERT dbo.MeasurementValue(measurement_id,metric_code,value,read_status) "
                "VALUES(:measurement,:metric,:value,:status)"
            ),
            {
                "measurement": measurement,
                "metric": metric,
                "value": value,
                "status": "OK" if value is not None else "READ_ERROR",
            },
        )
    assert (
        connection.execute(
            text("SELECT COUNT(*) FROM dbo.MeasurementValue WHERE measurement_id=:measurement"),
            {"measurement": measurement},
        ).scalar_one()
        == 5
    )
    for sql, invalid in (
        (
            "UPDATE dbo.MeasurementValue SET value=NULL WHERE measurement_id=:measurement "
            "AND metric_code='temperature'",
            {"measurement": measurement},
        ),
        (
            "UPDATE dbo.MeasurementValue SET value=90 WHERE measurement_id=:measurement "
            "AND metric_code='temperature'",
            {"measurement": measurement},
        ),
        (
            "UPDATE dbo.EvaluationState SET completed_generation=1 WHERE node_id=:node",
            {"node": node},
        ),
        ("UPDATE dbo.NodeRuntime SET last_measurement_id=-1 WHERE node_id=:node", {"node": node}),
    ):
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(text(sql), invalid)
    row = connection.execute(
        text(
            "SELECT desired_generation, completed_generation, last_threshold_measurement_id, "
            "last_threshold_event_at, threshold_checkpoint_json FROM dbo.EvaluationState "
            "WHERE node_id=:node"
        ),
        {"node": node},
    ).one()
    assert tuple(row) == (0, 0, None, None, None)
