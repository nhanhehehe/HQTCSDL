import re
from io import StringIO
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory

MIGRATIONS = Path(__file__).parents[1] / "migrations"
REVISION = "d6b3a5f09c42"
PARENT = "fee0fa015af7"
TABLES = ("AlertEpisode", "AlertRevision", "AlertAcknowledgement", "AlertReplacement")


def render_migrations(direction, *, from_base=False):
    script = ScriptDirectory(str(MIGRATIONS))
    if from_base:
        revisions = list(reversed(list(script.iterate_revisions(REVISION, "base"))))
    else:
        revisions = [script.get_revision(REVISION)]
    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="mssql", opts={"as_sql": True, "output_buffer": output}
    )
    with Operations.context(context):
        for revision in revisions:
            getattr(revision.module, direction)()
    return (
        re.sub(r"^\s*GO\s*$", "", output.getvalue(), flags=re.MULTILINE)
        .replace("[", "")
        .replace("]", "")
    )


def test_m3_is_the_single_head_after_pr_a_and_bootstraps_19_tables():
    script = ScriptDirectory(str(MIGRATIONS))
    assert script.get_heads() == [REVISION]
    assert script.get_revision(REVISION).down_revision == PARENT
    sql = render_migrations("upgrade", from_base=True)
    assert len(set(re.findall(r"CREATE TABLE dbo\.(\w+)", sql))) == 19
    assert sql.count("CREATE TABLE") == 19
    assert sql.index("CREATE TABLE dbo.EvaluationState") < sql.index(
        "ALTER TABLE dbo.EvaluationState ADD timeout_last_error_code"
    )


def test_upgrade_only_adds_four_tables_and_two_nullable_timeout_fields():
    sql = render_migrations("upgrade")
    assert tuple(re.findall(r"CREATE TABLE dbo\.(\w+)", sql)) == TABLES
    assert re.findall(r"ALTER TABLE dbo\.EvaluationState ADD ([^;]+)", sql) == [
        "timeout_last_error_code VARCHAR(64) COLLATE Latin1_General_100_BIN2 NULL",
        "timeout_retry_after DATETIME2(3) NULL",
    ]
    assert not re.search(r"^\s*(INSERT|UPDATE|DELETE|DROP)\b", sql, re.MULTILINE)
    assert "checkpoint" not in sql
    assert re.search(r"episode_id BIGINT NOT NULL IDENTITY\s*\(1,1\)", sql)
    assert sql.count("IDENTITY") == 1
    assert "threshold_metadata_json NVARCHAR(max) NULL" in sql
    assert "ISJSON(threshold_metadata_json) = 1" in sql


def test_upgrade_constrains_current_episode_and_revision_audit():
    sql = render_migrations("upgrade")
    assert "current_revision_no INTEGER NULL" in sql
    assert "PRIMARY KEY (episode_id, revision_no)" in sql
    assert "PRIMARY KEY (episode_id, acknowledgement_version)" in sql
    assert "PRIMARY KEY (old_episode_id, old_revision_no, new_episode_id, new_revision_no)" in sql
    assert "old_episode_id <> new_episode_id OR old_revision_no < new_revision_no" in sql
    assert sql.index("CREATE TABLE dbo.AlertRevision") < sql.index(
        "ADD CONSTRAINT FK_AlertEpisode_current_revision"
    )
    for columns in (
        "episode_id, current_revision_no",
        "episode_id, acknowledged_revision_no",
        "old_episode_id, old_revision_no",
        "new_episode_id, new_revision_no",
    ):
        assert (
            f"FOREIGN KEY({columns}) REFERENCES dbo.AlertRevision (episode_id, revision_no) "
            "ON DELETE NO ACTION" in sql
        )
    for column in ("start_measurement_id", "end_measurement_id"):
        assert f"FOREIGN KEY({column}) REFERENCES dbo.Measurement (measurement_id)" in sql
    assert sql.count("FOREIGN KEY") == sql.count("ON DELETE NO ACTION") == 13
    assert "alert_kind = 'TIMEOUT' AND metric_code IS NULL" in sql
    assert "alert_kind = 'THRESHOLD' AND metric_code IS NOT NULL" in sql
    for kind, keys in (("threshold", "node_id, metric_code"), ("timeout", "node_id")):
        assert (
            f"CREATE UNIQUE INDEX UX_AlertEpisode_open_{kind} ON dbo.AlertEpisode ({keys}) "
            f"WHERE is_current = 1 AND lifecycle_status = 'OPEN' AND alert_kind = '{kind.upper()}'"
        ) in sql


def test_downgrade_breaks_fk_cycle_and_only_removes_m3_objects():
    statements = [statement.strip() for statement in render_migrations("downgrade").split(";")]
    assert [statement for statement in statements if statement] == [
        "ALTER TABLE dbo.AlertEpisode DROP CONSTRAINT FK_AlertEpisode_current_revision",
        "DROP TABLE dbo.AlertReplacement",
        "DROP TABLE dbo.AlertAcknowledgement",
        "DROP TABLE dbo.AlertRevision",
        "DROP TABLE dbo.AlertEpisode",
        "ALTER TABLE dbo.EvaluationState DROP COLUMN timeout_retry_after",
        "ALTER TABLE dbo.EvaluationState DROP COLUMN timeout_last_error_code",
    ]
