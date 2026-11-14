from alembic import context
from sqlalchemy import create_engine

from app.core.config import Settings
from app.core.db import Base

# Import reviewed module models here when their migrations are added.
target_metadata = Base.metadata
settings = Settings()
secret = settings.migration_database_url or settings.database_url
if secret is None:
    raise RuntimeError("Configure HQ_MIGRATION_DATABASE_URL before running Alembic")
url = secret.get_secret_value()

if context.is_offline_mode():
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(url, hide_parameters=True, connect_args={"timeout": 3})
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()
