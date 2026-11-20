"""Create M3 alert episodes and audit tables.

Revision ID: d6b3a5f09c42
Revises: fee0fa015af7
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mssql

revision = "d6b3a5f09c42"
down_revision = "fee0fa015af7"
branch_labels = None
depends_on = None

COLLATION = "Latin1_General_100_BIN2"
METRICS = "'temperature','airHumidity','pressure','light','soilMoisture'"
CURRENT_OPEN = "is_current = 1 AND lifecycle_status = 'OPEN'"


def bigint(name, nullable=False):
    return sa.Column(name, sa.BigInteger(), nullable=nullable, autoincrement=False)


def code(name, size, nullable=False):
    return sa.Column(name, sa.VARCHAR(size, collation=COLLATION), nullable=nullable)


def instant(name, nullable=False):
    return sa.Column(name, mssql.DATETIME2(precision=3), nullable=nullable)


def fk(table, columns, parent, targets, suffix=None):
    op.create_foreign_key(
        f"FK_{table}_{suffix or parent}",
        table,
        parent,
        columns,
        targets,
        source_schema="dbo",
        referent_schema="dbo",
        ondelete="NO ACTION",
    )


def upgrade() -> None:
    # M2 already owns the threshold checkpoint and threshold error/retry fields.
    op.add_column("EvaluationState", code("timeout_last_error_code", 64, True), schema="dbo")
    op.add_column("EvaluationState", instant("timeout_retry_after", True), schema="dbo")

    op.create_table(
        "AlertEpisode",
        sa.Column("episode_id", sa.BigInteger(), sa.Identity(start=1, increment=1), nullable=False),
        bigint("node_id"),
        code("alert_kind", 12),
        code("metric_code", 20, True),
        code("lifecycle_status", 12),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("current_revision_no", sa.Integer(), nullable=True),
        instant("first_detected_at"),
        sa.Column("acknowledgement_version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("episode_id", name="PK_AlertEpisode"),
        sa.CheckConstraint(
            "(alert_kind = 'TIMEOUT' AND metric_code IS NULL) OR "
            "(alert_kind = 'THRESHOLD' AND metric_code IS NOT NULL "
            f"AND metric_code IN ({METRICS}))",
            name="CK_AlertEpisode_kind",
        ),
        sa.CheckConstraint(
            "lifecycle_status IN ('OPEN','CLOSED')", name="CK_AlertEpisode_lifecycle"
        ),
        sa.CheckConstraint(
            "acknowledgement_version > 0", name="CK_AlertEpisode_acknowledgement_version"
        ),
        schema="dbo",
    )
    fk("AlertEpisode", ["node_id"], "Node", ["node_id"])
    fk("AlertEpisode", ["metric_code"], "MetricDefinition", ["metric_code"])

    op.create_table(
        "AlertRevision",
        bigint("episode_id"),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        bigint("config_id"),
        bigint("period_id", True),
        code("direction", 8, True),
        bigint("start_event_time", True),
        bigint("end_event_time", True),
        instant("start_backend_at", True),
        instant("end_backend_at", True),
        instant("closed_at", True),
        code("end_reason", 32, True),
        bigint("start_measurement_id", True),
        bigint("end_measurement_id", True),
        sa.Column("lower_snapshot", sa.Numeric(9, 2), nullable=True),
        sa.Column("upper_snapshot", sa.Numeric(9, 2), nullable=True),
        sa.Column("timeout_snapshot", sa.Integer(), nullable=True),
        instant("evaluated_at"),
        bigint("evaluation_generation"),
        sa.Column("acknowledgement_version", sa.Integer(), nullable=False),
        sa.Column("threshold_metadata_json", mssql.NVARCHAR(None), nullable=True),
        sa.PrimaryKeyConstraint("episode_id", "revision_no", name="PK_AlertRevision"),
        sa.CheckConstraint(
            "revision_no > 0 AND acknowledgement_version > 0 AND evaluation_generation >= 0",
            name="CK_AlertRevision_versions",
        ),
        sa.CheckConstraint(
            "direction IS NULL OR direction IN ('LOW','HIGH')", name="CK_AlertRevision_direction"
        ),
        sa.CheckConstraint(
            "(lower_snapshot IS NULL AND upper_snapshot IS NULL) OR "
            "(lower_snapshot IS NOT NULL AND upper_snapshot IS NOT NULL "
            "AND lower_snapshot < upper_snapshot)",
            name="CK_AlertRevision_threshold_snapshot",
        ),
        sa.CheckConstraint(
            "timeout_snapshot IS NULL OR timeout_snapshot > 0",
            name="CK_AlertRevision_timeout_snapshot",
        ),
        sa.CheckConstraint(
            "threshold_metadata_json IS NULL OR ISJSON(threshold_metadata_json) = 1",
            name="CK_AlertRevision_metadata_json",
        ),
        schema="dbo",
    )
    fk("AlertRevision", ["episode_id"], "AlertEpisode", ["episode_id"])
    fk("AlertRevision", ["config_id"], "NodeConfigVersion", ["config_id"])
    fk("AlertRevision", ["period_id"], "MonitoringPeriod", ["period_id"])
    fk("AlertRevision", ["start_measurement_id"], "Measurement", ["measurement_id"], "start_sample")
    fk("AlertRevision", ["end_measurement_id"], "Measurement", ["measurement_id"], "end_sample")
    # Writers set the pointer before commit, after inserting the revision for this episode.
    fk(
        "AlertEpisode",
        ["episode_id", "current_revision_no"],
        "AlertRevision",
        ["episode_id", "revision_no"],
        "current_revision",
    )

    op.create_table(
        "AlertAcknowledgement",
        bigint("episode_id"),
        sa.Column("acknowledgement_version", sa.Integer(), nullable=False),
        sa.Column("acknowledged_revision_no", sa.Integer(), nullable=False),
        bigint("admin_id"),
        instant("acknowledged_at"),
        sa.Column("note", sa.NVARCHAR(1000), nullable=True),
        sa.PrimaryKeyConstraint(
            "episode_id", "acknowledgement_version", name="PK_AlertAcknowledgement"
        ),
        sa.CheckConstraint(
            "acknowledgement_version > 0", name="CK_AlertAcknowledgement_acknowledgement_version"
        ),
        schema="dbo",
    )
    fk("AlertAcknowledgement", ["episode_id"], "AlertEpisode", ["episode_id"])
    fk("AlertAcknowledgement", ["admin_id"], "AdminAccount", ["admin_id"])
    fk(
        "AlertAcknowledgement",
        ["episode_id", "acknowledged_revision_no"],
        "AlertRevision",
        ["episode_id", "revision_no"],
    )

    op.create_table(
        "AlertReplacement",
        bigint("old_episode_id"),
        sa.Column("old_revision_no", sa.Integer(), nullable=False),
        bigint("new_episode_id"),
        sa.Column("new_revision_no", sa.Integer(), nullable=False),
        instant("created_at"),
        bigint("evaluation_generation"),
        code("reason", 32),
        sa.PrimaryKeyConstraint(
            "old_episode_id",
            "old_revision_no",
            "new_episode_id",
            "new_revision_no",
            name="PK_AlertReplacement",
        ),
        sa.CheckConstraint(
            "old_episode_id <> new_episode_id OR old_revision_no < new_revision_no",
            name="CK_AlertReplacement_revision_order",
        ),
        sa.CheckConstraint("evaluation_generation >= 0", name="CK_AlertReplacement_generation"),
        sa.CheckConstraint(
            "reason IN ('SPLIT','MERGE','REWRITE','REPLACED')", name="CK_AlertReplacement_reason"
        ),
        schema="dbo",
    )
    fk(
        "AlertReplacement",
        ["old_episode_id", "old_revision_no"],
        "AlertRevision",
        ["episode_id", "revision_no"],
        "old_revision",
    )
    fk(
        "AlertReplacement",
        ["new_episode_id", "new_revision_no"],
        "AlertRevision",
        ["episode_id", "revision_no"],
        "new_revision",
    )

    op.create_index(
        "UX_AlertEpisode_open_threshold",
        "AlertEpisode",
        ["node_id", "metric_code"],
        unique=True,
        schema="dbo",
        mssql_where=sa.text(f"{CURRENT_OPEN} AND alert_kind = 'THRESHOLD'"),
    )
    op.create_index(
        "UX_AlertEpisode_open_timeout",
        "AlertEpisode",
        ["node_id"],
        unique=True,
        schema="dbo",
        mssql_where=sa.text(f"{CURRENT_OPEN} AND alert_kind = 'TIMEOUT'"),
    )
    op.create_index(
        "IX_AlertEpisode_node_projection",
        "AlertEpisode",
        [
            "node_id",
            "is_current",
            "lifecycle_status",
            sa.text("first_detected_at DESC"),
            sa.text("episode_id DESC"),
        ],
        schema="dbo",
    )


def downgrade() -> None:
    # Drops G4 alert history and timeout retry state. Only for a disposable DB.
    op.drop_constraint(
        "FK_AlertEpisode_current_revision", "AlertEpisode", schema="dbo", type_="foreignkey"
    )
    op.drop_table("AlertReplacement", schema="dbo")
    op.drop_table("AlertAcknowledgement", schema="dbo")
    op.drop_table("AlertRevision", schema="dbo")
    op.drop_table("AlertEpisode", schema="dbo")
    op.drop_column("EvaluationState", "timeout_retry_after", schema="dbo")
    op.drop_column("EvaluationState", "timeout_last_error_code", schema="dbo")
