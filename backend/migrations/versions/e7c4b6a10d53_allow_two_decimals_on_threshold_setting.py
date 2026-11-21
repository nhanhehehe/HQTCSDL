"""Allow two decimal places on ThresholdSetting bounds.

Revision ID: e7c4b6a10d53
Revises: d6b3a5f09c42
"""

from alembic import op

revision = "e7c4b6a10d53"
down_revision = "d6b3a5f09c42"
branch_labels = None
depends_on = None

_BOUNDS = {
    "temperature": (-40, 85, 2),
    "airHumidity": (0, 100, 2),
    "pressure": (300, 1100, 2),
    "light": (0, 65535, 1),
    "soilMoisture": (0, 100, 1),
}


def _range_predicate(*, enforce_metric_scale: bool) -> str:
    clauses = []
    for metric, (low, high, scale) in _BOUNDS.items():
        parts = []
        for col in ("lower_value", "upper_value"):
            part = f"{col} BETWEEN {low} AND {high}"
            if enforce_metric_scale:
                part += f" AND {col} = ROUND({col}, {scale})"
            parts.append(part)
        clauses.append(f"(metric_code = '{metric}' AND " + " AND ".join(parts) + ")")
    return (
        "(enabled = 0 AND lower_value IS NULL AND upper_value IS NULL) OR "
        "(lower_value IS NOT NULL AND upper_value IS NOT NULL AND lower_value < upper_value "
        "AND (" + " OR ".join(clauses) + "))"
    )


def upgrade() -> None:
    op.drop_constraint("CK_ThresholdSetting_range", "ThresholdSetting", schema="dbo", type_="check")
    op.create_check_constraint(
        "CK_ThresholdSetting_range",
        "ThresholdSetting",
        _range_predicate(enforce_metric_scale=False),
        schema="dbo",
    )


def downgrade() -> None:
    op.drop_constraint("CK_ThresholdSetting_range", "ThresholdSetting", schema="dbo", type_="check")
    op.create_check_constraint(
        "CK_ThresholdSetting_range",
        "ThresholdSetting",
        _range_predicate(enforce_metric_scale=True),
        schema="dbo",
    )
