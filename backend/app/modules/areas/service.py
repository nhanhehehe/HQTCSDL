from sqlalchemy.exc import SQLAlchemyError

from app.core.web import fail, sign_cursor, verify_cursor, web_transaction
from app.modules.areas import repository as repo
from app.modules.auth.service import storage_error
from app.modules.devices.schemas import identity, version


class AreaService:
    def __init__(self, database, settings):
        self.database = database
        self.settings = settings

    def _decode_boundary(self, cursor: str | None, binding: dict) -> int:
        if cursor is None:
            return 0
        decoded = verify_cursor(self.settings, cursor)
        boundary_raw = decoded.pop("boundary", None)
        try:
            boundary = int(identity(boundary_raw))
        except (ValueError, TypeError):
            fail(400, "CURSOR_INVALID")
        if decoded != binding:
            fail(400, "CURSOR_INVALID")
        return boundary

    def list_farms(
        self,
        cookie: str | None,
        *,
        status: str,
        code: str | None,
        page_size: int,
        cursor: str | None,
    ) -> dict:
        binding = {
            "purpose": "area-cursor",
            "route": "farms",
            "status": status,
            "code": code,
            "page_size": page_size,
            "order": "id-asc",
            "generation": self.settings.dataset_generation,
        }
        boundary = self._decode_boundary(cursor, binding)
        with web_transaction(self.database, self.settings, cookie) as session:
            rows = repo.list_farms(
                session, status=status, code=code, boundary=boundary, limit=page_size + 1
            )
            items = [repo.farm_view(r) for r in rows[:page_size]]
            next_cursor = (
                sign_cursor(
                    self.settings, {**binding, "boundary": str(rows[page_size - 1]["farm_id"])}
                )
                if len(rows) > page_size
                else None
            )
            return {"items": items, "next_cursor": next_cursor}

    def get_farm(self, cookie: str | None, farm_id: str) -> dict:
        with web_transaction(self.database, self.settings, cookie) as session:
            row = repo.get_farm(session, farm_id)
            if row is None:
                fail(404, "NOT_FOUND")
            return repo.farm_view(row)

    def create_farm(self, cookie: str | None, csrf: str | None, body) -> dict:
        try:
            with web_transaction(self.database, self.settings, cookie, csrf, write=True) as session:
                try:
                    row = repo.insert_farm(
                        session, code=body.code, name=body.name, description=body.description
                    )
                except SQLAlchemyError as exc:
                    message = str(getattr(exc, "orig", ""))
                    if ("2601" in message or "2627" in message) and "UQ_Farm_farm_code" in message:
                        fail(409, "CODE_EXISTS")
                    raise
                return repo.farm_view(row)
        except SQLAlchemyError:
            raise storage_error() from None

    def patch_farm(self, cookie: str | None, csrf: str | None, farm_id: str, body) -> dict:
        expected = version(body.expected_row_version)
        try:
            with web_transaction(self.database, self.settings, cookie, csrf, write=True) as session:
                row = repo.get_farm(session, farm_id, lock=True)
                if row is None:
                    fail(404, "NOT_FOUND")
                if bytes(row["row_ver"]) != expected:
                    fail(409, "VERSION_CONFLICT")
                changes = {
                    field: getattr(body, field)
                    for field in ("name", "description")
                    if field in body.model_fields_set and getattr(body, field) != row[field]
                }
                if changes:
                    updated = repo.update_farm_metadata(
                        session, farm_id=farm_id, expected_version=expected, changes=changes
                    )
                    if updated is None:
                        fail(409, "VERSION_CONFLICT")
                    row = updated
                return repo.farm_view(row)
        except SQLAlchemyError:
            raise storage_error() from None

    def list_zones(
        self,
        cookie: str | None,
        *,
        farm_id: str,
        status: str,
        code: str | None,
        page_size: int,
        cursor: str | None,
    ) -> dict:
        binding = {
            "purpose": "area-cursor",
            "route": "farm-zones",
            "farm_id": farm_id,
            "status": status,
            "code": code,
            "page_size": page_size,
            "order": "id-asc",
            "generation": self.settings.dataset_generation,
        }
        boundary = self._decode_boundary(cursor, binding)
        with web_transaction(self.database, self.settings, cookie) as session:
            if repo.get_farm(session, farm_id) is None:
                fail(404, "NOT_FOUND")
            rows = repo.list_zones(
                session,
                farm_id=farm_id,
                status=status,
                code=code,
                boundary=boundary,
                limit=page_size + 1,
            )
            items = [repo.zone_view(r) for r in rows[:page_size]]
            next_cursor = (
                sign_cursor(
                    self.settings, {**binding, "boundary": str(rows[page_size - 1]["zone_id"])}
                )
                if len(rows) > page_size
                else None
            )
            return {"items": items, "next_cursor": next_cursor}

    def get_zone(self, cookie: str | None, zone_id: str) -> dict:
        with web_transaction(self.database, self.settings, cookie) as session:
            row = repo.get_zone(session, zone_id)
            if row is None:
                fail(404, "NOT_FOUND")
            return repo.zone_view(row)

    def create_zone(self, cookie: str | None, csrf: str | None, farm_id: str, body) -> dict:
        try:
            with web_transaction(self.database, self.settings, cookie, csrf, write=True) as session:
                farm = repo.get_farm(session, farm_id, lock=True)
                if farm is None:
                    fail(404, "NOT_FOUND")
                if farm["archived_at"] is not None:
                    fail(409, "AREA_ARCHIVED", "Không thể tạo Zone trong Farm đã lưu trữ")
                try:
                    row = repo.insert_zone(
                        session,
                        farm_id=farm_id,
                        code=body.code,
                        name=body.name,
                        description=body.description,
                    )
                except SQLAlchemyError as exc:
                    message = str(getattr(exc, "orig", ""))
                    if ("2601" in message or "2627" in message) and "UQ_Zone_zone_code" in message:
                        fail(409, "CODE_EXISTS")
                    raise
                return repo.zone_view(row)
        except SQLAlchemyError:
            raise storage_error() from None

    def patch_zone(self, cookie: str | None, csrf: str | None, zone_id: str, body) -> dict:
        expected = version(body.expected_row_version)
        try:
            with web_transaction(self.database, self.settings, cookie, csrf, write=True) as session:
                farm_id = repo.get_zone_farm_id(session, zone_id)
                if farm_id is None:
                    fail(404, "NOT_FOUND")
                # Canonical lock order: Session -> Farm -> Zone.
                repo.get_farm(session, str(farm_id), lock=True)
                row = repo.get_zone(session, zone_id, lock=True)
                if row is None:
                    fail(404, "NOT_FOUND")
                if bytes(row["row_ver"]) != expected:
                    fail(409, "VERSION_CONFLICT")
                changes = {
                    field: getattr(body, field)
                    for field in ("name", "description")
                    if field in body.model_fields_set and getattr(body, field) != row[field]
                }
                if changes:
                    updated = repo.update_zone_metadata(
                        session, zone_id=zone_id, expected_version=expected, changes=changes
                    )
                    if updated is None:
                        fail(409, "VERSION_CONFLICT")
                    row = updated
                return repo.zone_view(row)
        except SQLAlchemyError:
            raise storage_error() from None

    def archive_farm(self, cookie: str | None, csrf: str | None, farm_id: str, body) -> dict:
        expected = version(body.expected_row_version)
        try:
            with web_transaction(self.database, self.settings, cookie, csrf, write=True) as session:
                row = repo.get_farm(session, farm_id, lock=True)
                if row is None:
                    fail(404, "NOT_FOUND")
                if bytes(row["row_ver"]) != expected:
                    fail(409, "VERSION_CONFLICT")
                if row["archived_at"] is not None:
                    return repo.farm_view(row)
                zones = repo.lock_farm_zones(session, farm_id)
                if any(z["archived_at"] is None for z in zones):
                    fail(
                        409,
                        "AREA_IN_USE",
                        "Cần lưu trữ tất cả Zone trong Farm trước khi lưu trữ Farm",
                    )
                gateways = repo.lock_farm_gateways(session, farm_id)
                if any(bool(g["receiving_enabled"]) for g in gateways):
                    fail(
                        409,
                        "AREA_IN_USE",
                        "Cần tắt quyền nhận của tất cả Gateway trong Farm "
                        "trước khi lưu trữ (FL-04)",
                    )
                updated = repo.set_farm_archived(
                    session, farm_id=farm_id, expected_version=expected, archive=True
                )
                if updated is None:
                    fail(409, "VERSION_CONFLICT")
                return repo.farm_view(updated)
        except SQLAlchemyError:
            raise storage_error() from None

    def restore_farm(self, cookie: str | None, csrf: str | None, farm_id: str, body) -> dict:
        expected = version(body.expected_row_version)
        try:
            with web_transaction(self.database, self.settings, cookie, csrf, write=True) as session:
                row = repo.get_farm(session, farm_id, lock=True)
                if row is None:
                    fail(404, "NOT_FOUND")
                if bytes(row["row_ver"]) != expected:
                    fail(409, "VERSION_CONFLICT")
                if row["archived_at"] is None:
                    return repo.farm_view(row)
                updated = repo.set_farm_archived(
                    session, farm_id=farm_id, expected_version=expected, archive=False
                )
                if updated is None:
                    fail(409, "VERSION_CONFLICT")
                return repo.farm_view(updated)
        except SQLAlchemyError:
            raise storage_error() from None

    def archive_zone(self, cookie: str | None, csrf: str | None, zone_id: str, body) -> dict:
        expected = version(body.expected_row_version)
        try:
            with web_transaction(self.database, self.settings, cookie, csrf, write=True) as session:
                farm_id = repo.get_zone_farm_id(session, zone_id)
                if farm_id is None:
                    fail(404, "NOT_FOUND")
                repo.get_farm(session, str(farm_id), lock=True)
                row = repo.get_zone(session, zone_id, lock=True)
                if row is None:
                    fail(404, "NOT_FOUND")
                if bytes(row["row_ver"]) != expected:
                    fail(409, "VERSION_CONFLICT")
                if row["archived_at"] is not None:
                    return repo.zone_view(row)
                nodes = repo.lock_zone_nodes(session, zone_id)
                if any(bool(n["receiving_enabled"]) for n in nodes):
                    fail(
                        409,
                        "AREA_IN_USE",
                        "Cần tắt quyền nhận của tất cả Node trong Zone trước khi lưu trữ (FL-04)",
                    )
                updated = repo.set_zone_archived(
                    session, zone_id=zone_id, expected_version=expected, archive=True
                )
                if updated is None:
                    fail(409, "VERSION_CONFLICT")
                return repo.zone_view(updated)
        except SQLAlchemyError:
            raise storage_error() from None

    def restore_zone(self, cookie: str | None, csrf: str | None, zone_id: str, body) -> dict:
        expected = version(body.expected_row_version)
        try:
            with web_transaction(self.database, self.settings, cookie, csrf, write=True) as session:
                farm_id = repo.get_zone_farm_id(session, zone_id)
                if farm_id is None:
                    fail(404, "NOT_FOUND")
                farm = repo.get_farm(session, str(farm_id), lock=True)
                row = repo.get_zone(session, zone_id, lock=True)
                if farm is None or row is None:
                    fail(404, "NOT_FOUND")
                if bytes(row["row_ver"]) != expected:
                    fail(409, "VERSION_CONFLICT")
                if farm["archived_at"] is not None:
                    fail(409, "AREA_ARCHIVED", "Cần khôi phục Farm cha trước khi khôi phục Zone")
                if row["archived_at"] is None:
                    return repo.zone_view(row)
                updated = repo.set_zone_archived(
                    session, zone_id=zone_id, expected_version=expected, archive=False
                )
                if updated is None:
                    fail(409, "VERSION_CONFLICT")
                return repo.zone_view(updated)
        except SQLAlchemyError:
            raise storage_error() from None
