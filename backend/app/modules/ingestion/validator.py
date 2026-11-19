import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from app.core.errors import IngestionError

MAX_INT64 = 9_223_372_036_854_775_807
MAX_EPOCH_SEC = 253_402_300_799
D06_TOLERANCE_SECONDS = 120
CODE_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

ENVELOPE_KEYS = frozenset({"gateway_id", "records"})
RECORD_REQUIRED_KEYS = frozenset(
    {"device_id", "boot_id", "seq", "t_node", "time_status", "recv_time", "measurements"}
)
RECORD_ALLOWED_KEYS = RECORD_REQUIRED_KEYS | frozenset({"rssi"})
METRIC_ITEM_KEYS = frozenset({"value", "status"})

METRIC_ORDER = ("temperature", "airHumidity", "pressure", "light", "soilMoisture")
METRIC_SPECS: dict[str, tuple[Decimal, Decimal, int]] = {
    "temperature": (Decimal("-40"), Decimal("85"), 2),
    "airHumidity": (Decimal("0"), Decimal("100"), 2),
    "pressure": (Decimal("300"), Decimal("1100"), 2),
    "light": (Decimal("0"), Decimal("65535"), 1),
    "soilMoisture": (Decimal("0"), Decimal("100"), 1),
}
RSSI_MIN = Decimal("-999999.99")
RSSI_MAX = Decimal("999999.99")
RSSI_SCALE = 2

VALID_TIME_STATUSES = frozenset({"VALID", "UNKNOWN"})
VALID_READ_STATUSES = frozenset({"OK", "READ_ERROR", "INVALID_VALUE"})


@dataclass(frozen=True)
class ValidatedMetric:
    metric_code: str
    status: str
    value: Decimal | None
    scaled_int: int | None


@dataclass(frozen=True)
class ValidatedRecord:
    device_id: str
    boot_id: int
    seq: int
    time_status: str
    t_node: int | None
    recv_time: int
    rssi: Decimal | None
    measurements: dict[str, ValidatedMetric]


def _reject_non_finite(token: str) -> None:
    raise ValueError(f"Non-finite JSON constant not allowed: {token}")


def parse_ingestion_json(raw_bytes: bytes) -> Any:
    """Parse UTF-8 JSON with Decimal for floats and reject NaN/Infinity."""
    try:
        body_text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise IngestionError(
            400,
            "INVALID_REQUEST",
            "Request body must be valid UTF-8 JSON",
            retryable=False,
        ) from None
    try:
        return json.loads(
            body_text,
            parse_float=Decimal,
            parse_constant=_reject_non_finite,
        )
    except (ValueError, InvalidOperation):
        raise IngestionError(
            400,
            "INVALID_REQUEST",
            "Malformed JSON request body",
            retryable=False,
        ) from None


def is_valid_code(value: Any) -> bool:
    return type(value) is str and CODE_RE.fullmatch(value) is not None


def is_valid_boot_id(value: Any) -> bool:
    return type(value) is int and 0 <= value <= MAX_INT64


def is_valid_seq(value: Any) -> bool:
    return type(value) is int and 1 <= value <= MAX_INT64


def is_valid_epoch(value: Any) -> bool:
    return type(value) is int and 0 <= value <= MAX_EPOCH_SEC


def validate_envelope(payload: Any) -> tuple[str, list[Any]]:
    """Validate request envelope per 03.1 D04 and G2-D02a (strict unknown keys)."""
    if not isinstance(payload, dict):
        raise IngestionError(
            400,
            "INVALID_REQUEST",
            "Request body must be a JSON object",
            retryable=False,
        )

    extra = sorted(set(payload.keys()) - ENVELOPE_KEYS)
    if extra:
        raise IngestionError(
            400,
            "INVALID_REQUEST",
            "Unexpected fields in request envelope",
            retryable=False,
            details=[{"field": k, "message": "Unexpected field"} for k in extra],
        )

    gateway_id = payload.get("gateway_id")
    if not is_valid_code(gateway_id):
        raise IngestionError(
            400,
            "INVALID_REQUEST",
            "gateway_id must be a valid 1-128 ASCII code",
            retryable=False,
            details=[{"field": "gateway_id", "message": "Invalid or missing gateway_id"}],
        )

    records = payload.get("records")
    if type(records) is not list or not (1 <= len(records) <= 50):
        count = len(records) if type(records) is list else None
        detail_msg = (
            f"Received {count} items" if count is not None else "records must be a JSON array"
        )
        raise IngestionError(
            400,
            "INVALID_REQUEST",
            "records must contain between 1 and 50 items",
            retryable=False,
            details=[{"field": "records", "message": detail_msg}],
        )

    return gateway_id, records


def extract_valid_identity(raw_item: Any) -> dict[str, Any]:
    """Echo only syntactically valid identity components per 03.1 D04 (5.9.5)."""
    if not isinstance(raw_item, dict):
        return {}
    echo: dict[str, Any] = {}
    device_id = raw_item.get("device_id")
    if is_valid_code(device_id):
        echo["device_id"] = device_id
    boot_id = raw_item.get("boot_id")
    if is_valid_boot_id(boot_id):
        echo["boot_id"] = boot_id
    seq = raw_item.get("seq")
    if is_valid_seq(seq):
        echo["seq"] = seq
    return echo


def _parse_exact_decimal(
    value: Any, *, min_value: Decimal, max_value: Decimal, scale: int
) -> tuple[Decimal, int] | None:
    if type(value) is int:
        dec = Decimal(value)
    elif type(value) is Decimal:
        dec = value
    else:
        return None
    if not dec.is_finite():
        return None
    if dec < min_value or dec > max_value:
        return None
    factor = Decimal(10) ** scale
    scaled = dec * factor
    if scaled != scaled.to_integral_value():
        return None
    scaled_int = int(scaled)
    norm_dec = (Decimal(scaled_int) / factor).quantize(Decimal(10) ** -scale)
    return norm_dec, scaled_int


def validate_record_static(raw_item: Any) -> tuple[ValidatedRecord | None, str | None]:
    """Validate static record schema/types/domains without dynamic D06 or DB checks."""
    if not isinstance(raw_item, dict):
        return None, "Record must be a JSON object"

    keys = set(raw_item.keys())
    extra = sorted(keys - RECORD_ALLOWED_KEYS)
    if extra:
        return None, f"Unexpected record fields: {', '.join(extra)}"

    missing = sorted(RECORD_REQUIRED_KEYS - keys)
    if missing:
        return None, f"Missing required fields: {', '.join(missing)}"

    device_id = raw_item["device_id"]
    if not is_valid_code(device_id):
        return None, "Invalid device_id"

    boot_id = raw_item["boot_id"]
    if not is_valid_boot_id(boot_id):
        return None, "Invalid boot_id"

    seq = raw_item["seq"]
    if not is_valid_seq(seq):
        return None, "Invalid seq"

    time_status = raw_item["time_status"]
    if type(time_status) is not str or time_status not in VALID_TIME_STATUSES:
        return None, "Invalid time_status"

    t_node = raw_item["t_node"]
    if time_status == "VALID":
        if not is_valid_epoch(t_node):
            return None, "VALID time_status requires integer t_node in allowed UTC epoch range"
    else:
        if t_node is not None:
            return None, "UNKNOWN time_status requires t_node to be null"

    recv_time = raw_item["recv_time"]
    if not is_valid_epoch(recv_time):
        return None, "Invalid recv_time"

    rssi_raw = raw_item.get("rssi")
    rssi_val: Decimal | None = None
    if rssi_raw is not None:
        parsed_rssi = _parse_exact_decimal(
            rssi_raw, min_value=RSSI_MIN, max_value=RSSI_MAX, scale=RSSI_SCALE
        )
        if parsed_rssi is None:
            return None, "Invalid rssi value or precision"
        rssi_val = parsed_rssi[0]

    measurements_raw = raw_item["measurements"]
    if not isinstance(measurements_raw, dict):
        return None, "measurements must be a JSON object"

    metric_keys = set(measurements_raw.keys())
    expected_metrics = set(METRIC_ORDER)
    if metric_keys != expected_metrics:
        return None, "measurements must contain exactly the 5 required metrics"

    validated_metrics: dict[str, ValidatedMetric] = {}
    for metric_code in METRIC_ORDER:
        item = measurements_raw[metric_code]
        if not isinstance(item, dict) or set(item.keys()) != METRIC_ITEM_KEYS:
            return None, f"Invalid measurement object for {metric_code}"
        status = item["status"]
        if type(status) is not str or status not in VALID_READ_STATUSES:
            return None, f"Invalid read status for {metric_code}"
        raw_val = item["value"]
        if status == "OK":
            min_v, max_v, scale = METRIC_SPECS[metric_code]
            parsed = _parse_exact_decimal(raw_val, min_value=min_v, max_value=max_v, scale=scale)
            if parsed is None:
                return None, f"Invalid value or precision for {metric_code}"
            validated_metrics[metric_code] = ValidatedMetric(
                metric_code=metric_code,
                status=status,
                value=parsed[0],
                scaled_int=parsed[1],
            )
        else:
            if raw_val is not None:
                return None, f"{metric_code} with status {status} must have value=null"
            validated_metrics[metric_code] = ValidatedMetric(
                metric_code=metric_code,
                status=status,
                value=None,
                scaled_int=None,
            )

    return (
        ValidatedRecord(
            device_id=device_id,
            boot_id=boot_id,
            seq=seq,
            time_status=time_status,
            t_node=t_node,
            recv_time=recv_time,
            rssi=rssi_val,
            measurements=validated_metrics,
        ),
        None,
    )


def validate_d06_timestamps(record: ValidatedRecord, backend_received_epoch_sec: int) -> str | None:
    """Validate D06 future/order tolerance (+120s inclusive) for NEW records only."""
    if record.recv_time > backend_received_epoch_sec + D06_TOLERANCE_SECONDS:
        return "recv_time exceeds the allowed future clock tolerance of 120 seconds"
    if record.time_status == "VALID" and record.t_node is not None:
        if record.t_node > record.recv_time + D06_TOLERANCE_SECONDS:
            return "t_node exceeds recv_time by more than 120 seconds"
        if record.t_node > backend_received_epoch_sec + D06_TOLERANCE_SECONDS:
            return "t_node exceeds the allowed future clock tolerance of 120 seconds"
    return None
