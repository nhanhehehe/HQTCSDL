from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator, model_validator
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
    auth_test_database_url: SecretStr | None = None
    web_origin: str = "http://127.0.0.1:5173"
    csrf_secret: SecretStr | None = None
    cookie_secure: bool = False

    @model_validator(mode="after")
    def secure_https_cookie(self):
        if self.web_origin.startswith("https://") and not self.cookie_secure:
            raise ValueError("HTTPS requires HQ_COOKIE_SECURE=true")
        return self

    @field_validator("web_origin")
    @classmethod
    def origin_only(cls, value: str) -> str:
        from urllib.parse import urlsplit

        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Configure an exact HTTP(S) origin without a path")
        return value

    @field_validator("database_url", "migration_database_url", "auth_test_database_url")
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
