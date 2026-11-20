import csv
import io
import unicodedata
from typing import Any

from app.core.web import format_utc_iso_to_vi, parse_utc_datetime_ms
from app.modules.devices.schemas import identity

CSV_HEADER = (
    "report_as_of",
    "from_measured_at",
    "to_measured_at",
    "timezone",
    "farm_code",
    "zone_code",
    "node_code",
    "data_source",
    "total_records",
    "metric",
    "unit",
    "valid_count",
    "invalid_count",
    "min",
    "max",
    "avg",
)

_DANGEROUS_PREFIX_CHARS = frozenset({"=", "+", "-", "@"})
_PROBE_SKIP_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp", "Zs"})


def sanitize_csv_text(raw: str) -> str:
    """Protect text cells from CSV formula injection without mutating signed text."""
    if raw and unicodedata.category(raw[0]) in {"Cc", "Cf"}:
        return "text:" + raw
    probe = unicodedata.normalize("NFKC", raw)
    idx = 0
    length = len(probe)
    while idx < length and (
        probe[idx].isspace() or unicodedata.category(probe[idx]) in _PROBE_SKIP_CATEGORIES
    ):
        idx += 1
    if idx < length and probe[idx] in _DANGEROUS_PREFIX_CHARS:
        return "text:" + raw
    return raw


def build_report_filename(node_id: str, as_of_utc: str) -> str:
    safe_node_id = identity(node_id)
    parse_utc_datetime_ms(as_of_utc)
    compact = as_of_utc.replace("-", "").replace(":", "").replace(".", "")
    return f"measurements_node_{safe_node_id}_{compact}.csv"


def build_report_csv_bytes(report: dict[str, Any]) -> tuple[bytes, str]:
    ctx = report["context"]
    as_of_utc = report["as_of"]
    as_of_vi = format_utc_iso_to_vi(as_of_utc)
    from_vi = format_utc_iso_to_vi(ctx["from"])
    to_vi = format_utc_iso_to_vi(ctx["to"])
    tz_label = sanitize_csv_text(ctx["timezone"])
    farm_code = sanitize_csv_text(ctx["farm"]["code"])
    zone_code = sanitize_csv_text(ctx["zone"]["code"])
    node_code = sanitize_csv_text(ctx["node"]["device_id"])
    data_source = sanitize_csv_text(ctx["source"])
    n_total = report["N_total"]

    buf = io.StringIO(newline="")
    writer = csv.writer(
        buf,
        delimiter=",",
        quotechar='"',
        doublequote=True,
        quoting=csv.QUOTE_ALL,
        lineterminator="\r\n",
    )
    writer.writerow(CSV_HEADER)
    for row in report["rows"]:
        writer.writerow(
            [
                as_of_vi,
                from_vi,
                to_vi,
                tz_label,
                farm_code,
                zone_code,
                node_code,
                data_source,
                n_total,
                sanitize_csv_text(row["metric_code"]),
                sanitize_csv_text(row["unit"]),
                row["n_valid"],
                row["n_invalid"],
                row["min"] if row["min"] is not None else "",
                row["max"] if row["max"] is not None else "",
                row["avg"] if row["avg"] is not None else "",
            ]
        )

    csv_bytes = b"\xef\xbb\xbf" + buf.getvalue().encode("utf-8")
    filename = build_report_filename(ctx["node"]["id"], as_of_utc)
    return csv_bytes, filename
