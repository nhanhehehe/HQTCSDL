import base64
import hmac
import json
from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.errors import WebError
from app.modules.auth import repository as auth_repo
from app.modules.auth.security import token_hash, valid_csrf
from app.modules.auth.service import invalid_session, storage_error
from app.modules.devices import repository as repo
from app.modules.devices.context import parse_manifest, read_context
from app.modules.devices.schemas import version


def fail(status, code):
    raise WebError(status, code, code)


class DeviceService:
    def __init__(self, database, settings):
        self.database = database
        self.settings = settings
        self.manifest = parse_manifest(settings.area_manifest)

    @contextmanager
    def transaction(self, cookie, csrf=None, write=False):
        digest = token_hash(cookie)
        if digest is None:
            raise invalid_session()
        if self.database.engine is None:
            raise storage_error()
        try:
            with (
                self.database.engine.connect().execution_options(
                    isolation_level="READ COMMITTED" if write else "SNAPSHOT"
                ) as connection,
                connection.begin(),
                Session(bind=connection) as session,
            ):
                if write:
                    session.execute(text("SET XACT_ABORT ON"))
                if auth_repo.session_guard(session, digest, lock=write) is None:
                    raise invalid_session()
                if write and (
                    self.settings.csrf_secret is None
                    or not valid_csrf(self.settings.csrf_secret.get_secret_value(), digest, csrf)
                ):
                    fail(403, "CSRF_INVALID")
                yield session
        except SQLAlchemyError:
            raise storage_error() from None

    def cursor(self, payload=None, token=None):
        secret = self.settings.cursor_secret
        if secret is None or not secret.get_secret_value():
            raise storage_error()
        key = secret.get_secret_value().encode()

        def encode(raw):
            return base64.urlsafe_b64encode(raw).decode().rstrip("=")

        if token is None:
            body = encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
            prefix = "v1." + body
            return prefix + "." + encode(hmac.digest(key, prefix.encode(), "sha256"))
        try:
            if len(token) > 4096:
                raise ValueError()
            v, body, signature = token.split(".")
            expected = encode(hmac.digest(key, (v + "." + body).encode(), "sha256"))
            if v != "v1" or not hmac.compare_digest(signature, expected):
                raise ValueError()
            raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
            if encode(raw) != body:
                raise ValueError()
            return json.loads(raw)
        except (ValueError, TypeError, UnicodeError):
            fail(400, "CURSOR_INVALID")

    def context(self, cookie):
        with self.transaction(cookie) as session:
            return read_context(session, self.manifest)

    def detail(self, cookie, kind, row_id):
        with self.transaction(cookie) as session:
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
            if not isinstance(decoded, dict):
                fail(400, "CURSOR_INVALID")
            boundary_raw = decoded.pop("boundary", None)
            from app.modules.devices.schemas import identity

            try:
                boundary = int(identity(boundary_raw))
            except (ValueError, TypeError):
                fail(400, "CURSOR_INVALID")
            if decoded != binding:
                fail(400, "CURSOR_INVALID")
        table, pk, business = repo.TABLES[kind]
        with self.transaction(cookie) as session:
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
            with self.transaction(cookie, csrf, write=True) as session:
                context = read_context(session, self.manifest, lock=True)
                if kind == "nodes":
                    target = next((z for z in context["zones"] if z["id"] == body.zone_id), None)
                    parent_column, parent = "zone_id", body.zone_id
                    business_code = body.device_id
                else:
                    target = context["farm"]
                    parent_column, parent = "farm_id", target["id"] if target else None
                    business_code = body.gateway_id
                if target is None:
                    fail(400, "CONFIGURATION_INVALID")
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
        with self.transaction(cookie, csrf, write=True) as session:
            if kind == "nodes":
                gateway_id = session.execute(
                    text("SELECT gateway_id FROM dbo.Node WHERE node_id=:id"), {"id": row_id}
                ).scalar()
                if gateway_id is not None:
                    session.execute(
                        text(
                            "SELECT gateway_id FROM dbo.Gateway WITH (UPDLOCK,HOLDLOCK) "
                            "WHERE gateway_id=:id"
                        ),
                        {"id": gateway_id},
                    ).all()
            row = (
                session.execute(
                    text(f"SELECT * FROM dbo.{table} WITH (UPDLOCK,HOLDLOCK) WHERE {pk}=:id"),
                    {"id": row_id},
                )
                .mappings()
                .first()
            )
            if row is None:
                fail(404, "NOT_FOUND")
            expected = version(body.expected_row_version)
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
                    {**changes, "id": row_id, "version": expected},
                ).scalar()
                if updated is None:
                    fail(409, "VERSION_CONFLICT")
            result = repo.profile(session, kind, row_id)
        return result
