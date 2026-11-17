import base64
from datetime import UTC

from sqlalchemy import text

TABLES = {
    "nodes": ("Node", "node_id", "device_code"),
    "gateways": ("Gateway", "gateway_id", "gateway_code"),
}


def profile(session, kind, row_id):
    table, pk, business = TABLES[kind]
    row = (
        session.execute(text(f"SELECT * FROM dbo.{table} WHERE {pk}=:id"), {"id": row_id})
        .mappings()
        .first()
    )
    if row is None:
        return None
    value = {
        "id": str(row[pk]),
        "device_id" if kind == "nodes" else "gateway_id": row[business],
        "name": row["name"],
        "notes": row["notes"],
        "receiving_enabled": bool(row["receiving_enabled"]),
        "row_version": base64.b64encode(row["row_ver"]).decode(),
    }
    if kind == "nodes":
        zone = (
            session.execute(
                text(
                    "SELECT zone_id AS id, zone_code AS code, name FROM dbo.Zone WHERE zone_id=:id"
                ),
                {"id": row["zone_id"]},
            )
            .mappings()
            .one()
        )
        value["zone"] = {**dict(zone), "id": str(zone["id"])}
        gateway = (
            session.execute(
                text(
                    "SELECT gateway_id AS id, gateway_code AS code "
                    "FROM dbo.Gateway WHERE gateway_id=:id"
                ),
                {"id": row["gateway_id"]},
            )
            .mappings()
            .first()
        )
        value["gateway"] = {**dict(gateway), "id": str(gateway["id"])} if gateway else None
        value["metrics"] = [
            dict(r)
            for r in session.execute(
                text(
                    "SELECT metric_code, unit FROM dbo.MetricDefinition ORDER BY CASE metric_code "
                    "WHEN 'temperature' THEN 1 WHEN 'airHumidity' THEN 2 WHEN 'pressure' THEN 3 "
                    "WHEN 'light' THEN 4 ELSE 5 END"
                )
            ).mappings()
        ]
        stamp = session.execute(
            text("SELECT last_new_persisted_at FROM dbo.NodeRuntime WHERE node_id=:id"),
            {"id": row_id},
        ).scalar_one()
    else:
        farm = (
            session.execute(
                text(
                    "SELECT farm_id AS id, farm_code AS code, name FROM dbo.Farm WHERE farm_id=:id"
                ),
                {"id": row["farm_id"]},
            )
            .mappings()
            .one()
        )
        value["farm"] = {**dict(farm), "id": str(farm["id"])}
        value["nodes"] = [
            {"id": str(r.node_id), "device_id": r.device_code, "name": r.name}
            for r in session.execute(
                text(
                    "SELECT node_id,device_code,name "
                    "FROM dbo.Node WHERE gateway_id=:id ORDER BY node_id"
                ),
                {"id": row_id},
            )
        ]
        stamp = session.execute(
            text("SELECT MAX(persisted_at) FROM dbo.Measurement WHERE gateway_id=:id"),
            {"id": row_id},
        ).scalar_one()
    value["last_new_persisted_at"] = (
        stamp.replace(tzinfo=UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        if stamp
        else None
    )
    return value
