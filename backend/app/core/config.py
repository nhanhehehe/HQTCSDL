import hmac
import json
import re
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy.engine import make_url

from app.core.schemas import WebModel

BACKEND_DIR = Path(__file__).resolve().parents[2]
_DEVICE_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    for key, val in pairs:
        if key in obj:
            raise ValueError(f"Duplicate JSON key: {key}")
        obj[key] = val
    return obj


def _reject_non_finite_json(token: str) -> None:
    raise ValueError(f"Non-finite JSON constant not allowed: {token}")


class NodeTimeProfile(WebModel):
    sampling_interval_seconds: int
    normal_delivery_budget_seconds: int

    @field_validator("sampling_interval_seconds")
    @classmethod
    def positive_sampling_interval(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("sampling_interval_seconds must be > 0")
        return value

    @field_validator("normal_delivery_budget_seconds")
    @classmethod
    def non_negative_delivery_budget(cls, value: int) -> int:
        if value < 0:
            raise ValueError("normal_delivery_budget_seconds must be >= 0")
        return value

    @property
    def stale_after_seconds(self) -> int:
        return self.sampling_interval_seconds + self.normal_delivery_budget_seconds


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
    cursor_secret: SecretStr | None = None
    dataset_generation: str = "1"
    ingestion_max_body_bytes: int = 65536
    report_secret: SecretStr | None = None
    report_snapshot_ttl_seconds: int = 900
    node_time_profiles: Annotated[dict[str, NodeTimeProfile], NoDecode] = Field(
        default_factory=dict
    )

    @field_validator("ingestion_max_body_bytes")
    @classmethod
    def positive_body_limit(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("HQ_INGESTION_MAX_BODY_BYTES must be positive")
        return value

    @field_validator("report_secret")
    @classmethod
    def valid_report_secret(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and len(value.get_secret_value().encode("utf-8")) < 32:
            raise ValueError("HQ_REPORT_SECRET must be at least 32 UTF-8 bytes")
        return value

    @field_validator("report_snapshot_ttl_seconds", mode="before")
    @classmethod
    def valid_report_ttl(cls, value: Any) -> int:
        if isinstance(value, bool):
            raise ValueError("HQ_REPORT_SNAPSHOT_TTL_SECONDS must be a positive integer")
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped.isascii() or not stripped.isdecimal():
                raise ValueError("HQ_REPORT_SNAPSHOT_TTL_SECONDS must be a positive integer")
            parsed = int(stripped)
        elif isinstance(value, int):
            parsed = value
        else:
            raise ValueError("HQ_REPORT_SNAPSHOT_TTL_SECONDS must be a positive integer")
        if parsed <= 0:
            raise ValueError("HQ_REPORT_SNAPSHOT_TTL_SECONDS must be positive")
        return parsed

    @field_validator("node_time_profiles", mode="before")
    @classmethod
    def valid_node_time_profiles(cls, value: Any) -> dict[str, NodeTimeProfile]:
        if value is None:
            return {}
        if isinstance(value, str):
            raw_text = value.strip()
            if not raw_text:
                return {}
            try:
                parsed = json.loads(
                    raw_text,
                    object_pairs_hook=_reject_duplicate_keys,
                    parse_constant=_reject_non_finite_json,
                )
            except (ValueError, TypeError) as exc:
                raise ValueError("HQ_NODE_TIME_PROFILES must be valid JSON") from exc
        else:
            parsed = value
        if not isinstance(parsed, dict):
            raise ValueError("HQ_NODE_TIME_PROFILES must be a JSON object mapped by device_id")
        validated: dict[str, NodeTimeProfile] = {}
        for key, item in parsed.items():
            if type(key) is not str or _DEVICE_CODE_RE.fullmatch(key) is None:
                raise ValueError("HQ_NODE_TIME_PROFILES keys must be valid device_id codes")
            validated[key] = (
                item if isinstance(item, NodeTimeProfile) else NodeTimeProfile.model_validate(item)
            )
        return validated

    @model_validator(mode="after")
    def secure_https_cookie(self):
        if self.web_origin.startswith("https://") and not self.cookie_secure:
            raise ValueError("HTTPS requires HQ_COOKIE_SECURE=true")
        return self

    @model_validator(mode="after")
    def distinct_report_secret(self):
        if self.report_secret is None:
            return self
        report_bytes = self.report_secret.get_secret_value().encode("utf-8")
        for label, other in (
            ("HQ_CSRF_SECRET", self.csrf_secret),
            ("HQ_CURSOR_SECRET", self.cursor_secret),
        ):
            if other is not None and hmac.compare_digest(
                report_bytes, other.get_secret_value().encode("utf-8")
            ):
                raise ValueError(f"HQ_REPORT_SECRET must be distinct from {label}")
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
