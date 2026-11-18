from sqlalchemy import text

from app.core.web import fail
from app.modules.areas.schemas import area_code
from app.modules.devices.schemas import sized


def _valid_area_metadata(row: dict, code_key: str) -> bool:
    try:
        area_code(row[code_key])
        if not isinstance(row["name"], str) or not row["name"].strip():
            return False
        sized(row["name"], 200)
        if row["description"] is not None:
            if not isinstance(row["description"], str):
                return False
            sized(row["description"], 1000)
        return True
    except (ValueError, UnicodeError, AttributeError):
        return False


def _legacy_farm(row: dict) -> dict:
    return {
        "id": str(row["farm_id"]),
        "code": row["farm_code"],
        "name": row["name"],
        "description": row["description"],
    }


def _legacy_zone(row: dict) -> dict:
    return {
        "id": str(row["zone_id"]),
        "farm_id": str(row["farm_id"]),
        "code": row["zone_code"],
        "name": row["name"],
        "description": row["description"],
    }


def read_context(session, farm_id: str | None = None) -> dict:
    """Read-only legacy /api/v1/context compatibility adapter backed by SQL Server."""
    issues: list[dict] = []
    result: dict = {
        "farm": None,
        "zones": [],
        "configuration": {"state": "INVALID", "issues": issues},
    }

    if farm_id is None:
        active_farms = (
            session.execute(
                text(
                    "SELECT farm_id, farm_code, name, description, archived_at "
                    "FROM dbo.Farm WHERE archived_at IS NULL ORDER BY farm_id ASC"
                )
            )
            .mappings()
            .all()
        )
        if not active_farms:
            issues.append({"code": "AREA_NOT_CONFIGURED", "entity_type": "CONFIGURATION"})
            return result
        if len(active_farms) > 1:
            fail(409, "AREA_SELECTION_REQUIRED", "Cần chọn farm_id cụ thể khi có nhiều Farm")
        farm_row = dict(active_farms[0])
    else:
        row = (
            session.execute(
                text(
                    "SELECT farm_id, farm_code, name, description, archived_at "
                    "FROM dbo.Farm WHERE farm_id = :id"
                ),
                {"id": int(farm_id)},
            )
            .mappings()
            .first()
        )
        if row is None:
            fail(404, "NOT_FOUND")
        farm_row = dict(row)

    farm_valid = _valid_area_metadata(farm_row, "farm_code")
    if not farm_valid:
        issues.append(
            {"code": "INVALID_METADATA", "entity_type": "FARM", "id": str(farm_row["farm_id"])}
        )
        return result

    result["farm"] = _legacy_farm(farm_row)
    if farm_row["archived_at"] is not None:
        issues.append(
            {"code": "AREA_ARCHIVED", "entity_type": "FARM", "id": str(farm_row["farm_id"])}
        )
        return result

    zone_rows = (
        session.execute(
            text(
                "SELECT zone_id, farm_id, zone_code, name, description "
                "FROM dbo.Zone WHERE farm_id = :farm_id AND archived_at IS NULL "
                "ORDER BY zone_id ASC"
            ),
            {"farm_id": farm_row["farm_id"]},
        )
        .mappings()
        .all()
    )
    for z in zone_rows:
        z_dict = dict(z)
        if not _valid_area_metadata(z_dict, "zone_code"):
            issues.append(
                {
                    "code": "INVALID_METADATA",
                    "entity_type": "ZONE",
                    "id": str(z_dict["zone_id"]),
                }
            )
        else:
            result["zones"].append(_legacy_zone(z_dict))

    if not issues:
        result["configuration"]["state"] = "VALID"
    return result
