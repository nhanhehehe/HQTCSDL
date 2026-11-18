import base64
from datetime import UTC

from sqlalchemy import text

from app.core.web import serialize_utc

TABLES = {
    "nodes": ("Node", "node_id", "device_code"),
    "gateways": ("Gateway", "gateway_id", "gateway_code"),
}


def credential_view(row: dict) -> dict:
    return {
        "id": str(row["credential_id"]),
        "gateway_id": str(row["gateway_id"]),
        "created_at": serialize_utc(row["created_at"]),
        "revoked_at": serialize_utc(row["revoked_at"]),
        "status": "REVOKED" if row["revoked_at"] is not None else "ACTIVE",
    }


def list_credentials(session, *, gateway_id: str, boundary: int | None, limit: int) -> list[dict]:
    if boundary is None:
        rows = (
            session.execute(
                text(
                    "SELECT TOP (:limit) credential_id, gateway_id, created_at, revoked_at "
                    "FROM dbo.GatewayCredential WHERE gateway_id = :gid "
                    "ORDER BY credential_id DESC"
                ),
                {"limit": limit, "gid": int(gateway_id)},
            )
            .mappings()
            .all()
        )
    else:
        rows = (
            session.execute(
                text(
                    "SELECT TOP (:limit) credential_id, gateway_id, created_at, revoked_at "
                    "FROM dbo.GatewayCredential "
                    "WHERE gateway_id = :gid AND credential_id < :boundary "
                    "ORDER BY credential_id DESC"
                ),
                {"limit": limit, "gid": int(gateway_id), "boundary": boundary},
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]


def get_credential(
    session, *, gateway_id: str, credential_id: str, lock: bool = False
) -> dict | None:
    hint = " WITH (UPDLOCK, HOLDLOCK)" if lock else ""
    row = (
        session.execute(
            text(
                "SELECT credential_id, gateway_id, created_at, revoked_at "
                f"FROM dbo.GatewayCredential{hint} "
                "WHERE credential_id = :cid AND gateway_id = :gid"
            ),
            {"cid": int(credential_id), "gid": int(gateway_id)},
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def count_active_credentials(session, gateway_id: str) -> int:
    return int(
        session.execute(
            text(
                "SELECT COUNT_BIG(*) FROM dbo.GatewayCredential "
                "WHERE gateway_id = :gid AND revoked_at IS NULL"
            ),
            {"gid": int(gateway_id)},
        ).scalar_one()
    )


def insert_credential(session, *, gateway_id: str, key_hash: bytes) -> dict:
    row = (
        session.execute(
            text(
                "INSERT INTO dbo.GatewayCredential (gateway_id, key_hash, created_at) "
                "OUTPUT inserted.credential_id, inserted.gateway_id, "
                "inserted.created_at, inserted.revoked_at "
                "VALUES (:gid, :key_hash, SYSUTCDATETIME())"
            ),
            {"gid": int(gateway_id), "key_hash": key_hash},
        )
        .mappings()
        .one()
    )
    return dict(row)


def revoke_credential(session, *, gateway_id: str, credential_id: str) -> dict | None:
    row = (
        session.execute(
            text(
                "UPDATE dbo.GatewayCredential SET revoked_at = SYSUTCDATETIME() "
                "OUTPUT inserted.credential_id, inserted.gateway_id, "
                "inserted.created_at, inserted.revoked_at "
                "WHERE credential_id = :cid AND gateway_id = :gid AND revoked_at IS NULL"
            ),
            {"cid": int(credential_id), "gid": int(gateway_id)},
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def list_metrics(session) -> list[dict]:
    rows = (
        session.execute(
            text(
                "SELECT metric_code, unit, min_value, max_value, decimal_places "
                "FROM dbo.MetricDefinition ORDER BY CASE metric_code "
                "WHEN 'temperature' THEN 1 WHEN 'airHumidity' THEN 2 "
                "WHEN 'pressure' THEN 3 WHEN 'light' THEN 4 ELSE 5 END"
            )
        )
        .mappings()
        .all()
    )
    return [
        {
            "code": r["metric_code"],
            "unit": r["unit"],
            "min_value": str(r["min_value"]),
            "max_value": str(r["max_value"]),
            "decimal_places": int(r["decimal_places"]),
        }
        for r in rows
    ]


def resolve_gateway_farm_id(session, gateway_id: str) -> int | None:
    return session.execute(
        text("SELECT farm_id FROM dbo.Gateway WHERE gateway_id = :id"),
        {"id": int(gateway_id)},
    ).scalar()


def resolve_node_parents(session, node_id: str) -> dict | None:
    row = (
        session.execute(
            text(
                "SELECT n.node_id, n.zone_id, z.farm_id, n.gateway_id "
                "FROM dbo.Node n JOIN dbo.Zone z ON z.zone_id = n.zone_id "
                "WHERE n.node_id = :id"
            ),
            {"id": int(node_id)},
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def lock_gateway(session, gateway_id: int | str) -> dict | None:
    row = (
        session.execute(
            text("SELECT * FROM dbo.Gateway WITH (UPDLOCK, HOLDLOCK) WHERE gateway_id = :id"),
            {"id": int(gateway_id)},
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def lock_node(session, node_id: int | str) -> dict | None:
    row = (
        session.execute(
            text("SELECT * FROM dbo.Node WITH (UPDLOCK, HOLDLOCK) WHERE node_id = :id"),
            {"id": int(node_id)},
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


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
