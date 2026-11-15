"""create m1 foundation tables

Revision ID: a70b8056608d
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mssql

revision = "a70b8056608d"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "Farm",
        sa.Column(
            "farm_id",
            sa.BigInteger(),
            sa.Identity(start=1, increment=1),
            nullable=False,
        ),
        sa.Column(
            "farm_code",
            sa.NVARCHAR(128, collation="Latin1_General_100_BIN2"),
            nullable=False,
        ),
        sa.Column("name", sa.NVARCHAR(200), nullable=False),
        sa.Column("description", sa.NVARCHAR(1000), nullable=True),
        sa.PrimaryKeyConstraint("farm_id", name="PK_Farm"),
        sa.UniqueConstraint("farm_code", name="UQ_Farm_farm_code"),
        schema="dbo",
    )

    op.create_table(
        "Zone",
        sa.Column(
            "zone_id",
            sa.BigInteger(),
            sa.Identity(start=1, increment=1),
            nullable=False,
        ),
        sa.Column("farm_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "zone_code",
            sa.NVARCHAR(128, collation="Latin1_General_100_BIN2"),
            nullable=False,
        ),
        sa.Column("name", sa.NVARCHAR(200), nullable=False),
        sa.Column("description", sa.NVARCHAR(1000), nullable=True),
        sa.PrimaryKeyConstraint("zone_id", name="PK_Zone"),
        sa.UniqueConstraint("zone_code", name="UQ_Zone_zone_code"),
        sa.ForeignKeyConstraint(
            ["farm_id"],
            ["dbo.Farm.farm_id"],
            name="FK_Zone_Farm",
        ),
        schema="dbo",
    )

    op.create_table(
        "AdminAccount",
        sa.Column(
            "admin_id",
            sa.BigInteger(),
            sa.Identity(start=1, increment=1),
            nullable=False,
        ),
        sa.Column(
            "username",
            sa.NVARCHAR(128, collation="Latin1_General_100_BIN2"),
            nullable=False,
        ),
        sa.Column("password_hash", sa.NVARCHAR(255), nullable=False),
        sa.Column(
            "role_code",
            sa.VARCHAR(16, collation="Latin1_General_100_BIN2"),
            nullable=False,
        ),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", mssql.DATETIME2(precision=3), nullable=False),
        sa.PrimaryKeyConstraint("admin_id", name="PK_AdminAccount"),
        sa.UniqueConstraint("username", name="UQ_AdminAccount_username"),
        sa.CheckConstraint(
            "role_code = 'ADMIN'",
            name="CK_AdminAccount_role",
        ),
        schema="dbo",
    )

    op.create_table(
        "AdminSession",
        sa.Column(
            "session_id",
            sa.BigInteger(),
            sa.Identity(start=1, increment=1),
            nullable=False,
        ),
        sa.Column("admin_id", sa.BigInteger(), nullable=False),
        sa.Column("token_hash", sa.BINARY(32), nullable=False),
        sa.Column("created_at", mssql.DATETIME2(precision=3), nullable=False),
        sa.Column("expires_at", mssql.DATETIME2(precision=3), nullable=False),
        sa.Column("revoked_at", mssql.DATETIME2(precision=3), nullable=True),
        sa.PrimaryKeyConstraint("session_id", name="PK_AdminSession"),
        sa.UniqueConstraint("token_hash", name="UQ_AdminSession_token_hash"),
        sa.ForeignKeyConstraint(
            ["admin_id"],
            ["dbo.AdminAccount.admin_id"],
            name="FK_AdminSession_AdminAccount",
        ),
        sa.CheckConstraint(
            "expires_at = DATEADD(hour, 2, created_at)",
            name="CK_AdminSession_expiry",
        ),
        schema="dbo",
    )

    op.create_table(
        "MetricDefinition",
        sa.Column(
            "metric_code",
            sa.VARCHAR(20, collation="Latin1_General_100_BIN2"),
            nullable=False,
        ),
        sa.Column("unit", sa.NVARCHAR(16), nullable=False),
        sa.Column("min_value", sa.Numeric(9, 2), nullable=False),
        sa.Column("max_value", sa.Numeric(9, 2), nullable=False),
        sa.Column("decimal_places", mssql.TINYINT(), nullable=False),
        sa.PrimaryKeyConstraint("metric_code", name="PK_MetricDefinition"),
        sa.CheckConstraint(
            "metric_code IN ('temperature', 'airHumidity', 'pressure', 'light', 'soilMoisture')",
            name="CK_MetricDefinition_code",
        ),
        sa.CheckConstraint(
            "min_value <= max_value",
            name="CK_MetricDefinition_range",
        ),
        schema="dbo",
    )

    op.create_table(
        "Gateway",
        sa.Column(
            "gateway_id",
            sa.BigInteger(),
            sa.Identity(start=1, increment=1),
            nullable=False,
        ),
        sa.Column(
            "gateway_code",
            sa.VARCHAR(128, collation="Latin1_General_100_BIN2"),
            nullable=False,
        ),
        sa.Column("farm_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.NVARCHAR(200), nullable=False),
        sa.Column("notes", sa.NVARCHAR(1000), nullable=True),
        sa.Column(
            "receiving_enabled",
            sa.Boolean(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column("row_ver", mssql.ROWVERSION(), nullable=False),
        sa.PrimaryKeyConstraint("gateway_id", name="PK_Gateway"),
        sa.UniqueConstraint("gateway_code", name="UQ_Gateway_gateway_code"),
        sa.ForeignKeyConstraint(
            ["farm_id"],
            ["dbo.Farm.farm_id"],
            name="FK_Gateway_Farm",
        ),
        schema="dbo",
    )

    op.create_table(
        "Node",
        sa.Column(
            "node_id",
            sa.BigInteger(),
            sa.Identity(start=1, increment=1),
            nullable=False,
        ),
        sa.Column(
            "device_code",
            sa.VARCHAR(128, collation="Latin1_General_100_BIN2"),
            nullable=False,
        ),
        sa.Column("zone_id", sa.BigInteger(), nullable=False),
        sa.Column("gateway_id", sa.BigInteger(), nullable=True),
        sa.Column("name", sa.NVARCHAR(200), nullable=False),
        sa.Column("notes", sa.NVARCHAR(1000), nullable=True),
        sa.Column(
            "receiving_enabled",
            sa.Boolean(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column("row_ver", mssql.ROWVERSION(), nullable=False),
        sa.PrimaryKeyConstraint("node_id", name="PK_Node"),
        sa.UniqueConstraint("device_code", name="UQ_Node_device_code"),
        sa.ForeignKeyConstraint(
            ["zone_id"],
            ["dbo.Zone.zone_id"],
            name="FK_Node_Zone",
        ),
        sa.ForeignKeyConstraint(
            ["gateway_id"],
            ["dbo.Gateway.gateway_id"],
            name="FK_Node_Gateway",
        ),
        schema="dbo",
    )

    op.create_table(
        "GatewayCredential",
        sa.Column(
            "credential_id",
            sa.BigInteger(),
            sa.Identity(start=1, increment=1),
            nullable=False,
        ),
        sa.Column("gateway_id", sa.BigInteger(), nullable=False),
        sa.Column("key_hash", sa.BINARY(32), nullable=False),
        sa.Column("created_at", mssql.DATETIME2(precision=3), nullable=False),
        sa.Column("revoked_at", mssql.DATETIME2(precision=3), nullable=True),
        sa.PrimaryKeyConstraint("credential_id", name="PK_GatewayCredential"),
        sa.UniqueConstraint("key_hash", name="UQ_GatewayCredential_key_hash"),
        sa.ForeignKeyConstraint(
            ["gateway_id"],
            ["dbo.Gateway.gateway_id"],
            name="FK_GatewayCredential_Gateway",
        ),
        schema="dbo",
    )


def downgrade() -> None:
    op.drop_table("GatewayCredential", schema="dbo")
    op.drop_table("Node", schema="dbo")
    op.drop_table("Gateway", schema="dbo")
    op.drop_table("MetricDefinition", schema="dbo")
    op.drop_table("AdminSession", schema="dbo")
    op.drop_table("AdminAccount", schema="dbo")
    op.drop_table("Zone", schema="dbo")
    op.drop_table("Farm", schema="dbo")
