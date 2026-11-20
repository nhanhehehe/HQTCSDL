import random
import time
from collections.abc import Callable
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from app.core.errors import WebError
from app.core.web import (
    fail,
    parse_rfc3339_utc,
    parse_utc_datetime_ms,
    resolve_event_range,
    serialize_utc,
    web_session_transaction,
)
from app.modules.auth.service import storage_error
from app.modules.measurements import repository as repo
from app.modules.measurements.tokens import (
    make_view_payload,
    sign_measurement_cursor,
    sign_view_token,
    verify_measurement_cursor,
    verify_view_token,
)

MAX_DEADLOCK_RETRIES = 3


def _is_deadlock(exc: SQLAlchemyError) -> bool:
    return "1205" in str(getattr(exc, "orig", ""))


class MeasurementsService:
    def __init__(self, database, settings, *, sleep_fn: Callable[[float], None] | None = None):
        self.database = database
        self.settings = settings
        self.sleep_fn = sleep_fn or time.sleep

    def get_current(self, cookie: str | None, *, node_id: str) -> dict[str, Any]:
        with web_session_transaction(self.database, self.settings, cookie) as (session, auth_ctx):
            node = repo.get_node_meta(session, node_id)
            if node is None:
                fail(404, "NOT_FOUND")
            as_of_iso = serialize_utc(auth_ctx["db_now"])
            assert as_of_iso is not None
            return repo.read_node_current(
                session,
                node_id=int(node["node_id"]),
                device_code=node["device_code"],
                as_of_iso=as_of_iso,
            )

    def _capture_cap_with_retry(self, cookie: str | None, *, node_id: str) -> tuple[int, Any]:
        for attempt in range(MAX_DEADLOCK_RETRIES + 1):
            try:
                with web_session_transaction(
                    self.database,
                    self.settings,
                    cookie,
                    write=False,
                    require_csrf=False,
                    lock_session=False,
                    isolation="READ COMMITTED",
                    propagate_deadlock=True,
                ) as (session, _):
                    captured = repo.capture_node_measurement_cap(session, node_id)
                    if captured is None:
                        fail(404, "NOT_FOUND")
                    return captured
            except SQLAlchemyError as exc:
                if _is_deadlock(exc) and attempt < MAX_DEADLOCK_RETRIES:
                    self.sleep_fn(random.uniform(0.05, 0.2))
                    continue
                raise storage_error() from None
        raise storage_error()

    def _resolve_view_and_boundary(
        self,
        cookie: str | None,
        *,
        node_id: str,
        time_mode: str,
        route: str,
        order: str,
        page_size: int,
        from_raw: str | None,
        to_raw: str | None,
        cursor: str | None,
        view_token: str | None,
    ) -> tuple[dict[str, Any], str, dict[str, str] | None]:
        generation = self.settings.dataset_generation
        if cursor is None and view_token is None:
            if time_mode == "known":
                parsed_from = None
                parsed_to = None
                try:
                    if from_raw is not None:
                        parsed_from = parse_rfc3339_utc(from_raw)
                    if to_raw is not None:
                        parsed_to = parse_rfc3339_utc(to_raw)
                except ValueError:
                    raise WebError(
                        400, "RANGE_INVALID", "Định dạng thời gian không hợp lệ"
                    ) from None
                if (
                    parsed_from is not None
                    and parsed_to is not None
                    and parsed_from.exact_epoch > parsed_to.exact_epoch
                ):
                    raise WebError(
                        400,
                        "RANGE_INVALID",
                        "Thời gian bắt đầu phải nhỏ hơn hoặc bằng kết thúc",
                    )
            cap, as_of_dt = self._capture_cap_with_retry(cookie, node_id=node_id)
            as_of_iso = serialize_utc(as_of_dt)
            assert as_of_iso is not None
            if time_mode == "known":
                resolved = resolve_event_range(from_raw, to_raw, as_of_dt=as_of_dt)
                from_iso = resolved.from_ts.utc_iso
                to_iso = resolved.to_ts.utc_iso
            else:
                from_iso = None
                to_iso = None
            view_payload = make_view_payload(
                generation=generation,
                node_id=node_id,
                mode=time_mode,
                from_iso=from_iso,
                to_iso=to_iso,
                cap=cap,
                as_of=as_of_iso,
            )
            token_str = sign_view_token(self.settings, view_payload)
            return view_payload, token_str, None

        view_from_cursor: dict[str, Any] | None = None
        boundary: dict[str, str] | None = None
        if cursor is not None:
            cursor_data = verify_measurement_cursor(
                self.settings,
                cursor,
                expected_route=route,
                expected_order=order,
                expected_page_size=page_size,
                expected_generation=generation,
                expected_node_id=node_id,
                expected_mode=time_mode,
            )
            view_from_cursor = cursor_data["view"]
            boundary = cursor_data["boundary"]

        view_from_token: dict[str, Any] | None = None
        if view_token is not None:
            view_from_token = verify_view_token(
                self.settings,
                view_token,
                expected_generation=generation,
                expected_node_id=node_id,
                expected_mode=time_mode,
            )

        if view_from_cursor is not None and view_from_token is not None:
            if view_from_cursor != view_from_token:
                fail(400, "CURSOR_INVALID")
            view_payload = view_from_token
        elif view_from_cursor is not None:
            view_payload = view_from_cursor
        else:
            assert view_from_token is not None
            view_payload = view_from_token

        if time_mode == "known":
            locked_from = parse_rfc3339_utc(view_payload["from"])
            locked_to = parse_rfc3339_utc(view_payload["to"])
            if from_raw is not None:
                try:
                    req_from = parse_rfc3339_utc(from_raw)
                except ValueError:
                    raise WebError(
                        400, "RANGE_INVALID", "Định dạng thời gian không hợp lệ"
                    ) from None
                if req_from.exact_epoch != locked_from.exact_epoch:
                    fail(400, "CURSOR_INVALID")
            if to_raw is not None:
                try:
                    req_to = parse_rfc3339_utc(to_raw)
                except ValueError:
                    raise WebError(
                        400, "RANGE_INVALID", "Định dạng thời gian không hợp lệ"
                    ) from None
                if req_to.exact_epoch != locked_to.exact_epoch:
                    fail(400, "CURSOR_INVALID")

        token_str = sign_view_token(self.settings, view_payload)
        return view_payload, token_str, boundary

    def _query_view_page(
        self,
        cookie: str | None,
        *,
        node_id: str,
        time_mode: str,
        route: str,
        order: str,
        ascending: bool,
        page_size: int,
        from_raw: str | None,
        to_raw: str | None,
        cursor: str | None,
        view_token: str | None,
    ) -> dict[str, Any]:
        if time_mode == "unknown" and (from_raw is not None or to_raw is not None):
            fail(
                400,
                "INPUT_INVALID",
                "Không áp dụng bộ lọc from/to cho chế độ chưa xác định giờ đo",
            )

        view_payload, token_str, boundary = self._resolve_view_and_boundary(
            cookie,
            node_id=node_id,
            time_mode=time_mode,
            route=route,
            order=order,
            page_size=page_size,
            from_raw=from_raw,
            to_raw=to_raw,
            cursor=cursor,
            view_token=view_token,
        )
        cap = int(view_payload["cap"])

        with web_session_transaction(self.database, self.settings, cookie) as (session, _):
            node = repo.get_node_meta(session, node_id)
            if node is None:
                fail(404, "NOT_FOUND")
            nid = int(node["node_id"])
            device_code = node["device_code"]

            if time_mode == "known":
                from_ts = parse_rfc3339_utc(view_payload["from"])
                to_ts = parse_rfc3339_utc(view_payload["to"])
                lo = from_ts.whole_epoch_sec + (1 if from_ts.frac > 0 else 0)
                hi = to_ts.whole_epoch_sec
                parsed_boundary = (
                    {
                        "t_node": int(boundary["t_node"]),
                        "boot_id": int(boundary["boot_id"]),
                        "seq": int(boundary["seq"]),
                    }
                    if boundary is not None
                    else None
                )
                rows = repo.list_known_headers(
                    session,
                    node_id=nid,
                    lo=lo,
                    hi=hi,
                    cap=cap,
                    ascending=ascending,
                    boundary=parsed_boundary,
                    limit=page_size + 1,
                )
            else:
                b_persisted = (
                    parse_utc_datetime_ms(boundary["persisted_at"])
                    if boundary is not None
                    else None
                )
                b_mid = int(boundary["measurement_id"]) if boundary is not None else None
                rows = repo.list_unknown_headers(
                    session,
                    node_id=nid,
                    cap=cap,
                    boundary_persisted_at=b_persisted,
                    boundary_measurement_id=b_mid,
                    limit=page_size + 1,
                )

            page_headers = rows[:page_size]
            values_map = repo.load_measurement_values_map(
                session, [int(r["measurement_id"]) for r in page_headers]
            )

            next_cursor: str | None = None
            if len(rows) > page_size:
                last = page_headers[-1]
                if time_mode == "known":
                    next_boundary = {
                        "t_node": str(last["t_node"]),
                        "boot_id": str(last["boot_id"]),
                        "seq": str(last["seq"]),
                    }
                else:
                    persisted_iso = serialize_utc(last["persisted_at"])
                    assert persisted_iso is not None
                    next_boundary = {
                        "persisted_at": persisted_iso,
                        "measurement_id": str(last["measurement_id"]),
                    }
                next_cursor = sign_measurement_cursor(
                    self.settings,
                    route=route,
                    order=order,
                    page_size=page_size,
                    view_payload=view_payload,
                    boundary=next_boundary,
                )

            if page_headers:
                state = "AVAILABLE"
            else:
                total_count, known_count = repo.count_node_existence_up_to_cap(
                    session, node_id=nid, cap=cap
                )
                if total_count == 0:
                    state = "NO_DATA"
                elif time_mode == "known" and known_count == 0:
                    state = "NO_EVENT_TIME"
                else:
                    state = "EMPTY_RANGE"

        return {
            "state": state,
            "headers": page_headers,
            "values_map": values_map,
            "device_code": device_code,
            "next_cursor": next_cursor,
            "view_token": token_str,
            "range": (
                {"from": view_payload["from"], "to": view_payload["to"]}
                if time_mode == "known"
                else None
            ),
            "as_of": view_payload["as_of"],
        }

    def list_measurements(
        self,
        cookie: str | None,
        *,
        node_id: str,
        time_mode: str,
        from_raw: str | None,
        to_raw: str | None,
        page_size: int,
        cursor: str | None,
        view_token: str | None,
    ) -> dict[str, Any]:
        route = "measurements-known" if time_mode == "known" else "measurements-unknown"
        order = "event-desc" if time_mode == "known" else "unknown-desc"
        page = self._query_view_page(
            cookie,
            node_id=node_id,
            time_mode=time_mode,
            route=route,
            order=order,
            ascending=False,
            page_size=page_size,
            from_raw=from_raw,
            to_raw=to_raw,
            cursor=cursor,
            view_token=view_token,
        )
        items = [
            repo.build_measurement_view(
                h,
                page["values_map"][int(h["measurement_id"])],
                device_code=page["device_code"],
            )
            for h in page["headers"]
        ]
        return {
            "state": page["state"],
            "items": items,
            "next_cursor": page["next_cursor"],
            "view_token": page["view_token"],
            "range": page["range"],
            "as_of": page["as_of"],
        }

    def get_chart(
        self,
        cookie: str | None,
        *,
        node_id: str,
        from_raw: str | None,
        to_raw: str | None,
        page_size: int,
        cursor: str | None,
        view_token: str | None,
    ) -> dict[str, Any]:
        page = self._query_view_page(
            cookie,
            node_id=node_id,
            time_mode="known",
            route="measurements-chart",
            order="event-asc",
            ascending=True,
            page_size=page_size,
            from_raw=from_raw,
            to_raw=to_raw,
            cursor=cursor,
            view_token=view_token,
        )
        points = [
            repo.build_chart_point(
                h,
                page["values_map"][int(h["measurement_id"])],
                device_code=page["device_code"],
            )
            for h in page["headers"]
        ]
        return {
            "state": page["state"],
            "points": points,
            "next_cursor": page["next_cursor"],
            "view_token": page["view_token"],
            "range": page["range"],
            "as_of": page["as_of"],
        }
