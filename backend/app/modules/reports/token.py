import base64
import hmac
import json
import re
from decimal import Decimal
from typing import Any

from app.core.web import (
    fail,
    json_loads_no_duplicates,
    parse_rfc3339_utc,
    parse_utc_datetime_ms,
)
from app.modules.auth.service import storage_error
from app.modules.devices.schemas import identity
from app.modules.measurements.repository import METRIC_ORDER, METRIC_UNITS

MAX_REPORT_TOKEN_CHARS = 32768
REPORT_PURPOSE = "REPORT_V1"
REPORT_CODEC = 1
MAX_INT64 = 9_223_372_036_854_775_807

_NON_NEG_INT_RE = re.compile(r"^(?:0|[1-9][0-9]{0,18})$")
_CANONICAL_STAT_RE = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.(?:[1-9]|[0-9][1-9]))?$")

_ROOT_KEYS = frozenset(
    {
        "purpose",
        "codec",
        "issued_at",
        "expires_at",
        "session_id",
        "instance_generation",
        "report",
    }
)
_REPORT_KEYS = frozenset({"context", "as_of", "N_total", "rows"})
_CONTEXT_KEYS = frozenset({"source", "farm", "zone", "node", "from", "to", "timezone"})
_FARM_ZONE_KEYS = frozenset({"id", "code", "name"})
_NODE_KEYS = frozenset({"id", "device_id", "name"})
_ROW_KEYS = frozenset({"metric_code", "unit", "n_valid", "n_invalid", "min", "max", "avg"})


def _b64u_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64u_decode_canonical(segment: str) -> bytes:
    if not segment or "=" in segment:
        raise ValueError("Invalid base64url segment")
    raw = base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
    if _b64u_encode(raw) != segment:
        raise ValueError("Non-canonical base64url segment")
    return raw


def _non_neg_bigint(value: Any) -> int:
    if type(value) is not str or _NON_NEG_INT_RE.fullmatch(value) is None:
        raise ValueError("Invalid canonical non-negative BIGINT string")
    parsed = int(value)
    if parsed > MAX_INT64:
        raise ValueError("BIGINT overflow")
    return parsed


def _non_empty_str(value: Any) -> str:
    if type(value) is not str or not value:
        raise ValueError("Expected non-empty string")
    return value


def _canonical_stat(value: Any) -> Decimal:
    if type(value) is not str or value == "-0" or _CANONICAL_STAT_RE.fullmatch(value) is None:
        raise ValueError("Invalid canonical statistic string")
    return Decimal(value)


def sign_report_token(settings, payload: dict[str, Any]) -> str:
    secret = settings.report_secret
    if secret is None or not secret.get_secret_value():
        raise storage_error()
    key = secret.get_secret_value().encode("utf-8")
    body = _b64u_encode(
        json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
    )
    prefix = "v1." + body
    sig = _b64u_encode(hmac.digest(key, prefix.encode("ascii"), "sha256"))
    return prefix + "." + sig


def verify_report_token(settings, token: str) -> dict[str, Any]:
    secret = settings.report_secret
    if secret is None or not secret.get_secret_value():
        raise storage_error()
    key = secret.get_secret_value().encode("utf-8")
    try:
        if type(token) is not str or not token or len(token) > MAX_REPORT_TOKEN_CHARS:
            raise ValueError()
        if not token.isascii():
            raise ValueError()
        parts = token.split(".")
        if len(parts) != 3:
            raise ValueError()
        version, body, signature = parts
        if version != "v1":
            raise ValueError()
        raw_sig = _b64u_decode_canonical(signature)
        if len(raw_sig) != 32:
            raise ValueError()
        expected_sig = hmac.digest(key, f"v1.{body}".encode("ascii"), "sha256")
        if not hmac.compare_digest(raw_sig, expected_sig):
            raise ValueError()

        raw_payload = _b64u_decode_canonical(body)
        decoded = json_loads_no_duplicates(raw_payload)
        if not isinstance(decoded, dict) or set(decoded.keys()) != _ROOT_KEYS:
            raise ValueError()
        if decoded["purpose"] != REPORT_PURPOSE or decoded["codec"] is not REPORT_CODEC:
            if decoded["purpose"] != REPORT_PURPOSE or type(decoded["codec"]) is not int:
                raise ValueError()
            if decoded["codec"] != REPORT_CODEC:
                raise ValueError()

        issued_dt = parse_utc_datetime_ms(decoded["issued_at"])
        expires_dt = parse_utc_datetime_ms(decoded["expires_at"])
        if issued_dt >= expires_dt:
            raise ValueError()

        identity(_non_empty_str(decoded["session_id"]))
        _non_empty_str(decoded["instance_generation"])

        report = decoded["report"]
        if not isinstance(report, dict) or set(report.keys()) != _REPORT_KEYS:
            raise ValueError()
        parse_utc_datetime_ms(report["as_of"])
        n_total = _non_neg_bigint(report["N_total"])

        ctx = report["context"]
        if not isinstance(ctx, dict) or set(ctx.keys()) != _CONTEXT_KEYS:
            raise ValueError()
        _non_empty_str(ctx["source"])
        if ctx["timezone"] != "UTC+07:00":
            raise ValueError()
        from_str = _non_empty_str(ctx["from"])
        to_str = _non_empty_str(ctx["to"])
        if not from_str.endswith("Z") or not to_str.endswith("Z"):
            raise ValueError()
        from_ts = parse_rfc3339_utc(from_str)
        to_ts = parse_rfc3339_utc(to_str)
        if from_ts.exact_epoch > to_ts.exact_epoch:
            raise ValueError()

        for area_key in ("farm", "zone"):
            area = ctx[area_key]
            if not isinstance(area, dict) or set(area.keys()) != _FARM_ZONE_KEYS:
                raise ValueError()
            identity(_non_empty_str(area["id"]))
            _non_empty_str(area["code"])
            _non_empty_str(area["name"])

        node = ctx["node"]
        if not isinstance(node, dict) or set(node.keys()) != _NODE_KEYS:
            raise ValueError()
        identity(_non_empty_str(node["id"]))
        _non_empty_str(node["device_id"])
        _non_empty_str(node["name"])

        rows = report["rows"]
        if not isinstance(rows, list) or len(rows) != len(METRIC_ORDER):
            raise ValueError()

        for idx, expected_metric in enumerate(METRIC_ORDER):
            row = rows[idx]
            if not isinstance(row, dict) or set(row.keys()) != _ROW_KEYS:
                raise ValueError()
            if row["metric_code"] != expected_metric:
                raise ValueError()
            if row["unit"] != METRIC_UNITS[expected_metric]:
                raise ValueError()
            n_valid = _non_neg_bigint(row["n_valid"])
            n_invalid = _non_neg_bigint(row["n_invalid"])
            if n_valid + n_invalid != n_total:
                raise ValueError()
            min_v, max_v, avg_v = row["min"], row["max"], row["avg"]
            if n_valid == 0:
                if min_v is not None or max_v is not None or avg_v is not None:
                    raise ValueError()
            else:
                d_min = _canonical_stat(min_v)
                d_max = _canonical_stat(max_v)
                d_avg = _canonical_stat(avg_v)
                if not (d_min <= d_avg <= d_max):
                    raise ValueError()

        return decoded
    except (ValueError, TypeError, UnicodeError):
        fail(400, "TOKEN_INVALID", "Snapshot báo cáo không hợp lệ")
