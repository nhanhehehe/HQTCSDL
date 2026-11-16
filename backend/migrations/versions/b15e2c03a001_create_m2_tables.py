"""Create M2 measurement, configuration and runtime tables.

Revision ID: b15e2c03a001
Revises: a70b8056608d
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mssql

revision = "b15e2c03a001"
down_revision = "a70b8056608d"
branch_labels = None
depends_on = None

COLLATION = "Latin1_General_100_BIN2"
METRICS = "'temperature','airHumidity','pressure','light','soilMoisture'"
RANGES = {
    "temperature": (-40, 85, 2),
    "airHumidity": (0, 100, 2),
    "pressure": (300, 1100, 2),
    "light": (0, 65535, 1),
    "soilMoisture": (0, 100, 1),
}


def bigint(name, nullable=False):
    return sa.Column(name, sa.BigInteger(), nullable=nullable)


def identity(name):
    return sa.Column(name, sa.BigInteger(), sa.Identity(), nullable=False)


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


def metric_range(*values):
    return (
        "("
        + " OR ".join(
            "(metric_code = '"
            + metric
            + "' AND "
            + " AND ".join(
                f"{value} BETWEEN {low} AND {high} AND {value} = ROUND({value}, {scale})"
                for value in values
            )
            + ")"
            for metric, (low, high, scale) in RANGES.items()
        )
        + ")"
    )


def upgrade():
    op.create_table(
        "Measurement",
        identity("measurement_id"),
        bigint("node_id"),
        bigint("gateway_id"),
        bigint("boot_id"),
        bigint("seq"),
        bigint("t_node", True),
        code("time_status", 7),
        bigint("recv_time"),
        sa.Column("rssi", sa.Numeric(8, 2), nullable=True),
        instant("backend_received_at"),
        instant("persisted_at"),
        sa.Column("canonical_version", sa.SmallInteger(), nullable=False),
        sa.Column("original_hash", sa.BINARY(32), nullable=False),
        sa.PrimaryKeyConstraint("measurement_id", name="PK_Measurement"),
        sa.UniqueConstraint("node_id", "boot_id", "seq", name="UQ_Measurement_identity"),
        sa.UniqueConstraint("node_id", "measurement_id", name="UQ_Measurement_node_id"),
        sa.CheckConstraint("boot_id >= 0 AND seq > 0", name="CK_Measurement_identity"),
        sa.CheckConstraint(
            "(time_status = 'VALID' AND t_node IS NOT NULL "
            "AND t_node BETWEEN 0 AND 253402300799) OR "
            "(time_status = 'UNKNOWN' AND t_node IS NULL)",
            name="CK_Measurement_time",
        ),
        sa.CheckConstraint("recv_time BETWEEN 0 AND 253402300799", name="CK_Measurement_recv"),
        sa.CheckConstraint("canonical_version = 1", name="CK_Measurement_canonical"),
        schema="dbo",
    )
    fk("Measurement", ["node_id"], "Node", ["node_id"])
    fk("Measurement", ["gateway_id"], "Gateway", ["gateway_id"])
    op.create_table(
        "MeasurementValue",
        bigint("measurement_id"),
        code("metric_code", 20),
        sa.Column("value", sa.Numeric(9, 2), nullable=True),
        code("read_status", 16),
        sa.PrimaryKeyConstraint("measurement_id", "metric_code", name="PK_MeasurementValue"),
        sa.CheckConstraint(f"metric_code IN ({METRICS})", name="CK_MeasurementValue_metric"),
        sa.CheckConstraint(
            "(read_status = 'OK' AND value IS NOT NULL AND " + metric_range("value") + ") OR "
            "(read_status IN ('READ_ERROR','INVALID_VALUE') AND value IS NULL)",
            name="CK_MeasurementValue_status_range",
        ),
        schema="dbo",
    )
    fk("MeasurementValue", ["measurement_id"], "Measurement", ["measurement_id"])
    fk("MeasurementValue", ["metric_code"], "MetricDefinition", ["metric_code"])
    op.create_table(
        "NodeConfigVersion",
        identity("config_id"),
        bigint("node_id"),
        code("config_kind", 12),
        sa.Column("config_no", sa.Integer(), nullable=False),
        sa.Column("timeout_minutes", sa.Integer(), nullable=True),
        instant("effective_from"),
        instant("effective_to", True),
        bigint("created_by"),
        code("change_reason", 32),
        sa.PrimaryKeyConstraint("config_id", name="PK_NodeConfigVersion"),
        sa.UniqueConstraint("node_id", "config_id", name="UQ_NodeConfigVersion_node_id"),
        sa.UniqueConstraint("node_id", "config_kind", "config_no", name="UQ_NodeConfigVersion_no"),
        sa.CheckConstraint(
            "(config_kind = 'TIMEOUT' AND timeout_minutes IS NOT NULL AND timeout_minutes > 0) "
            "OR (config_kind = 'THRESHOLD' AND timeout_minutes IS NULL)",
            name="CK_NodeConfigVersion_kind",
        ),
        sa.CheckConstraint("config_no > 0", name="CK_NodeConfigVersion_no"),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="CK_NodeConfigVersion_interval",
        ),
        schema="dbo",
    )
    fk("NodeConfigVersion", ["node_id"], "Node", ["node_id"])
    fk("NodeConfigVersion", ["created_by"], "AdminAccount", ["admin_id"])
    op.create_table(
        "ThresholdSetting",
        bigint("config_id"),
        code("metric_code", 20),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("lower_value", sa.Numeric(9, 2), nullable=True),
        sa.Column("upper_value", sa.Numeric(9, 2), nullable=True),
        sa.PrimaryKeyConstraint("config_id", "metric_code", name="PK_ThresholdSetting"),
        sa.CheckConstraint(f"metric_code IN ({METRICS})", name="CK_ThresholdSetting_metric"),
        sa.CheckConstraint(
            "(enabled = 0 AND lower_value IS NULL AND upper_value IS NULL) OR "
            "(lower_value IS NOT NULL AND upper_value IS NOT NULL AND lower_value < upper_value "
            "AND " + metric_range("lower_value", "upper_value") + ")",
            name="CK_ThresholdSetting_range",
        ),
        schema="dbo",
    )
    fk("ThresholdSetting", ["config_id"], "NodeConfigVersion", ["config_id"])
    fk("ThresholdSetting", ["metric_code"], "MetricDefinition", ["metric_code"])
    op.create_table(
        "MonitoringPeriod",
        identity("period_id"),
        bigint("node_id"),
        code("monitoring_kind", 12),
        code("metric_code", 20, True),
        instant("started_at"),
        instant("ended_at", True),
        bigint("start_config_id"),
        code("start_reason", 32),
        code("end_reason", 32, True),
        sa.PrimaryKeyConstraint("period_id", name="PK_MonitoringPeriod"),
        sa.UniqueConstraint("node_id", "period_id", name="UQ_MonitoringPeriod_node_id"),
        sa.CheckConstraint(
            "(monitoring_kind = 'TIMEOUT' AND metric_code IS NULL) OR "
            "(monitoring_kind = 'THRESHOLD' AND metric_code IS NOT NULL "
            f"AND metric_code IN ({METRICS}))",
            name="CK_MonitoringPeriod_kind",
        ),
        sa.CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at", name="CK_MonitoringPeriod_time"
        ),
        schema="dbo",
    )
    fk("MonitoringPeriod", ["node_id"], "Node", ["node_id"])
    fk("MonitoringPeriod", ["metric_code"], "MetricDefinition", ["metric_code"])
    fk(
        "MonitoringPeriod",
        ["node_id", "start_config_id"],
        "NodeConfigVersion",
        ["node_id", "config_id"],
    )
    op.create_table(
        "NodeRuntime",
        bigint("node_id"),
        bigint("current_config_id", True),
        bigint("current_timeout_config_id", True),
        bigint("active_period_id", True),
        bigint("last_measurement_id", True),
        instant("last_new_persisted_at", True),
        sa.PrimaryKeyConstraint("node_id", name="PK_NodeRuntime"),
        schema="dbo",
    )
    fk("NodeRuntime", ["node_id"], "Node", ["node_id"])
    op.create_table(
        "EvaluationState",
        bigint("node_id"),
        bigint("desired_generation"),
        bigint("completed_generation"),
        bigint("replay_from_t_node", True),
        sa.Column("full_replay_required", sa.Boolean(), nullable=False),
        instant("last_success_at", True),
        code("last_error_code", 64, True),
        instant("retry_after", True),
        bigint("last_threshold_measurement_id", True),
        instant("last_threshold_event_at", True),
        sa.Column("threshold_checkpoint_json", mssql.NVARCHAR(None), nullable=True),
        sa.PrimaryKeyConstraint("node_id", name="PK_EvaluationState"),
        sa.CheckConstraint(
            "completed_generation >= 0 AND completed_generation <= desired_generation",
            name="CK_EvaluationState_generation",
        ),
        sa.CheckConstraint(
            "replay_from_t_node IS NULL OR replay_from_t_node BETWEEN 0 AND 253402300799",
            name="CK_EvaluationState_replay",
        ),
        sa.CheckConstraint(
            "threshold_checkpoint_json IS NULL OR ISJSON(threshold_checkpoint_json) = 1",
            name="CK_EvaluationState_json",
        ),
        schema="dbo",
    )
    fk("EvaluationState", ["node_id"], "Node", ["node_id"])
    # All pointer targets now exist; composite FKs also enforce same-Node ownership.
    for column in ("current_config_id", "current_timeout_config_id"):
        fk(
            "NodeRuntime",
            ["node_id", column],
            "NodeConfigVersion",
            ["node_id", "config_id"],
            column,
        )
    fk("NodeRuntime", ["node_id", "active_period_id"], "MonitoringPeriod", ["node_id", "period_id"])
    fk(
        "NodeRuntime",
        ["node_id", "last_measurement_id"],
        "Measurement",
        ["node_id", "measurement_id"],
    )
    fk(
        "EvaluationState",
        ["node_id", "last_threshold_measurement_id"],
        "Measurement",
        ["node_id", "measurement_id"],
    )
    op.create_index(
        "UX_NodeConfigVersion_current",
        "NodeConfigVersion",
        ["node_id", "config_kind"],
        unique=True,
        schema="dbo",
        mssql_where=sa.text("effective_to IS NULL"),
    )
    op.create_index(
        "UX_MonitoringPeriod_timeout",
        "MonitoringPeriod",
        ["node_id"],
        unique=True,
        schema="dbo",
        mssql_where=sa.text("ended_at IS NULL AND monitoring_kind = 'TIMEOUT'"),
    )
    op.create_index(
        "UX_MonitoringPeriod_threshold",
        "MonitoringPeriod",
        ["node_id", "metric_code"],
        unique=True,
        schema="dbo",
        mssql_where=sa.text("ended_at IS NULL AND monitoring_kind = 'THRESHOLD'"),
    )
    for name, table, columns in (
        (
            "IX_Measurement_event",
            "Measurement",
            ["node_id", sa.text("t_node DESC"), sa.text("boot_id DESC"), sa.text("seq DESC")],
        ),
        ("IX_Measurement_gateway", "Measurement", ["gateway_id", "persisted_at", "measurement_id"]),
        (
            "IX_NodeConfigVersion_history",
            "NodeConfigVersion",
            ["node_id", "config_kind", "effective_from", "config_no"],
        ),
        (
            "IX_MonitoringPeriod_history",
            "MonitoringPeriod",
            ["node_id", "monitoring_kind", "metric_code", "started_at", "period_id"],
        ),
        ("IX_EvaluationState_pending", "EvaluationState", ["retry_after", "node_id"]),
    ):
        op.create_index(name, table, columns, schema="dbo")
    op.create_index(
        "IX_Measurement_unknown",
        "Measurement",
        ["node_id", "persisted_at", "measurement_id"],
        schema="dbo",
        mssql_where=sa.text("t_node IS NULL"),
    )
    # Existing M1 Nodes receive only empty runtime/state, never invented configs or thresholds.
    op.execute("INSERT INTO dbo.NodeRuntime (node_id) SELECT node_id FROM dbo.Node")
    op.execute(
        "INSERT INTO dbo.EvaluationState "
        "(node_id, desired_generation, completed_generation, full_replay_required) "
        "SELECT node_id, 0, 0, 0 FROM dbo.Node"
    )


def downgrade():
    # No M1 tables or seed data are removed. Runtime pointers disappear before their targets.
    for table in (
        "EvaluationState",
        "NodeRuntime",
        "MonitoringPeriod",
        "ThresholdSetting",
        "NodeConfigVersion",
        "MeasurementValue",
        "Measurement",
    ):
        op.drop_table(table, schema="dbo")
