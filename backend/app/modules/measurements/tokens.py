import re
from typing import Any

from app.core.web import (
    MAX_EPOCH_SEC,
    fail,
    parse_rfc3339_utc,
    parse_utc_datetime_ms,
    sign_cursor,
    verify_cursor,
)
from app.modules.devices.schemas import identity

MAX_INT64 = 9_223_372_036_854_775_807
_NON_NEG_INT_RE = re.compile(r"^(?:0|[1-9][0-9]{0,18})$")

_VIEW_KEYS = frozenset({"purpose", "generation", "node_id", "mode", "from", "to", "cap", "as_of"})
_CURSOR_KEYS = frozenset({"purpose", "route", "order", "page_size", "view", "boundary"})
_KNOWN_BOUNDARY_KEYS = frozenset({"t_node", "boot_id", "seq"})
_UNKNOWN_BOUNDARY_KEYS = frozenset({"persisted_at", "measurement_id"})
_VALID_MODES = frozenset({"known", "unknown"})
_VALID_ROUTES = frozenset({"measurements-known", "measurements-unknown", "measurements-chart"})
_VALID_ORDERS = frozenset({"event-desc", "unknown-desc", "event-asc"})


def _non_neg_bigint_str(value: Any, *, maximum: int = MAX_INT64) -> int:
    if type(value) is not str or _NON_NEG_INT_RE.fullmatch(value) is None:
        raise ValueError("Invalid non-negative integer string")
    parsed = int(value)
    if parsed > maximum:
        raise ValueError("Integer out of bounds")
    return parsed


def make_view_payload(
    *,
    generation: str,
    node_id: str,
    mode: str,
    from_iso: str | None,
    to_iso: str | None,
    cap: int,
    as_of: str,
) -> dict[str, Any]:
    return {
        "purpose": "measurement-view",
        "generation": generation,
        "node_id": node_id,
        "mode": mode,
        "from": from_iso,
        "to": to_iso,
        "cap": str(cap),
        "as_of": as_of,
    }


def validate_view_payload(
    decoded: Any,
    *,
    expected_generation: str,
    expected_node_id: str,
    expected_mode: str,
) -> dict[str, Any]:
    try:
        if not isinstance(decoded, dict) or set(decoded.keys()) != _VIEW_KEYS:
            raise ValueError()
        if decoded["purpose"] != "measurement-view":
            raise ValueError()
        if decoded["generation"] != expected_generation:
            raise ValueError()
        if decoded["node_id"] != expected_node_id:
            raise ValueError()
        mode = decoded["mode"]
        if type(mode) is not str or mode not in _VALID_MODES or mode != expected_mode:
            raise ValueError()
        _non_neg_bigint_str(decoded["cap"])
        parse_utc_datetime_ms(decoded["as_of"])
        from_val = decoded["from"]
        to_val = decoded["to"]
        if mode == "known":
            if type(from_val) is not str or type(to_val) is not str:
                raise ValueError()
            from_ts = parse_rfc3339_utc(from_val)
            to_ts = parse_rfc3339_utc(to_val)
            if from_ts.exact_epoch > to_ts.exact_epoch:
                raise ValueError()
        else:
            if from_val is not None or to_val is not None:
                raise ValueError()
        return decoded
    except (ValueError, TypeError):
        fail(400, "CURSOR_INVALID")


def sign_view_token(settings, view_payload: dict[str, Any]) -> str:
    return sign_cursor(settings, view_payload)


def verify_view_token(
    settings,
    token: str,
    *,
    expected_generation: str,
    expected_node_id: str,
    expected_mode: str,
) -> dict[str, Any]:
    decoded = verify_cursor(settings, token)
    return validate_view_payload(
        decoded,
        expected_generation=expected_generation,
        expected_node_id=expected_node_id,
        expected_mode=expected_mode,
    )


def sign_measurement_cursor(
    settings,
    *,
    route: str,
    order: str,
    page_size: int,
    view_payload: dict[str, Any],
    boundary: dict[str, str],
) -> str:
    return sign_cursor(
        settings,
        {
            "purpose": "measurement-cursor",
            "route": route,
            "order": order,
            "page_size": page_size,
            "view": view_payload,
            "boundary": boundary,
        },
    )


def verify_measurement_cursor(
    settings,
    token: str,
    *,
    expected_route: str,
    expected_order: str,
    expected_page_size: int,
    expected_generation: str,
    expected_node_id: str,
    expected_mode: str,
) -> dict[str, Any]:
    decoded = verify_cursor(settings, token)
    try:
        if not isinstance(decoded, dict) or set(decoded.keys()) != _CURSOR_KEYS:
            raise ValueError()
        if decoded["purpose"] != "measurement-cursor":
            raise ValueError()
        route = decoded["route"]
        order = decoded["order"]
        page_size = decoded["page_size"]
        if (
            type(route) is not str
            or route not in _VALID_ROUTES
            or route != expected_route
            or type(order) is not str
            or order not in _VALID_ORDERS
            or order != expected_order
            or type(page_size) is not int
            or page_size != expected_page_size
        ):
            raise ValueError()
        view = validate_view_payload(
            decoded["view"],
            expected_generation=expected_generation,
            expected_node_id=expected_node_id,
            expected_mode=expected_mode,
        )
        boundary = decoded["boundary"]
        if not isinstance(boundary, dict):
            raise ValueError()
        if expected_mode == "known":
            if set(boundary.keys()) != _KNOWN_BOUNDARY_KEYS:
                raise ValueError()
            _non_neg_bigint_str(boundary["t_node"], maximum=MAX_EPOCH_SEC)
            _non_neg_bigint_str(boundary["boot_id"])
            identity(boundary["seq"])
        else:
            if set(boundary.keys()) != _UNKNOWN_BOUNDARY_KEYS:
                raise ValueError()
            parse_utc_datetime_ms(boundary["persisted_at"])
            identity(boundary["measurement_id"])
        return {"view": view, "boundary": boundary}
    except (ValueError, TypeError):
        fail(400, "CURSOR_INVALID")
