import importlib.util
import re
from io import StringIO
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory

MIGRATIONS = Path(__file__).parents[1] / "migrations"
REVISION = "fee0fa015af7"
PARENT = "b15e2c03a001"


def load_revision():
    path = MIGRATIONS / "versions" / f"{REVISION}_add_farm_zone_lifecycle_columns.py"
    spec = importlib.util.spec_from_file_location("farm_zone_revision", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def render_revision(direction):
    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="mssql", opts={"as_sql": True, "output_buffer": output}
    )
    with Operations.context(context):
        getattr(load_revision(), direction)()
    sql = output.getvalue().replace("[", "").replace("]", "")
    # MSSQL batch separators and identifier quoting are not part of the contract.
    sql = re.sub(r"^\s*GO\s*$", "", sql, flags=re.MULTILINE)
    return [" ".join(statement.split()) for statement in sql.split(";") if statement.strip()]


@pytest.mark.parametrize("table", ["Farm", "Zone"])
def test_upgrade_declares_nullable_archive_and_server_managed_rowversion(table):
    statements = render_revision("upgrade")
    assert f"ALTER TABLE dbo.{table} ADD archived_at DATETIME2(3) NULL" in statements
    assert f"ALTER TABLE dbo.{table} ADD row_ver ROWVERSION NOT NULL" in statements


def test_upgrade_only_adds_four_columns_without_data_or_constraint_changes():
    statements = render_revision("upgrade")
    assert len(statements) == 4
    assert set(statements) == {
        f"ALTER TABLE dbo.{table} ADD {declaration}"
        for table in ("Farm", "Zone")
        for declaration in ("archived_at DATETIME2(3) NULL", "row_ver ROWVERSION NOT NULL")
    }


def test_downgrade_only_drops_new_columns_in_reverse_order():
    assert render_revision("downgrade") == [
        "ALTER TABLE dbo.Zone DROP COLUMN row_ver",
        "ALTER TABLE dbo.Zone DROP COLUMN archived_at",
        "ALTER TABLE dbo.Farm DROP COLUMN row_ver",
        "ALTER TABLE dbo.Farm DROP COLUMN archived_at",
    ]


def test_pr_a_preserves_foundation_chain():
    script = ScriptDirectory(str(MIGRATIONS))
    assert script.get_revision(REVISION).down_revision == PARENT
    assert script.get_revision(PARENT).down_revision == "a70b8056608d"
    assert script.get_revision("a70b8056608d").down_revision is None
