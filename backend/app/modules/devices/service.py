from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.web import fail, sign_cursor, verify_cursor, web_transaction
from app.modules.areas import repository as areas_repo
from app.modules.auth.service import storage_error
from app.modules.devices import repository as repo
from app.modules.devices.context import read_context
from app.modules.devices.schemas import identity, version


class DeviceService:
    def __init__(self, database, settings):
        self.database = database
        self.settings = settings

    def cursor(self, payload=None, token=None):
        if token is None:
            return sign_cursor(self.settings, payload)
        return verify_cursor(self.settings, token)

    def context(self, cookie, *, farm_id: str | None = None):
        with web_transaction(self.database, self.settings, cookie) as session:
            return read_context(session, farm_id=farm_id)

    def detail(self, cookie, kind, row_id):
        with web_transaction(self.database, self.settings, cookie) as session:
            result = repo.profile(session, kind, row_id)
            if result is None:
                fail(404, "NOT_FOUND")
            return result

    def listing(self, cookie, kind, size, exact, cursor):
        binding = {
            "purpose": "profile-cursor",
            "route": kind,
            "filter": exact,
            "page_size": size,
            "order": "id-asc",
            "generation": self.settings.dataset_generation,
        }
        boundary = 0
        if cursor is not None:
            decoded = self.cursor(token=cursor)
            boundary_raw = decoded.pop("boundary", None)
            try:
                boundary = int(identity(boundary_raw))
            except (ValueError, TypeError):
                fail(400, "CURSOR_INVALID")
            if decoded != binding:
                fail(400, "CURSOR_INVALID")
        table, pk, business = repo.TABLES[kind]
        with web_transaction(self.database, self.settings, cookie) as session:
            rows = (
                session.execute(
                    text(
                        f"SELECT TOP (:limit) {pk} FROM dbo.{table} WHERE {pk}>:boundary "
                        + (
                            f"AND {business}=:code "
                            + f"AND DATALENGTH({business})=DATALENGTH(CAST(:code AS VARCHAR(128))) "
                            if exact is not None
                            else ""
                        )
                        + f"ORDER BY {pk}"
                    ),
                    {"limit": size + 1, "boundary": boundary, "code": exact},
                )
                .scalars()
                .all()
            )
            items = [repo.profile(session, kind, row_id) for row_id in rows[:size]]
            next_cursor = (
                self.cursor({**binding, "boundary": str(rows[size - 1])})
                if len(rows) > size
                else None
            )
            return {"items": items, "next_cursor": next_cursor}

    def create(self, cookie, csrf, kind, body):
        table, pk, business = repo.TABLES[kind]
        try:
            with web_transaction(
                cookie=cookie, database=self.database, settings=self.settings, csrf=csrf, write=True
            ) as session:
                if kind == "nodes":
                    farm_id = areas_repo.get_zone_farm_id(session, body.zone_id)
                    if farm_id is None:
                        fail(404, "NOT_FOUND")
                    farm = areas_repo.get_farm(session, str(farm_id), lock=True)
                    zone = areas_repo.get_zone(session, body.zone_id, lock=True)
                    if farm is None or zone is None or int(zone["farm_id"]) != int(farm_id):
                        fail(404, "NOT_FOUND")
                    if farm["archived_at"] is not None or zone["archived_at"] is not None:
                        fail(409, "AREA_ARCHIVED", "Không thể tạo Node trong khu vực đã lưu trữ")
                    parent_column, parent = "zone_id", int(body.zone_id)
                    business_code = body.device_id
                else:
                    farm = areas_repo.get_farm(session, body.farm_id, lock=True)
                    if farm is None:
                        fail(404, "NOT_FOUND")
                    if farm["archived_at"] is not None:
                        fail(409, "AREA_ARCHIVED", "Không thể tạo Gateway trong Farm đã lưu trữ")
                    parent_column, parent = "farm_id", int(body.farm_id)
                    business_code = body.gateway_id
                try:
                    row_id = session.execute(
                        text(
                            f"INSERT INTO dbo.{table} ({business},{parent_column},name,notes) "
                            f"OUTPUT inserted.{pk} VALUES (:code,:parent,:name,:notes)"
                        ),
                        {
                            "code": business_code,
                            "parent": parent,
                            "name": body.name,
                            "notes": body.notes,
                        },
                    ).scalar_one()
                except SQLAlchemyError as exc:
                    # Only the business-code INSERT can map its unique violation.
                    message = str(getattr(exc, "orig", ""))
                    if (
                        "2601" in message or "2627" in message
                    ) and f"UQ_{table}_{business}" in message:
                        fail(409, "CODE_EXISTS")
                    raise
                if kind == "nodes":
                    session.execute(
                        text("INSERT INTO dbo.NodeRuntime(node_id) VALUES (:id)"), {"id": row_id}
                    )
                    session.execute(
                        text(
                            "INSERT INTO dbo.EvaluationState "
                            "(node_id,desired_generation,completed_generation,"
                            "full_replay_required) "
                            "VALUES (:id,0,0,0)"
                        ),
                        {"id": row_id},
                    )
                result = repo.profile(session, kind, row_id)
            return result
        except SQLAlchemyError:
            raise storage_error() from None

    def patch(self, cookie, csrf, kind, row_id, body):
        table, pk, _ = repo.TABLES[kind]
        expected = version(body.expected_row_version)
        try:
            with web_transaction(self.database, self.settings, cookie, csrf, write=True) as session:
                # Canonical lock order: Session -> Farm -> Zone -> Gateway -> Node.
                if kind == "nodes":
                    parents = repo.resolve_node_parents(session, row_id)
                    if parents is None:
                        fail(404, "NOT_FOUND")
                    areas_repo.get_farm(session, str(parents["farm_id"]), lock=True)
                    areas_repo.get_zone(session, str(parents["zone_id"]), lock=True)
                    if parents["gateway_id"] is not None:
                        repo.lock_gateway(session, parents["gateway_id"])
                    row = repo.lock_node(session, row_id)
                else:
                    farm_id = repo.resolve_gateway_farm_id(session, row_id)
                    if farm_id is None:
                        fail(404, "NOT_FOUND")
                    areas_repo.get_farm(session, str(farm_id), lock=True)
                    row = repo.lock_gateway(session, row_id)
                if row is None:
                    fail(404, "NOT_FOUND")
                if bytes(row["row_ver"]) != expected:
                    fail(409, "VERSION_CONFLICT")
                changes = {
                    field: getattr(body, field)
                    for field in ("name", "notes")
                    if field in body.model_fields_set and getattr(body, field) != row[field]
                }
                if changes:
                    assignments = ",".join(f"{field}=:{field}" for field in changes)
                    updated = session.execute(
                        text(
                            f"UPDATE dbo.{table} SET {assignments} "
                            f"OUTPUT inserted.{pk} WHERE {pk}=:id AND row_ver=:version"
                        ),
                        {**changes, "id": int(row_id), "version": expected},
                    ).scalar()
                    if updated is None:
                        fail(409, "VERSION_CONFLICT")
                result = repo.profile(session, kind, row_id)
            return result
        except SQLAlchemyError:
            raise storage_error() from None
