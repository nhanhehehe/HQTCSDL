import importlib.util
import re
from io import StringIO
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations


def render_m2(direction):
    path = Path(__file__).parents[1] / "migrations/versions/b15e2c03a001_create_m2_tables.py"
    spec = importlib.util.spec_from_file_location("m2_revision", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="mssql", opts={"as_sql": True, "output_buffer": output}
    )
    with Operations.context(context):
        getattr(migration, direction)()
    return output.getvalue()


def test_m2_sql_server_ddl_compiles_and_preserves_m1():
    sql = render_m2("upgrade")
    assert sql.count("CREATE TABLE") == 7
    assert "NVARCHAR(max)" in sql
    assert "FLOAT" not in sql and "NTEXT" not in sql
    assert "CREATE UNIQUE INDEX [UX_NodeConfigVersion_current]" in sql
    assert "last_threshold_measurement_id" in sql
    assert "last_threshold_event_at" in sql
    assert "threshold_checkpoint_json" in sql
    assert "NO ACTION" in sql
    assert "AlertEpisode" not in sql
    assert "DROP TABLE" not in sql
    assert "UPDATE dbo.Node" not in sql
    assert "INSERT INTO dbo.NodeRuntime" in sql


def test_m2_downgrade_removes_dependents_before_targets_only():
    sql = render_m2("downgrade")
    assert sql.count("DROP TABLE") == 7
    assert sql.index("[NodeRuntime]") < sql.index("[MonitoringPeriod]")
    assert sql.index("[MonitoringPeriod]") < sql.index("[NodeConfigVersion]")
    assert "DROP TABLE dbo.[Node];" not in sql
    assert "AdminAccount" not in sql


@pytest.mark.parametrize(
    ("table", "column", "generated"),
    [
        ("NodeRuntime", "node_id", False),
        ("EvaluationState", "node_id", False),
        ("Measurement", "measurement_id", True),
        ("NodeConfigVersion", "config_id", True),
        ("MonitoringPeriod", "period_id", True),
    ],
)
def test_m2_identity_is_only_generated_for_owned_keys(table, column, generated):
    sql = render_m2("upgrade")
    table_ddl = sql.split(f"CREATE TABLE dbo.[{table}] (", 1)[1].split("\n);", 1)[0]
    declaration = re.search(rf"^\s*{column}\s+BIGINT\b[^\n]*", table_ddl, re.MULTILINE)
    assert declaration is not None
    assert bool(re.search(r"\bIDENTITY\b", declaration.group())) is generated
