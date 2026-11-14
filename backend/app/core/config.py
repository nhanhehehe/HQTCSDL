from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HQ_", env_file=BACKEND_DIR / ".env", extra="forbid", hide_input_in_errors=True
    )
    env: Literal["dev", "test", "demo"] = "dev"
    database_url: SecretStr | None = None
    migration_database_url: SecretStr | None = None

    @field_validator("database_url", "migration_database_url")
    @classmethod
    def sql_server_only(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            try:
                url = make_url(value.get_secret_value())
                if url.drivername == "mssql+pyodbc" and url.database:
                    return value
            except Exception:
                pass
            raise ValueError("Configure a mssql+pyodbc URL with an explicit database")
        return value
