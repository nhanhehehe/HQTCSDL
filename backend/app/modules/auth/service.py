import hashlib
import secrets
from datetime import UTC

from sqlalchemy.exc import DisconnectionError, InterfaceError, OperationalError, TimeoutError

from app.core.errors import WebError
from app.modules.auth import repository as repo
from app.modules.auth.schemas import Admin, SessionResponse
from app.modules.auth.security import (
    DUMMY_HASH,
    csrf,
    encode,
    token_hash,
    valid_csrf,
    verify_password,
)

STORAGE_ERRORS = (DisconnectionError, InterfaceError, OperationalError, TimeoutError)


def invalid_session():
    return WebError(401, "SESSION_INVALID", "Phiên không hợp lệ hoặc đã hết hạn")


def storage_error():
    return WebError(503, "STORAGE_UNAVAILABLE", "Chưa xác minh được kết quả lưu trữ")


class AuthService:
    def __init__(self, database, settings):
        self.database = database
        self.settings = settings

    def secret(self):
        if self.settings.csrf_secret is None or not self.settings.csrf_secret.get_secret_value():
            raise storage_error()
        return self.settings.csrf_secret.get_secret_value()

    def sessions(self):
        if self.database.sessions is None:
            raise storage_error()
        return self.database.sessions

    def dto(self, row, digest):
        expires = row["expires_at"].replace(tzinfo=UTC)
        return SessionResponse(
            admin=Admin(id=str(row["admin_id"]), username=row["username"]),
            expires_at=expires.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            csrf_token=csrf(self.secret(), digest),
        )

    def login(self, username, password, old_cookie):
        self.secret()
        factory = self.sessions()
        try:
            with factory() as session:
                candidate = repo.account(session, username)
            verified = verify_password(
                candidate["password_hash"] if candidate else DUMMY_HASH, password
            )
            bad = WebError(401, "INVALID_CREDENTIALS", "Tên đăng nhập hoặc mật khẩu không đúng")
            if not verified or not candidate:
                raise bad
            raw = secrets.token_bytes(32)
            digest = hashlib.sha256(raw).digest()
            with factory.begin() as session:
                # Session guard precedes account locks, matching logout lock ordering.
                old_digest = token_hash(old_cookie)
                old = repo.session_guard(session, old_digest, lock=True) if old_digest else None
                current = repo.account(session, username, lock=True)
                if (
                    not current
                    or not current["is_enabled"]
                    or current["role_code"] != "ADMIN"
                    or current["password_hash"] != candidate["password_hash"]
                ):
                    raise bad
                if old:
                    repo.revoke(session, old_digest)
                created = dict(repo.create_session(session, current["admin_id"], digest))
                created["username"] = current["username"]
                dto = self.dto(created, digest)
            remaining = int((created["expires_at"] - created["db_now"]).total_seconds())
            return dto, encode(raw), remaining
        except STORAGE_ERRORS:
            raise storage_error() from None

    def session(self, cookie):
        digest = token_hash(cookie)
        if digest is None:
            raise invalid_session()
        try:
            with self.sessions()() as session:
                row = repo.session_guard(session, digest)
                if row is None:
                    raise invalid_session()
                return self.dto(row, digest)
        except STORAGE_ERRORS:
            raise storage_error() from None

    def logout(self, cookie, csrf_token):
        digest = token_hash(cookie)
        if digest is None:
            return
        try:
            with self.sessions().begin() as session:
                row = repo.session_guard(session, digest, lock=True)
                if row is not None:
                    if not valid_csrf(self.secret(), digest, csrf_token):
                        raise WebError(403, "CSRF_INVALID", "Yêu cầu xác thực không hợp lệ")
                    repo.revoke(session, digest)
        except STORAGE_ERRORS:
            raise storage_error() from None
