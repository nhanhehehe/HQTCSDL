from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import Settings


class Base(DeclarativeBase):
    pass


class Database:
    def __init__(self, settings: Settings):
        self.engine: Engine | None = None
        self.sessions: sessionmaker[Session] | None = None
        if settings.database_url is not None:
            self.engine = create_engine(
                settings.database_url.get_secret_value(),
                pool_pre_ping=True,
                hide_parameters=True,
                connect_args={"timeout": 3},
            )
            self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def probe(self) -> None:
        if self.engine is None:
            raise RuntimeError("SQL Server is not configured")
        with self.engine.connect() as connection:
            connection.execute(text("SELECT 1"))

    def dispose(self) -> None:
        if self.engine is not None:
            self.engine.dispose()


# Services own transaction boundaries: with db.sessions.begin() as session: ...
# Routes and repositories do not commit independently. Each background thread must
# open its own session. No create_all() or schema changes at application startup.
