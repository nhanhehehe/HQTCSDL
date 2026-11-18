"""Add Farm/Zone lifecycle columns.

Revision ID: fee0fa015af7
Revises: b15e2c03a001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mssql

revision = "fee0fa015af7"
down_revision = "b15e2c03a001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "Farm",
        sa.Column("archived_at", mssql.DATETIME2(precision=3), nullable=True),
        schema="dbo",
    )
    op.add_column("Farm", sa.Column("row_ver", mssql.ROWVERSION(), nullable=False), schema="dbo")
    op.add_column(
        "Zone",
        sa.Column("archived_at", mssql.DATETIME2(precision=3), nullable=True),
        schema="dbo",
    )
    op.add_column("Zone", sa.Column("row_ver", mssql.ROWVERSION(), nullable=False), schema="dbo")


def downgrade() -> None:
    # Only for an explicitly approved disposable DB: archive state will be lost.
    op.drop_column("Zone", "row_ver", schema="dbo")
    op.drop_column("Zone", "archived_at", schema="dbo")
    op.drop_column("Farm", "row_ver", schema="dbo")
    op.drop_column("Farm", "archived_at", schema="dbo")
