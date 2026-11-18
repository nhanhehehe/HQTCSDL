import unicodedata

from pydantic import field_validator

from app.core.schemas import WebModel
from app.modules.devices.schemas import sized, version


def area_code(value: str) -> str:
    """Validate Farm/Zone business code: 1..128 UTF-16 units, no edge whitespace/control chars."""
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("Invalid area code")
    sized(value, 128)
    if any(unicodedata.category(c).startswith("C") for c in value):
        raise ValueError("Invalid area code")
    return value


def area_name(value: str) -> str:
    trimmed = value.strip()
    if not trimmed:
        raise ValueError("Name required")
    return sized(trimmed, 200)


def area_description(value: str | None) -> str | None:
    return sized(value, 1000) if value is not None else None


class AreaCreate(WebModel):
    code: str
    name: str
    description: str | None = None

    _code = field_validator("code")(area_code)
    _name = field_validator("name")(area_name)
    _description = field_validator("description")(area_description)


class AreaPatch(WebModel):
    name: str | None = None
    description: str | None = None
    expected_row_version: str

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("Name cannot be null")
        return area_name(value)

    _description = field_validator("description")(area_description)

    @field_validator("expected_row_version")
    @classmethod
    def valid_version(cls, value: str) -> str:
        version(value)
        return value


class AreaLifecycleAction(WebModel):
    expected_row_version: str

    @field_validator("expected_row_version")
    @classmethod
    def valid_version(cls, value: str) -> str:
        version(value)
        return value
