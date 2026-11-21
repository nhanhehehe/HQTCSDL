from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.core.web import fail, json_loads_no_duplicates, serialize_utc
from app.modules.alerts.threshold_codec import datetime_from_utc_ms
from app.modules.auth.service import storage_error

_ACK_KEYS = frozenset({"acknowledgement_version", "viewed_revision_no", "note"})
_MAX_INT32 = 2_147_483_647


def _format_bound(value: Any) -> str | None:
    if value is None:
        return None
    return f"{Decimal(value):.2f}"


def _format_epoch_utc(epoch_sec: Any) -> str | None:
    if epoch_sec is None:
        return None
    return serialize_utc(datetime_from_utc_ms(int(epoch_sec) * 1000))


def _parse_threshold_metadata(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if type(raw) is not str:
        raise storage_error()
    try:
        parsed = json_loads_no_duplicates(raw)
    except (ValueError, TypeError):
        raise storage_error() from None
    if not isinstance(parsed, dict):
        raise storage_error()
    return {
        "codec": parsed.get("codec"),
        "is_current": parsed.get("is_current"),
        "transition": parsed.get("transition"),
    }


def validate_alert_projection_row(row: dict[str, Any]) -> None:
    cur_rev = row.get("current_revision_no")
    rev_no = row.get("revision_no")
    if cur_rev is None or rev_no is None or int(cur_rev) != int(rev_no):
        raise storage_error()
    status = row.get("lifecycle_status")
    end_reason = row.get("end_reason")
    if status == "OPEN" and end_reason is not None:
        raise storage_error()
    if status == "CLOSED" and end_reason is None:
        raise storage_error()


def compute_recovery_proven(row: dict[str, Any]) -> bool | None:
    if row["alert_kind"] == "TIMEOUT":
        return None
    return row["lifecycle_status"] == "CLOSED" and row.get("end_reason") == "RECOVERED"


def build_revision_dto(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "revision_no": int(row["revision_no"]),
        "config_id": str(row["config_id"]),
        "period_id": str(row["period_id"]) if row.get("period_id") is not None else None,
        "direction": row.get("direction"),
        "start_event_time": _format_epoch_utc(row.get("start_event_time")),
        "end_event_time": _format_epoch_utc(row.get("end_event_time")),
        "start_backend_at": serialize_utc(row.get("start_backend_at")),
        "end_backend_at": serialize_utc(row.get("end_backend_at")),
        "closed_at": serialize_utc(row.get("closed_at")),
        "end_reason": row.get("end_reason"),
        "start_measurement_id": (
            str(row["start_measurement_id"])
            if row.get("start_measurement_id") is not None
            else None
        ),
        "end_measurement_id": (
            str(row["end_measurement_id"]) if row.get("end_measurement_id") is not None else None
        ),
        "lower_snapshot": _format_bound(row.get("lower_snapshot")),
        "upper_snapshot": _format_bound(row.get("upper_snapshot")),
        "timeout_snapshot": (
            int(row["timeout_snapshot"]) if row.get("timeout_snapshot") is not None else None
        ),
        "evaluated_at": serialize_utc(row.get("evaluated_at")),
        "evaluation_generation": str(row["evaluation_generation"]),
        "acknowledgement_version": int(row["acknowledgement_version"]),
        "threshold_metadata": _parse_threshold_metadata(row.get("threshold_metadata_json")),
    }


def build_acknowledgement_dto(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "episode_id": str(row["episode_id"]),
        "acknowledgement_version": int(row["acknowledgement_version"]),
        "acknowledged_revision_no": int(row["acknowledged_revision_no"]),
        "admin_id": str(row["admin_id"]),
        "admin_username": row.get("username"),
        "acknowledged_at": serialize_utc(row["acknowledged_at"]),
        "note": row.get("note"),
    }


def build_replacement_dto(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "old_episode_id": str(row["old_episode_id"]),
        "old_revision_no": int(row["old_revision_no"]),
        "new_episode_id": str(row["new_episode_id"]),
        "new_revision_no": int(row["new_revision_no"]),
        "created_at": serialize_utc(row["created_at"]),
        "evaluation_generation": str(row["evaluation_generation"]),
        "reason": row["reason"],
    }


def build_alert_summary(row: dict[str, Any]) -> dict[str, Any]:
    validate_alert_projection_row(row)
    ack_at: datetime | None = row.get("acknowledged_at")
    current_ack = (
        {
            "episode_id": str(row["episode_id"]),
            "acknowledgement_version": int(row["acknowledgement_version"]),
            "acknowledged_revision_no": int(row["acknowledged_revision_no"]),
            "admin_id": str(row["ack_admin_id"]),
            "admin_username": row.get("ack_username"),
            "acknowledged_at": serialize_utc(ack_at),
            "note": row.get("ack_note"),
        }
        if ack_at is not None
        else None
    )
    return {
        "id": str(row["episode_id"]),
        "node_id": str(row["node_id"]),
        "device_id": row["device_code"],
        "node_name": row["node_name"],
        "zone_id": str(row["zone_id"]),
        "zone_code": row["zone_code"],
        "farm_id": str(row["farm_id"]),
        "kind": row["alert_kind"],
        "metric_code": row.get("metric_code"),
        "direction": row.get("direction"),
        "lifecycle_status": row["lifecycle_status"],
        "is_current": bool(row["is_current"]),
        "current_revision_no": int(row["current_revision_no"]),
        "acknowledgement_version": int(row["acknowledgement_version"]),
        "acknowledged": current_ack is not None,
        "current_acknowledgement": current_ack,
        "recovery_proven": compute_recovery_proven(row),
        "first_detected_at": serialize_utc(row["first_detected_at"]),
        "closed_at": serialize_utc(row.get("closed_at")),
        "end_reason": row.get("end_reason"),
        "current_revision": build_revision_dto(row),
    }


def build_alert_detail(row: dict[str, Any]) -> dict[str, Any]:
    summary = build_alert_summary(row)
    summary["farm"] = {
        "id": str(row["farm_id"]),
        "code": row["farm_code"],
        "name": row["farm_name"],
        "archived_at": serialize_utc(row.get("farm_archived_at")),
    }
    summary["zone"] = {
        "id": str(row["zone_id"]),
        "code": row["zone_code"],
        "name": row["zone_name"],
        "archived_at": serialize_utc(row.get("zone_archived_at")),
    }
    summary["node"] = {
        "id": str(row["node_id"]),
        "device_id": row["device_code"],
        "name": row["node_name"],
    }
    return summary


@dataclass(frozen=True)
class AcknowledgementCreateData:
    acknowledgement_version: int
    viewed_revision_no: int
    note: str | None


def parse_acknowledgement_body(raw_bytes: bytes) -> AcknowledgementCreateData:
    try:
        payload = json_loads_no_duplicates(raw_bytes)
    except (UnicodeDecodeError, ValueError, TypeError):
        fail(400, "INPUT_INVALID", "Dữ liệu JSON không hợp lệ")

    if not isinstance(payload, dict):
        fail(400, "INPUT_INVALID", "Body phải là JSON object")
    if any(k not in _ACK_KEYS for k in payload):
        fail(400, "INPUT_INVALID", "Chứa trường không được hỗ trợ")
    if "acknowledgement_version" not in payload or "viewed_revision_no" not in payload:
        fail(400, "INPUT_INVALID", "Thiếu acknowledgement_version hoặc viewed_revision_no")

    ack_ver = payload["acknowledgement_version"]
    viewed_rev = payload["viewed_revision_no"]
    if type(ack_ver) is not int or not (1 <= ack_ver <= _MAX_INT32):
        fail(400, "INPUT_INVALID", "acknowledgement_version phải là số nguyên dương")
    if type(viewed_rev) is not int or not (1 <= viewed_rev <= _MAX_INT32):
        fail(400, "INPUT_INVALID", "viewed_revision_no phải là số nguyên dương")

    note_raw = payload.get("note")
    if note_raw is not None:
        if type(note_raw) is not str:
            fail(400, "INPUT_INVALID", "note phải là chuỗi văn bản")
        if len(note_raw) > 1000:
            fail(400, "INPUT_INVALID", "note vượt quá 1000 ký tự")

    return AcknowledgementCreateData(
        acknowledgement_version=ack_ver,
        viewed_revision_no=viewed_rev,
        note=note_raw,
    )
