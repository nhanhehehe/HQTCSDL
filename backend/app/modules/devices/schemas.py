import base64
import re

from pydantic import field_validator

from app.core.schemas import WebModel


def identity(value: str) -> str:
    if not re.fullmatch(r"[1-9][0-9]{0,18}", value) or int(value) > 9223372036854775807:
        raise ValueError("Invalid BIGINT identity")
    return value


def version(value: str) -> bytes:
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, TypeError):
        raise ValueError("Invalid row version") from None
    if len(raw) != 8 or base64.b64encode(raw).decode() != value:
        raise ValueError("Invalid row version")
    return raw


def sized(value: str, limit: int) -> str:
    if len(value.encode("utf-16-le")) // 2 > limit:
        raise ValueError("Text too long")
    return value


class Editable(WebModel):
    name: str
    notes: str | None = None

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Name required")
        return sized(value, 200)

    @field_validator("notes")
    @classmethod
    def valid_notes(cls, value: str | None) -> str | None:
        return sized(value, 1000) if value is not None else None


def code(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise ValueError("Invalid device code")
    return value


class NodeCreate(Editable):
    device_id: str
    zone_id: str
    _device = field_validator("device_id")(code)
    _zone = field_validator("zone_id")(identity)


class GatewayCreate(Editable):
    gateway_id: str
    farm_id: str
    _gateway = field_validator("gateway_id")(code)
    _farm = field_validator("farm_id")(identity)


class ProfilePatch(WebModel):
    name: str | None = None
    notes: str | None = None
    expected_row_version: str

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("Name cannot be null")
        return Editable.valid_name(value)

    @field_validator("notes")
    @classmethod
    def valid_notes(cls, value: str | None) -> str | None:
        return Editable.valid_notes(value)

    @field_validator("expected_row_version")
    @classmethod
    def valid_version(cls, value: str) -> str:
        version(value)
        return value


class EmptyBody(WebModel):
    pass
