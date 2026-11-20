from sqlalchemy import text
from sqlalchemy.orm import Session


def account(session: Session, username: str, *, lock: bool = False):
    hint = " WITH (UPDLOCK, HOLDLOCK)" if lock else ""
    return (
        session.execute(
            text(
                "SELECT admin_id, username, password_hash, role_code, is_enabled "
                f"FROM dbo.AdminAccount{hint} WHERE username = :username "
                "AND DATALENGTH(username) = DATALENGTH(CAST(:username AS NVARCHAR(128)))"
            ),
            {"username": username},
        )
        .mappings()
        .first()
    )


def session_guard(session: Session, digest: bytes, *, lock: bool = False):
    # Call inside the mutation transaction and retain it through commit/rollback.
    hint = " WITH (UPDLOCK, HOLDLOCK)" if lock else ""
    return (
        session.execute(
            text(
                "SELECT s.session_id, s.admin_id, a.username, s.expires_at, "
                "CAST(SYSUTCDATETIME() AS DATETIME2(3)) AS db_now "
                f"FROM dbo.AdminSession AS s{hint} "
                "JOIN dbo.AdminAccount AS a WITH (HOLDLOCK) ON a.admin_id = s.admin_id "
                "WHERE s.token_hash = :digest AND s.revoked_at IS NULL "
                "AND s.expires_at > SYSUTCDATETIME() AND a.is_enabled = 1 "
                "AND a.role_code = 'ADMIN'"
            ),
            {"digest": digest},
        )
        .mappings()
        .first()
    )


def revoke(session: Session, digest: bytes):
    session.execute(
        text(
            "UPDATE dbo.AdminSession SET revoked_at = CAST(SYSUTCDATETIME() AS DATETIME2(3)) "
            "WHERE token_hash = :digest AND revoked_at IS NULL"
        ),
        {"digest": digest},
    )


def create_session(session: Session, admin_id: int, digest: bytes):
    return (
        session.execute(
            text(
                "DECLARE @now DATETIME2(3) = SYSUTCDATETIME(); "
                "INSERT INTO dbo.AdminSession (admin_id, token_hash, created_at, expires_at) "
                "OUTPUT inserted.session_id, inserted.admin_id, inserted.expires_at, "
                "@now AS db_now "
                "VALUES (:admin_id, :digest, @now, DATEADD(hour, 2, @now));"
            ),
            {"admin_id": admin_id, "digest": digest},
        )
        .mappings()
        .one()
    )


def create_admin(session: Session, username: str, encoded: str):
    session.execute(
        text(
            "INSERT INTO dbo.AdminAccount "
            "(username, password_hash, role_code, is_enabled, created_at) "
            "VALUES (:username, :encoded, 'ADMIN', 1, SYSUTCDATETIME())"
        ),
        {"username": username, "encoded": encoded},
    )
