import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from app.core.web import fail
from app.modules.devices.schemas import identity, version

METRIC_ORDER = ("temperature", "airHumidity", "pressure", "light", "soilMoisture")
METRIC_BOUNDS: dict[str, tuple[Decimal, Decimal]] = {
    "temperature": (Decimal("-40"), Decimal("85")),
    "airHumidity": (Decimal("0"), Decimal("100")),
    "pressure": (Decimal("300"), Decimal("1100")),
    "light": (Decimal("0"), Decimal("65535")),
    "soilMoisture": (Decimal("0"), Decimal("100")),
}

_RECEIVING_KEYS = frozenset({"receiving_enabled", "expected_row_version"})
_MONITORING_KEYS = frozenset(
    {
        "thresholds",
        "timeout_minutes",
        "expected_threshold_config_id",
        "expected_timeout_config_id",
    }
)
_METRIC_PATCH_KEYS = frozenset({"enabled", "lower", "upper"})
_MAX_INT32 = 2_147_483_647


class _NonFiniteSentinel:
    def __init__(self, raw: str):
        self.raw = raw


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    for key, val in pairs:
        if key in obj:
            raise ValueError(f"Duplicate JSON key: {key}")
        obj[key] = val
    return obj


def _reject_non_finite(token: str) -> None:
    raise ValueError(f"Non-finite JSON constant: {token}")


def _parse_json_decimal(token: str) -> Decimal | _NonFiniteSentinel:
    try:
        d = Decimal(token)
    except InvalidOperation:
        return _NonFiniteSentinel(token)
    if not d.is_finite():
        return _NonFiniteSentinel(token)
    return d


def _to_exact_decimal_9_2(value: int | Decimal) -> Decimal | None:
    """Convert int/Decimal to exact DECIMAL(9,2) without rounding, or None if not exact."""
    d = Decimal(value) if type(value) is int else value
    if not d.is_finite() or abs(d) > Decimal("9999999.99"):
        return None
    if d == 0:
        return Decimal("0.00")
    tup = d.as_tuple()
    exp = tup.exponent
    if not isinstance(exp, int):
        return None
    digits = tup.digits
    nz_len = len(digits)
    while nz_len > 0 and digits[nz_len - 1] == 0:
        nz_len -= 1
        exp += 1
    if exp < -2:
        return None
    sign_str = "-" if tup.sign else ""
    sig_digits = "".join(str(x) for x in digits[:nz_len])
    if exp >= 0:
        exact = Decimal(f"{sign_str}{sig_digits}{'0' * exp}.00")
    else:
        pad = sig_digits.rjust(-exp + 1, "0")
        split = len(pad) + exp
        frac = pad[split:].ljust(2, "0")
        exact = Decimal(f"{sign_str}{pad[:split]}.{frac}")
    return Decimal("0.00") if exact == 0 else exact


def _validate_expected_id(raw: Any, field_name: str) -> int | None:
    if raw is None:
        return None
    if type(raw) is not str:
        fail(400, "INPUT_INVALID", f"{field_name} phải là chuỗi ID hoặc null")
    try:
        return int(identity(raw))
    except ValueError:
        fail(400, "INPUT_INVALID", f"{field_name} không hợp lệ")


@dataclass(frozen=True)
class ReceivingPatchData:
    receiving_enabled: bool
    expected_row_version: bytes


@dataclass(frozen=True)
class MetricThresholdPatch:
    enabled: bool | None = None
    has_lower: bool = False
    lower: Decimal | None = None
    has_upper: bool = False
    upper: Decimal | None = None


@dataclass(frozen=True)
class MonitoringPatchData:
    thresholds: dict[str, MetricThresholdPatch] | None
    has_expected_threshold: bool
    expected_threshold_config_id: int | None
    timeout_minutes: int | None
    has_expected_timeout: bool
    expected_timeout_config_id: int | None


def parse_receiving_patch_body(raw_bytes: bytes) -> ReceivingPatchData:
    try:
        text_data = raw_bytes.decode("utf-8")
        payload = json.loads(
            text_data,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
        )
    except (UnicodeDecodeError, ValueError, TypeError):
        fail(400, "INPUT_INVALID", "Dữ liệu JSON không hợp lệ")

    if not isinstance(payload, dict) or set(payload.keys()) != _RECEIVING_KEYS:
        fail(400, "INPUT_INVALID", "Trường dữ liệu không hợp lệ")

    enabled = payload["receiving_enabled"]
    if type(enabled) is not bool:
        fail(400, "INPUT_INVALID", "receiving_enabled phải là boolean")

    raw_version = payload["expected_row_version"]
    if type(raw_version) is not str:
        fail(400, "INPUT_INVALID", "expected_row_version không hợp lệ")
    try:
        expected_bytes = version(raw_version)
    except ValueError:
        fail(400, "INPUT_INVALID", "expected_row_version không hợp lệ")

    return ReceivingPatchData(receiving_enabled=enabled, expected_row_version=expected_bytes)


def parse_monitoring_patch_body(raw_bytes: bytes) -> MonitoringPatchData:
    try:
        text_data = raw_bytes.decode("utf-8")
        payload = json.loads(
            text_data,
            object_pairs_hook=_reject_duplicate_keys,
            parse_float=_parse_json_decimal,
            parse_constant=_NonFiniteSentinel,
        )
    except (UnicodeDecodeError, ValueError, TypeError):
        fail(400, "INPUT_INVALID", "Dữ liệu JSON không hợp lệ")

    if not isinstance(payload, dict):
        fail(400, "INPUT_INVALID", "Body phải là JSON object")
    if any(key not in _MONITORING_KEYS for key in payload):
        fail(400, "INPUT_INVALID", "Chứa trường không được hỗ trợ")
    if "thresholds" not in payload and "timeout_minutes" not in payload:
        fail(400, "INPUT_INVALID", "Phải gửi ít nhất thresholds hoặc timeout_minutes")

    has_expected_threshold = "expected_threshold_config_id" in payload
    expected_threshold_id = (
        _validate_expected_id(
            payload["expected_threshold_config_id"], "expected_threshold_config_id"
        )
        if has_expected_threshold
        else None
    )

    has_expected_timeout = "expected_timeout_config_id" in payload
    expected_timeout_id = (
        _validate_expected_id(payload["expected_timeout_config_id"], "expected_timeout_config_id")
        if has_expected_timeout
        else None
    )

    timeout_minutes: int | None = None
    if "timeout_minutes" in payload:
        if not has_expected_timeout:
            fail(400, "INPUT_INVALID", "Thiếu expected_timeout_config_id")
        raw_t = payload["timeout_minutes"]
        if (
            isinstance(raw_t, _NonFiniteSentinel)
            or type(raw_t) is not int
            or not (1 <= raw_t <= _MAX_INT32)
        ):
            fail(400, "INPUT_INVALID", "timeout_minutes phải là số nguyên dương")
        timeout_minutes = raw_t

    thresholds: dict[str, MetricThresholdPatch] | None = None
    if "thresholds" in payload:
        if not has_expected_threshold:
            fail(400, "INPUT_INVALID", "Thiếu expected_threshold_config_id")
        raw_thresholds = payload["thresholds"]
        if not isinstance(raw_thresholds, dict) or not raw_thresholds:
            fail(400, "INPUT_INVALID", "thresholds phải là object không rỗng")
        parsed_thresholds: dict[str, MetricThresholdPatch] = {}
        for metric_code, item in raw_thresholds.items():
            if metric_code not in METRIC_BOUNDS:
                fail(400, "INPUT_INVALID", f"Mã đại lượng không hợp lệ: {metric_code}")
            if not isinstance(item, dict) or not item:
                fail(400, "INPUT_INVALID", f"Cấu hình ngưỡng {metric_code} không hợp lệ")
            if any(k not in _METRIC_PATCH_KEYS for k in item):
                fail(400, "INPUT_INVALID", f"Chứa trường không hợp lệ tại {metric_code}")

            enabled_val: bool | None = None
            if "enabled" in item:
                if type(item["enabled"]) is not bool:
                    fail(400, "INPUT_INVALID", f"thresholds.{metric_code}.enabled phải là boolean")
                enabled_val = item["enabled"]

            min_val, max_val = METRIC_BOUNDS[metric_code]
            bounds: dict[str, Decimal] = {}
            for bound_key in ("lower", "upper"):
                if bound_key not in item:
                    continue
                field_path = f"thresholds.{metric_code}.{bound_key}"
                raw_num = item[bound_key]
                if isinstance(raw_num, _NonFiniteSentinel):
                    fail(
                        422,
                        "INPUT_INVALID",
                        "Giá trị ngưỡng không biểu diễn chính xác theo DECIMAL(9,2)",
                        details={"reason": "THRESHOLD_DECIMAL_INVALID", "field": field_path},
                    )
                if type(raw_num) is bool or not isinstance(raw_num, (int, Decimal)):
                    fail(400, "INPUT_INVALID", f"{field_path} phải là số")
                exact = _to_exact_decimal_9_2(raw_num)
                if exact is None:
                    fail(
                        422,
                        "INPUT_INVALID",
                        "Giá trị ngưỡng không biểu diễn chính xác theo DECIMAL(9,2)",
                        details={"reason": "THRESHOLD_DECIMAL_INVALID", "field": field_path},
                    )
                if not (min_val <= exact <= max_val):
                    fail(400, "INPUT_INVALID", f"{field_path} ngoài miền kỹ thuật")
                bounds[bound_key] = exact

            parsed_thresholds[metric_code] = MetricThresholdPatch(
                enabled=enabled_val,
                has_lower="lower" in bounds,
                lower=bounds.get("lower"),
                has_upper="upper" in bounds,
                upper=bounds.get("upper"),
            )
        thresholds = parsed_thresholds

    return MonitoringPatchData(
        thresholds=thresholds,
        has_expected_threshold=has_expected_threshold,
        expected_threshold_config_id=expected_threshold_id,
        timeout_minutes=timeout_minutes,
        has_expected_timeout=has_expected_timeout,
        expected_timeout_config_id=expected_timeout_id,
    )
