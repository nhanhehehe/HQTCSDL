import hashlib
import json
from decimal import Decimal

from app.modules.ingestion.validator import METRIC_ORDER, ValidatedRecord

CANONICAL_V1_PREFIX = b"HQTCSDL/measurement/v1\n"
CANONICAL_VERSION = 1


def build_canonical_v1(record: ValidatedRecord) -> tuple[bytes, bytes]:
    """Build exact Canonical-v1 UTF-8 bytes and SHA-256 32-byte digest per 03.5.1 §4."""
    array = [
        record.device_id,
        record.boot_id,
        record.seq,
        record.time_status,
        record.t_node,
        *(
            [record.measurements[code].status, record.measurements[code].scaled_int]
            for code in METRIC_ORDER
        ),
    ]
    body = json.dumps(array, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode(
        "utf-8"
    )
    canonical_bytes = CANONICAL_V1_PREFIX + body
    return canonical_bytes, hashlib.sha256(canonical_bytes).digest()


def is_same_original_content(
    incoming: ValidatedRecord,
    *,
    stored_device_code: str,
    stored_header: dict,
    stored_values: list[dict],
) -> bool:
    """Compare full typed original measurement fields (supports C10 regardless of hash version)."""
    if stored_device_code != incoming.device_id:
        return False
    if int(stored_header["boot_id"]) != incoming.boot_id:
        return False
    if int(stored_header["seq"]) != incoming.seq:
        return False
    if stored_header["time_status"] != incoming.time_status:
        return False
    stored_t_node = None if stored_header["t_node"] is None else int(stored_header["t_node"])
    if stored_t_node != incoming.t_node:
        return False

    by_metric = {row["metric_code"]: row for row in stored_values}
    if len(by_metric) != len(METRIC_ORDER) or set(by_metric.keys()) != set(METRIC_ORDER):
        return False

    for code in METRIC_ORDER:
        stored = by_metric[code]
        inc = incoming.measurements[code]
        if stored["read_status"] != inc.status:
            return False
        stored_val = stored["value"]
        if stored_val is None and inc.value is None:
            continue
        if stored_val is None or inc.value is None:
            return False
        if Decimal(stored_val) != inc.value:
            return False

    return True
