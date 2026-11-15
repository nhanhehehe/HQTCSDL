"""Web codecs only. ESP ingestion keeps its separate numeric wire contract."""

import base64
import binascii
import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, PlainSerializer

BIGINT_MAX = 2**63 - 1


class WebModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)


def _bigint_text(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"0|[1-9][0-9]{0,18}", value):
        raise ValueError("Expected a canonical unsigned BIGINT string")
    if int(value) > BIGINT_MAX:
        raise ValueError("BIGINT out of range")
    return value


def _internal_id(value: object) -> str:
    result = _bigint_text(value)
    if result == "0":
        raise ValueError("Internal ID must be positive")
    return result


BigIntText = Annotated[str, BeforeValidator(_bigint_text)]
InternalId = Annotated[str, BeforeValidator(_internal_id)]
PageSize = Annotated[int, Field(strict=True, ge=1, le=200)]


def web_integer(value: int) -> str:
    """Explicit SQL integer -> web text conversion; never accept float/bool."""
    if type(value) is not int or not 0 <= value <= BIGINT_MAX:
        raise ValueError("Expected an unsigned SQL BIGINT")
    return str(value)


def _decimal_text(value: object) -> Decimal:
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, str) and re.fullmatch(r"-?[0-9]+(?:\.[0-9]+)?", value):
        result = Decimal(value)
    else:
        raise ValueError("Expected Decimal or decimal text, never float")
    if not result.is_finite():
        raise ValueError("Decimal must be finite")
    return result


# Response/explicit decimal-text fields only. Metric domain/scale belongs to its module.
DecimalText = Annotated[
    Decimal,
    BeforeValidator(_decimal_text, json_schema_input_type=str),
    PlainSerializer(lambda value: format(value, "f"), return_type=str, when_used="json"),
]


def _rowversion(value: object) -> bytes:
    if isinstance(value, bytes):
        raw = value
    elif isinstance(value, str):
        try:
            raw = base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("Invalid rowversion base64") from exc
        if base64.b64encode(raw).decode("ascii") != value:
            raise ValueError("Noncanonical rowversion base64")
    else:
        raise ValueError("Expected rowversion bytes or base64 text")
    if len(raw) != 8:
        raise ValueError("Rowversion must contain exactly 8 bytes")
    return raw


RowVersion = Annotated[
    bytes,
    BeforeValidator(_rowversion, json_schema_input_type=str),
    PlainSerializer(
        lambda value: base64.b64encode(value).decode("ascii"),
        return_type=str,
        when_used="json",
    ),
]


def _timestamp(value: object) -> datetime:
    if isinstance(value, str):
        if not re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
            r"(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})",
            value,
        ):
            raise ValueError("Expected RFC3339 timestamp with timezone")
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("Timezone-aware timestamp required")
    try:
        return value.astimezone(UTC)
    except OverflowError as exc:
        raise ValueError("UTC timestamp out of supported range") from exc


# Keep inbound range precision; only BackendTime serializes at millisecond precision.
RangeTime = Annotated[datetime, BeforeValidator(_timestamp, json_schema_input_type=str)]
BackendTime = Annotated[
    datetime,
    BeforeValidator(_timestamp, json_schema_input_type=str),
    PlainSerializer(
        lambda value: value.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        return_type=str,
        when_used="json",
    ),
]


def page_size_query(value: str | None = None) -> int:
    """Query parameters are text; validate deliberately instead of coercing JSON."""
    if value is None:
        return 50
    if not re.fullmatch(r"[0-9]{1,3}", value) or not 1 <= int(value) <= 200:
        raise ValueError("page_size must be an integer from 1 to 200")
    return int(value)
