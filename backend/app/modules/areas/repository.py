import base64

from sqlalchemy import text

from app.core.web import serialize_utc


def _farm_dto(row) -> dict:
    return {
        "id": str(row["farm_id"]),
        "code": row["farm_code"],
        "name": row["name"],
        "description": row["description"],
        "archived_at": serialize_utc(row["archived_at"]),
        "row_version": base64.b64encode(row["row_ver"]).decode(),
    }


def _zone_dto(row) -> dict:
    return {
        "id": str(row["zone_id"]),
        "farm_id": str(row["farm_id"]),
        "code": row["zone_code"],
        "name": row["name"],
        "description": row["description"],
        "archived_at": serialize_utc(row["archived_at"]),
        "row_version": base64.b64encode(row["row_ver"]).decode(),
    }


def get_farm(session, farm_id: str, *, lock: bool = False) -> dict | None:
    hint = " WITH (UPDLOCK, HOLDLOCK)" if lock else ""
    row = (
        session.execute(
            text(
                "SELECT farm_id, farm_code, name, description, archived_at, row_ver "
                f"FROM dbo.Farm{hint} WHERE farm_id = :id"
            ),
            {"id": int(farm_id)},
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def get_zone(session, zone_id: str, *, lock: bool = False) -> dict | None:
    hint = " WITH (UPDLOCK, HOLDLOCK)" if lock else ""
    row = (
        session.execute(
            text(
                "SELECT zone_id, farm_id, zone_code, name, description, archived_at, row_ver "
                f"FROM dbo.Zone{hint} WHERE zone_id = :id"
            ),
            {"id": int(zone_id)},
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def get_zone_farm_id(session, zone_id: str) -> int | None:
    return session.execute(
        text("SELECT farm_id FROM dbo.Zone WHERE zone_id = :id"),
        {"id": int(zone_id)},
    ).scalar()


def list_farms(session, *, status: str, code: str | None, boundary: int, limit: int) -> list[dict]:
    clauses = ["farm_id > :boundary"]
    if status == "ACTIVE":
        clauses.append("archived_at IS NULL")
    elif status == "ARCHIVED":
        clauses.append("archived_at IS NOT NULL")
    if code is not None:
        clauses.append(
            "farm_code = :code AND DATALENGTH(farm_code) = DATALENGTH(CAST(:code AS NVARCHAR(128)))"
        )
    where = " AND ".join(clauses)
    rows = (
        session.execute(
            text(
                f"SELECT TOP (:limit) farm_id, farm_code, name, description, archived_at, row_ver "
                f"FROM dbo.Farm WHERE {where} ORDER BY farm_id ASC"
            ),
            {"limit": limit, "boundary": boundary, "code": code},
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


def list_zones(
    session, *, farm_id: str, status: str, code: str | None, boundary: int, limit: int
) -> list[dict]:
    clauses = ["farm_id = :farm_id", "zone_id > :boundary"]
    if status == "ACTIVE":
        clauses.append("archived_at IS NULL")
    elif status == "ARCHIVED":
        clauses.append("archived_at IS NOT NULL")
    if code is not None:
        clauses.append(
            "zone_code = :code AND DATALENGTH(zone_code) = DATALENGTH(CAST(:code AS NVARCHAR(128)))"
        )
    where = " AND ".join(clauses)
    rows = (
        session.execute(
            text(
                f"SELECT TOP (:limit) zone_id, farm_id, zone_code, name, description, "
                f"archived_at, row_ver FROM dbo.Zone WHERE {where} ORDER BY zone_id ASC"
            ),
            {"limit": limit, "farm_id": int(farm_id), "boundary": boundary, "code": code},
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


def insert_farm(session, *, code: str, name: str, description: str | None) -> dict:
    row = (
        session.execute(
            text(
                "INSERT INTO dbo.Farm (farm_code, name, description) "
                "OUTPUT inserted.farm_id, inserted.farm_code, inserted.name, "
                "inserted.description, inserted.archived_at, inserted.row_ver "
                "VALUES (:code, :name, :description)"
            ),
            {"code": code, "name": name, "description": description},
        )
        .mappings()
        .one()
    )
    return dict(row)


def insert_zone(session, *, farm_id: str, code: str, name: str, description: str | None) -> dict:
    row = (
        session.execute(
            text(
                "INSERT INTO dbo.Zone (farm_id, zone_code, name, description) "
                "OUTPUT inserted.zone_id, inserted.farm_id, inserted.zone_code, inserted.name, "
                "inserted.description, inserted.archived_at, inserted.row_ver "
                "VALUES (:farm_id, :code, :name, :description)"
            ),
            {"farm_id": int(farm_id), "code": code, "name": name, "description": description},
        )
        .mappings()
        .one()
    )
    return dict(row)


def update_farm_metadata(
    session, *, farm_id: str, expected_version: bytes, changes: dict
) -> dict | None:
    assignments = ", ".join(f"{field} = :{field}" for field in changes)
    row = (
        session.execute(
            text(
                f"UPDATE dbo.Farm SET {assignments} "
                "OUTPUT inserted.farm_id, inserted.farm_code, inserted.name, "
                "inserted.description, inserted.archived_at, inserted.row_ver "
                "WHERE farm_id = :id AND row_ver = :version"
            ),
            {**changes, "id": int(farm_id), "version": expected_version},
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def update_zone_metadata(
    session, *, zone_id: str, expected_version: bytes, changes: dict
) -> dict | None:
    assignments = ", ".join(f"{field} = :{field}" for field in changes)
    row = (
        session.execute(
            text(
                f"UPDATE dbo.Zone SET {assignments} "
                "OUTPUT inserted.zone_id, inserted.farm_id, inserted.zone_code, inserted.name, "
                "inserted.description, inserted.archived_at, inserted.row_ver "
                "WHERE zone_id = :id AND row_ver = :version"
            ),
            {**changes, "id": int(zone_id), "version": expected_version},
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def lock_farm_zones(session, farm_id: str) -> list[dict]:
    rows = (
        session.execute(
            text(
                "SELECT zone_id, archived_at FROM dbo.Zone WITH (UPDLOCK, HOLDLOCK) "
                "WHERE farm_id = :farm_id ORDER BY zone_id ASC"
            ),
            {"farm_id": int(farm_id)},
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


def lock_farm_gateways(session, farm_id: str) -> list[dict]:
    rows = (
        session.execute(
            text(
                "SELECT gateway_id, receiving_enabled FROM dbo.Gateway WITH (UPDLOCK, HOLDLOCK) "
                "WHERE farm_id = :farm_id ORDER BY gateway_id ASC"
            ),
            {"farm_id": int(farm_id)},
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


def lock_zone_nodes(session, zone_id: str) -> list[dict]:
    rows = (
        session.execute(
            text(
                "SELECT node_id, receiving_enabled FROM dbo.Node WITH (UPDLOCK, HOLDLOCK) "
                "WHERE zone_id = :zone_id ORDER BY node_id ASC"
            ),
            {"zone_id": int(zone_id)},
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


def set_farm_archived(
    session, *, farm_id: str, expected_version: bytes, archive: bool
) -> dict | None:
    expr = "SYSUTCDATETIME()" if archive else "NULL"
    row = (
        session.execute(
            text(
                f"UPDATE dbo.Farm SET archived_at = {expr} "
                "OUTPUT inserted.farm_id, inserted.farm_code, inserted.name, "
                "inserted.description, inserted.archived_at, inserted.row_ver "
                "WHERE farm_id = :id AND row_ver = :version"
            ),
            {"id": int(farm_id), "version": expected_version},
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def set_zone_archived(
    session, *, zone_id: str, expected_version: bytes, archive: bool
) -> dict | None:
    expr = "SYSUTCDATETIME()" if archive else "NULL"
    row = (
        session.execute(
            text(
                f"UPDATE dbo.Zone SET archived_at = {expr} "
                "OUTPUT inserted.zone_id, inserted.farm_id, inserted.zone_code, inserted.name, "
                "inserted.description, inserted.archived_at, inserted.row_ver "
                "WHERE zone_id = :id AND row_ver = :version"
            ),
            {"id": int(zone_id), "version": expected_version},
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def farm_view(row: dict) -> dict:
    return _farm_dto(row)


def zone_view(row: dict) -> dict:
    return _zone_dto(row)
