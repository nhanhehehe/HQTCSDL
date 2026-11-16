"""Explicit operator command; never called by API startup or migration."""

import argparse
from getpass import getpass

from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.modules.auth import repository as repo
from app.modules.auth.schemas import LoginRequest
from app.modules.auth.security import password_hasher


def main():
    parser = argparse.ArgumentParser(description="Create an ADMIN without replacing existing users")
    parser.add_argument("username")
    parser.add_argument(
        "--database", required=True, help="Expected DB_NAME(), checked before writing"
    )
    args = parser.parse_args()
    settings = Settings()
    if settings.migration_database_url is None:
        parser.exit(1, "Configure HQ_MIGRATION_DATABASE_URL.\n")
    password = getpass("Password: ")
    if password != getpass("Confirm password: "):
        parser.exit(1, "Passwords do not match.\n")
    try:
        LoginRequest(username=args.username, password=password)
    except ValueError:
        parser.exit(1, "Username/password length is invalid.\n")
    encoded = password_hasher.hash(password)
    engine = create_engine(settings.migration_database_url.get_secret_value(), hide_parameters=True)
    try:
        with Session(engine) as session, session.begin():
            identity = session.execute(text("SELECT DB_NAME(), SUSER_SNAME()")).one()
            print(f"Database: {identity[0]}; login: {identity[1]}")
            if identity[0] != args.database:
                parser.exit(1, "Database does not match --database; no account created.\n")
            if repo.account(session, args.username, lock=True):
                parser.exit(1, "Username exists; password was not changed.\n")
            repo.create_admin(session, args.username, encoded)
        print("Admin created.")
    except DBAPIError:
        parser.exit(1, "Storage operation failed; account creation is not confirmed.\n")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
