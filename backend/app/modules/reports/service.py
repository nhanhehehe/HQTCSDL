from datetime import timedelta
from decimal import Decimal
from typing import Any

from app.core.errors import WebError
from app.core.web import (
    fail,
    parse_rfc3339_utc,
    parse_utc_datetime_ms,
    resolve_event_range,
    serialize_utc,
    web_session_transaction,
)
from app.modules.auth import repository as auth_repo
from app.modules.auth.service import invalid_session, storage_error
from app.modules.measurements.repository import METRIC_ALLOWED_UNITS, METRIC_ORDER
from app.modules.reports import repository as repo
from app.modules.reports.csv_builder import build_report_csv_bytes
from app.modules.reports.rounding import round_avg_half_up, round_stat_half_up
from app.modules.reports.schemas import ReportCreateRequest, ReportCsvRequest
from app.modules.reports.token import (
    REPORT_CODEC,
    REPORT_PURPOSE,
    sign_report_token,
    verify_report_token,
)


class ReportService:
    def __init__(self, database, settings):
        self.database = database
        self.settings = settings

    def create_report(
        self, cookie: str | None, csrf: str | None, body: ReportCreateRequest
    ) -> dict[str, Any]:
        try:
            parsed_from = parse_rfc3339_utc(body.from_) if body.from_ is not None else None
            parsed_to = parse_rfc3339_utc(body.to) if body.to is not None else None
        except ValueError:
            raise WebError(400, "RANGE_INVALID", "Định dạng thời gian không hợp lệ") from None
        if (
            parsed_from is not None
            and parsed_to is not None
            and parsed_from.exact_epoch > parsed_to.exact_epoch
        ):
            raise WebError(
                400, "RANGE_INVALID", "Thời gian bắt đầu phải nhỏ hơn hoặc bằng kết thúc"
            )

        with web_session_transaction(
            self.database,
            self.settings,
            cookie,
            csrf,
            write=False,
            require_csrf=True,
            lock_session=True,
            isolation="SNAPSHOT",
        ) as (session, auth_ctx):
            if self.settings.report_secret is None:
                raise storage_error()

            node_ctx = repo.get_node_report_context(session, body.node_id)
            if node_ctx is None:
                fail(404, "NOT_FOUND")

            as_of_dt = auth_ctx["db_now"]
            as_of_iso = serialize_utc(as_of_dt)
            assert as_of_iso is not None

            resolved = resolve_event_range(body.from_, body.to, as_of_dt=as_of_dt)
            n_total, agg_rows = repo.aggregate_node_measurements(
                session,
                node_id=int(node_ctx["node_id"]),
                lo=resolved.lo_epoch,
                hi=resolved.hi_epoch,
            )

            by_metric = {r["metric_code"]: r for r in agg_rows}
            if set(by_metric.keys()) != set(METRIC_ORDER) or len(agg_rows) != len(METRIC_ORDER):
                raise storage_error()

            rows_out: list[dict[str, Any]] = []
            for metric_code in METRIC_ORDER:
                r = by_metric[metric_code]
                if r["unit"] not in METRIC_ALLOWED_UNITS[metric_code]:
                    raise storage_error()
                seen_count = int(r["seen_count"])
                n_valid = int(r["n_valid"])
                bad_rows = int(r["bad_rows"])
                if bad_rows != 0 or seen_count != n_total or not (0 <= n_valid <= n_total):
                    raise storage_error()
                n_invalid = n_total - n_valid
                if n_valid == 0:
                    min_str = None
                    max_str = None
                    avg_str = None
                else:
                    min_val = r["min_value"]
                    max_val = r["max_value"]
                    sum_val = r["sum_value"]
                    if min_val is None or max_val is None or sum_val is None:
                        raise storage_error()
                    min_dec = min_val if isinstance(min_val, Decimal) else Decimal(str(min_val))
                    max_dec = max_val if isinstance(max_val, Decimal) else Decimal(str(max_val))
                    sum_dec = sum_val if isinstance(sum_val, Decimal) else Decimal(str(sum_val))
                    min_str = round_stat_half_up(min_dec)
                    max_str = round_stat_half_up(max_dec)
                    avg_str = round_avg_half_up(sum_dec, n_valid)
                rows_out.append(
                    {
                        "metric_code": metric_code,
                        "unit": r["unit"],
                        "n_valid": str(n_valid),
                        "n_invalid": str(n_invalid),
                        "min": min_str,
                        "max": max_str,
                        "avg": avg_str,
                    }
                )

            report = {
                "context": {
                    "source": self.settings.env,
                    "farm": {
                        "id": str(node_ctx["farm_id"]),
                        "code": node_ctx["farm_code"],
                        "name": node_ctx["farm_name"],
                    },
                    "zone": {
                        "id": str(node_ctx["zone_id"]),
                        "code": node_ctx["zone_code"],
                        "name": node_ctx["zone_name"],
                    },
                    "node": {
                        "id": str(node_ctx["node_id"]),
                        "device_id": node_ctx["device_code"],
                        "name": node_ctx["node_name"],
                    },
                    "from": resolved.from_ts.utc_iso,
                    "to": resolved.to_ts.utc_iso,
                    "timezone": "UTC+07:00",
                },
                "as_of": as_of_iso,
                "N_total": str(n_total),
                "rows": rows_out,
            }

            rechecked = auth_repo.session_guard(session, auth_ctx["token_digest"], lock=True)
            if rechecked is None:
                raise invalid_session()
            issued_at_dt = rechecked["db_now"]
            session_expires_dt = rechecked["expires_at"]
            ttl_expiry = issued_at_dt + timedelta(seconds=self.settings.report_snapshot_ttl_seconds)
            expires_at_dt = min(ttl_expiry, session_expires_dt)
            if issued_at_dt >= expires_at_dt:
                raise invalid_session()

            issued_at_iso = serialize_utc(issued_at_dt)
            expires_at_iso = serialize_utc(expires_at_dt)
            assert issued_at_iso is not None and expires_at_iso is not None

            token_payload = {
                "purpose": REPORT_PURPOSE,
                "codec": REPORT_CODEC,
                "issued_at": issued_at_iso,
                "expires_at": expires_at_iso,
                "session_id": str(rechecked["session_id"]),
                "instance_generation": self.settings.dataset_generation,
                "report": report,
            }
            snapshot_token = sign_report_token(self.settings, token_payload)
            return {
                **report,
                "snapshot_token": snapshot_token,
                "expires_at": expires_at_iso,
            }

    def export_csv(
        self, cookie: str | None, csrf: str | None, body: ReportCsvRequest
    ) -> tuple[bytes, str]:
        with web_session_transaction(
            self.database,
            self.settings,
            cookie,
            csrf,
            write=False,
            require_csrf=True,
            lock_session=True,
            isolation="READ COMMITTED",
        ) as (session, auth_ctx):
            if self.settings.report_secret is None:
                raise storage_error()

            payload = verify_report_token(self.settings, body.snapshot_token)
            if payload["session_id"] != str(auth_ctx["session_id"]):
                fail(400, "TOKEN_INVALID", "Snapshot báo cáo không thuộc phiên hiện tại")

            expires_dt = parse_utc_datetime_ms(payload["expires_at"])
            if expires_dt > auth_ctx["expires_at"]:
                fail(400, "TOKEN_INVALID", "Thời hạn snapshot vượt quá thời hạn phiên")

            report = payload["report"]
            if (
                payload["instance_generation"] != self.settings.dataset_generation
                or report["context"]["source"] != self.settings.env
            ):
                fail(
                    409,
                    "REPORT_CONTEXT_CHANGED",
                    "Ngữ cảnh dữ liệu đã thay đổi, cần tạo lại báo cáo",
                )

            if auth_ctx["db_now"] >= expires_dt:
                fail(410, "SNAPSHOT_EXPIRED", "Snapshot báo cáo đã hết hạn, vui lòng tạo lại")

            node_ctx = repo.get_node_report_context(session, report["context"]["node"]["id"])
            if node_ctx is None:
                fail(404, "NOT_FOUND")
            if str(node_ctx["farm_id"]) != report["context"]["farm"]["id"]:
                fail(409, "REPORT_CONTEXT_CHANGED", "Ngữ cảnh Node/Farm đã thay đổi")

            csv_bytes, filename = build_report_csv_bytes(report)

            rechecked = auth_repo.session_guard(session, auth_ctx["token_digest"], lock=True)
            if rechecked is None:
                raise invalid_session()
            if rechecked["db_now"] >= expires_dt:
                fail(410, "SNAPSHOT_EXPIRED", "Snapshot báo cáo đã hết hạn, vui lòng tạo lại")

            return csv_bytes, filename
