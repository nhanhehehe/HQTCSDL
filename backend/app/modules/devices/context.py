import json
import unicodedata

from sqlalchemy import text

from app.modules.devices.schemas import sized


def area_code(value):
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError()
    sized(value, 128)
    if any(unicodedata.category(c).startswith("C") for c in value):
        raise ValueError()
    return value


def parse_manifest(raw):
    try:
        data = json.loads(raw or "")
        if set(data) != {"farm_code", "zone_codes"}:
            return None
        zones = data["zone_codes"]
        if set(zones) != {"indoor", "outdoor"}:
            return None
        area_code(data["farm_code"])
        for value in zones.values():
            area_code(value)
        if zones["indoor"] == zones["outdoor"]:
            return None
        return data
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError):
        return None


def read_context(session, manifest, *, lock=False):
    issues = []
    result = {"farm": None, "zones": [], "configuration": {"state": "INVALID", "issues": issues}}

    def issue(code, kind, expected=None, row=None):
        item = {"code": code, "entity_type": kind}
        if expected is not None:
            item["expected_code"] = expected
        if row is not None:
            item["id"] = str(row["id"])
        issues.append(item)

    def lookup(table, column, expected, kind):
        rows = (
            session.execute(
                text(
                    f"SELECT {table.lower()}_id AS id, {column} AS code, name, description"
                    + (", farm_id" if table == "Zone" else "")
                    + f" FROM dbo.{table}"
                    + (" WITH (UPDLOCK,HOLDLOCK)" if lock else "")
                    + f" WHERE {column}=:code "
                    + f"AND DATALENGTH({column})=DATALENGTH(CAST(:code AS NVARCHAR(128)))"
                ),
                {"code": expected},
            )
            .mappings()
            .all()
        )
        if len(rows) != 1:
            issue(f"{kind}_NOT_FOUND" if not rows else f"{kind}_AMBIGUOUS", kind, expected)
            return None
        row = dict(rows[0])
        try:
            area_code(row["code"])
            if not row["name"].strip():
                raise ValueError()
            sized(row["name"], 200)
            if row["description"] is not None:
                sized(row["description"], 1000)
        except (ValueError, UnicodeError, AttributeError):
            issue(f"{kind}_INVALID_METADATA", kind, expected, row)
            return None
        row["id"] = str(row["id"])
        if "farm_id" in row:
            row["farm_id"] = str(row["farm_id"])
        return row

    if manifest is None:
        issue("AREA_MANIFEST_INVALID", "CONFIGURATION")
        return result
    farm = lookup("Farm", "farm_code", manifest["farm_code"], "FARM")
    result["farm"] = farm
    if farm:
        for role in ("indoor", "outdoor"):
            expected = manifest["zone_codes"][role]
            zone = lookup("Zone", "zone_code", expected, "ZONE")
            if zone and zone["farm_id"] != farm["id"]:
                issue("ZONE_FARM_MISMATCH", "ZONE", expected, zone)
            elif zone:
                result["zones"].append(zone)
    if not issues:
        result["configuration"]["state"] = "VALID"
    return result
