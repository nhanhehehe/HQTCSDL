import random
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from app.core.web import (
    fail,
    parse_rfc3339_utc,
    serialize_utc,
    sign_cursor,
    utc_ms_from_datetime,
    verify_cursor,
    web_session_transaction,
)
from app.modules.alerts import repository as repo
from app.modules.alerts.schemas import (
    build_acknowledgement_dto,
    build_alert_detail,
    build_alert_summary,
    build_replacement_dto,
    build_revision_dto,
    parse_acknowledgement_body,
)
from app.modules.alerts.threshold_codec import datetime_from_utc_ms
from app.modules.alerts.timeout_engine import build_evaluation_view
from app.modules.auth.service import storage_error
from app.modules.devices import repository as devices_repo
from app.modules.measurements import repository as meas_repo
from app.modules.monitoring_config import repository as mc_repo

MAX_DEADLOCK_RETRIES = 3


def _is_deadlock_error(exc: SQLAlchemyError) -> bool:
    return "1205" in str(getattr(exc, "orig", ""))


def _is_pk_ack_violation(exc: SQLAlchemyError) -> bool:
    msg = str(getattr(exc, "orig", ""))
    return ("2627" in msg or "2601" in msg) and "AlertAcknowledgement" in msg


def _ceil_ms_from_rfc3339(raw: str) -> tuple[datetime, str]:
    parsed = parse_rfc3339_utc(raw)
    exact_ms = Decimal(parsed.whole_epoch_sec) * Decimal(1000) + parsed.frac * Decimal(1000)
    ms_int = int(exact_ms.to_integral_value(rounding=ROUND_CEILING))
    return datetime_from_utc_ms(ms_int), parsed.utc_iso


def _floor_ms_from_rfc3339(raw: str) -> tuple[datetime, str]:
    parsed = parse_rfc3339_utc(raw)
    exact_ms = Decimal(parsed.whole_epoch_sec) * Decimal(1000) + parsed.frac * Decimal(1000)
    ms_int = int(exact_ms.to_integral_value(rounding=ROUND_FLOOR))
    return datetime_from_utc_ms(ms_int), parsed.utc_iso


def _parse_optional_bool(raw: str | None, field: str) -> bool | None:
    if raw is None:
        return None
    if raw == "true":
        return True
    if raw == "false":
        return False
    fail(400, "INPUT_INVALID", f"{field} phải là true hoặc false")


def _parse_positive_int_str(raw: str | None, field: str) -> int | None:
    if raw is None:
        return None
    if not raw.isascii() or not raw.isdecimal() or int(raw) < 1:
        fail(400, "INPUT_INVALID", f"{field} phải là số nguyên dương")
    return int(raw)


class AlertsService:
    def __init__(
        self,
        database,
        settings,
        *,
        sleep_fn: Callable[[float], None] | None = None,
    ):
        self.database = database
        self.settings = settings
        self.sleep_fn = sleep_fn or time.sleep

    def list_alerts(
        self,
        cookie: str | None,
        *,
        farm_id: str,
        node_id: str | None = None,
        view: str = "active",
        kind: str | None = None,
        lifecycle: str | None = None,
        acknowledged_raw: str | None = None,
        recovery_proven_raw: str | None = None,
        from_raw: str | None = None,
        to_raw: str | None = None,
        page_size: int = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        if view not in {"active", "history"}:
            fail(400, "INPUT_INVALID", "view phải là active hoặc history")
        if kind is not None and kind not in {"THRESHOLD", "TIMEOUT"}:
            fail(400, "INPUT_INVALID", "kind phải là THRESHOLD hoặc TIMEOUT")
        if lifecycle is not None and lifecycle not in {"OPEN", "CLOSED"}:
            fail(400, "INPUT_INVALID", "lifecycle phải là OPEN hoặc CLOSED")
        if view == "active" and lifecycle == "CLOSED":
            fail(400, "INPUT_INVALID", "Không thể lọc lifecycle=CLOSED trên view=active")

        acknowledged = _parse_optional_bool(acknowledged_raw, "acknowledged")
        recovery_proven = _parse_optional_bool(recovery_proven_raw, "recovery_proven")
        if kind == "TIMEOUT" and recovery_proven is not None:
            fail(400, "INPUT_INVALID", "Không thể lọc recovery_proven cho cảnh báo TIMEOUT")

        boundary_dt: datetime | None = None
        boundary_eid: int | None = None
        cursor_range_from: str | None = None
        cursor_range_to: str | None = None

        if cursor is not None:
            decoded = verify_cursor(self.settings, cursor)
            if decoded.get("purpose") != "alerts-list-cursor":
                fail(400, "CURSOR_INVALID")
            if (
                decoded.get("generation") != self.settings.dataset_generation
                or decoded.get("farm_id") != farm_id
                or decoded.get("node_id") != node_id
                or decoded.get("view") != view
                or decoded.get("kind") != kind
                or decoded.get("lifecycle") != lifecycle
                or decoded.get("acknowledged") != acknowledged
                or decoded.get("recovery_proven") != recovery_proven
                or decoded.get("page_size") != page_size
            ):
                fail(400, "CURSOR_INVALID")
            b_ms = decoded.get("b_ms")
            b_id = decoded.get("b_id")
            if type(b_ms) is not int or type(b_id) is not int or b_ms < 0 or b_id < 1:
                fail(400, "CURSOR_INVALID")
            boundary_dt = datetime_from_utc_ms(b_ms)
            boundary_eid = b_id
            cursor_range_from = decoded.get("range_from")
            cursor_range_to = decoded.get("range_to")

        with web_session_transaction(self.database, self.settings, cookie) as (session, auth_ctx):
            as_of_dt = auth_ctx["db_now"]
            as_of_iso = serialize_utc(as_of_dt)
            assert as_of_iso is not None

            farm = meas_repo.get_farm_summary(session, farm_id)
            if farm is None:
                fail(404, "NOT_FOUND")

            if node_id is not None:
                node_row = meas_repo.get_dashboard_node(session, node_id)
                if node_row is None:
                    fail(404, "NOT_FOUND")
                if int(node_row["farm_id"]) != int(farm_id):
                    fail(400, "INPUT_INVALID", "Node không thuộc Farm đã chọn")
                scope_nodes = [node_row]
                scope_kind = "NODE"
            else:
                scope_nodes = meas_repo.list_farm_dashboard_nodes(session, farm_id)
                scope_kind = "FARM"

            # Resolve range with ms ceil/floor boundaries; fix history 24h range across cursors.
            from_dt: datetime | None = None
            to_dt: datetime | None = None
            range_from_iso: str | None = None
            range_to_iso: str | None = None

            if cursor is not None and from_raw is None and to_raw is None:
                range_from_iso = cursor_range_from
                range_to_iso = cursor_range_to
                if range_from_iso is not None:
                    from_dt, _ = _ceil_ms_from_rfc3339(range_from_iso)
                if range_to_iso is not None:
                    to_dt, _ = _floor_ms_from_rfc3339(range_to_iso)
            else:
                try:
                    if view == "history":
                        if to_raw is not None:
                            to_dt, range_to_iso = _floor_ms_from_rfc3339(to_raw)
                            to_exact = parse_rfc3339_utc(to_raw).exact_epoch
                        else:
                            to_dt = as_of_dt
                            range_to_iso = as_of_iso
                            to_exact = Decimal(utc_ms_from_datetime(as_of_dt)) / Decimal(1000)

                        if from_raw is not None:
                            from_dt, range_from_iso = _ceil_ms_from_rfc3339(from_raw)
                            from_exact = parse_rfc3339_utc(from_raw).exact_epoch
                        else:
                            from_dt = to_dt - timedelta(hours=24)
                            range_from_iso = serialize_utc(from_dt)
                            from_exact = Decimal(utc_ms_from_datetime(from_dt)) / Decimal(1000)

                        if from_exact > to_exact:
                            fail(400, "RANGE_INVALID", "from phải nhỏ hơn hoặc bằng to")
                    else:
                        if from_raw is not None:
                            from_dt, range_from_iso = _ceil_ms_from_rfc3339(from_raw)
                        if to_raw is not None:
                            to_dt, range_to_iso = _floor_ms_from_rfc3339(to_raw)
                        if from_raw is not None and to_raw is not None:
                            if (
                                parse_rfc3339_utc(from_raw).exact_epoch
                                > parse_rfc3339_utc(to_raw).exact_epoch
                            ):
                                fail(400, "RANGE_INVALID", "from phải nhỏ hơn hoặc bằng to")
                except ValueError:
                    fail(400, "RANGE_INVALID", "Định dạng thời gian không hợp lệ")

                if cursor is not None and (
                    range_from_iso != cursor_range_from or range_to_iso != cursor_range_to
                ):
                    fail(400, "CURSOR_INVALID")

            rows = repo.list_alerts_page(
                session,
                farm_id=int(farm_id),
                node_id=int(node_id) if node_id is not None else None,
                view=view,
                kind=kind,
                lifecycle=lifecycle,
                acknowledged=acknowledged,
                recovery_proven=recovery_proven,
                from_dt=from_dt,
                to_dt=to_dt,
                boundary_detected_at=boundary_dt,
                boundary_episode_id=boundary_eid,
                limit=page_size + 1,
            )

            has_more = len(rows) > page_size
            page_rows = rows[:page_size]
            items = [build_alert_summary(r) for r in page_rows]

            next_cursor: str | None = None
            if has_more and page_rows:
                last = page_rows[-1]
                next_cursor = sign_cursor(
                    self.settings,
                    {
                        "purpose": "alerts-list-cursor",
                        "generation": self.settings.dataset_generation,
                        "farm_id": farm_id,
                        "node_id": node_id,
                        "view": view,
                        "kind": kind,
                        "lifecycle": lifecycle,
                        "acknowledged": acknowledged,
                        "recovery_proven": recovery_proven,
                        "page_size": page_size,
                        "range_from": range_from_iso,
                        "range_to": range_to_iso,
                        "b_ms": utc_ms_from_datetime(last["first_detected_at"]),
                        "b_id": int(last["episode_id"]),
                    },
                )

            scope_nids = [int(n["node_id"]) for n in scope_nodes]
            eval_map = repo.list_evaluation_states_for_nodes(session, scope_nids)
            if len(eval_map) != len(scope_nids):
                raise storage_error()
            evaluation_by_node = {
                str(nid): build_evaluation_view(eval_map[nid]) for nid in scope_nids
            }

            return {
                "as_of": as_of_iso,
                "scope": {
                    "kind": scope_kind,
                    "farm_id": str(farm["farm_id"]),
                    "farm": {
                        "id": str(farm["farm_id"]),
                        "code": farm["farm_code"],
                        "name": farm["name"],
                        "archived_at": serialize_utc(farm["archived_at"]),
                    },
                    "node_id": node_id,
                },
                "view": view,
                "range": {"from": range_from_iso, "to": range_to_iso},
                "evaluation_by_node": evaluation_by_node,
                "items": items,
                "next_cursor": next_cursor,
            }

    def get_alert_detail(
        self,
        cookie: str | None,
        *,
        episode_id: str,
        audit_kind: str | None = None,
        audit_cursor: str | None = None,
        audit_page_size: int = 50,
        acknowledgement_version_raw: str | None = None,
    ) -> dict[str, Any]:
        eid = int(episode_id)
        if audit_kind is not None and audit_kind not in {
            "revisions",
            "acknowledgements",
            "replacements",
        }:
            fail(
                400,
                "INPUT_INVALID",
                "audit_kind phải là revisions, acknowledgements hoặc replacements",
            )
        if audit_kind is None and (
            audit_cursor is not None or acknowledgement_version_raw is not None
        ):
            fail(400, "INPUT_INVALID", "Tham số audit yêu cầu audit_kind")

        exact_ack_ver = _parse_positive_int_str(
            acknowledgement_version_raw, "acknowledgement_version"
        )
        if exact_ack_ver is not None:
            if audit_kind != "acknowledgements":
                fail(
                    400,
                    "INPUT_INVALID",
                    "acknowledgement_version chỉ dùng với audit_kind=acknowledgements",
                )
            if audit_cursor is not None:
                fail(
                    400,
                    "INPUT_INVALID",
                    "Không dùng đồng thời audit_cursor và acknowledgement_version",
                )

        with web_session_transaction(self.database, self.settings, cookie) as (session, auth_ctx):
            as_of_iso = serialize_utc(auth_ctx["db_now"])
            row = repo.get_alert_episode_context(session, eid, lock_episode=False)
            if row is None:
                fail(404, "NOT_FOUND")
            detail = build_alert_detail(row)
            eval_state = repo.get_evaluation_state(session, int(row["node_id"]))
            if eval_state is None:
                raise storage_error()
            evaluation = build_evaluation_view(eval_state)

            audit_payload: dict[str, Any] | None = None
            if audit_kind == "revisions":
                b_rev: int | None = None
                if audit_cursor is not None:
                    decoded = verify_cursor(self.settings, audit_cursor)
                    if (
                        decoded.get("purpose") != "alerts-audit-cursor"
                        or decoded.get("generation") != self.settings.dataset_generation
                        or decoded.get("episode_id") != episode_id
                        or decoded.get("audit_kind") != "revisions"
                        or decoded.get("page_size") != audit_page_size
                        or type(decoded.get("b_rev")) is not int
                    ):
                        fail(400, "CURSOR_INVALID")
                    b_rev = decoded["b_rev"]
                rev_rows = repo.list_audit_revisions(
                    session,
                    episode_id=eid,
                    boundary_rev_no=b_rev,
                    limit=audit_page_size + 1,
                )
                has_more = len(rev_rows) > audit_page_size
                page = rev_rows[:audit_page_size]
                next_cur = (
                    sign_cursor(
                        self.settings,
                        {
                            "purpose": "alerts-audit-cursor",
                            "generation": self.settings.dataset_generation,
                            "episode_id": episode_id,
                            "audit_kind": "revisions",
                            "page_size": audit_page_size,
                            "b_rev": int(page[-1]["revision_no"]),
                        },
                    )
                    if has_more and page
                    else None
                )
                audit_payload = {
                    "kind": "revisions",
                    "items": [build_revision_dto(r) for r in page],
                    "next_cursor": next_cur,
                }
            elif audit_kind == "acknowledgements":
                b_ver: int | None = None
                if audit_cursor is not None:
                    decoded = verify_cursor(self.settings, audit_cursor)
                    if (
                        decoded.get("purpose") != "alerts-audit-cursor"
                        or decoded.get("generation") != self.settings.dataset_generation
                        or decoded.get("episode_id") != episode_id
                        or decoded.get("audit_kind") != "acknowledgements"
                        or decoded.get("page_size") != audit_page_size
                        or type(decoded.get("b_ver")) is not int
                    ):
                        fail(400, "CURSOR_INVALID")
                    b_ver = decoded["b_ver"]
                ack_rows = repo.list_audit_acknowledgements(
                    session,
                    episode_id=eid,
                    exact_version=exact_ack_ver,
                    boundary_ack_ver=b_ver,
                    limit=1 if exact_ack_ver is not None else audit_page_size + 1,
                )
                has_more = exact_ack_ver is None and len(ack_rows) > audit_page_size
                page = ack_rows if exact_ack_ver is not None else ack_rows[:audit_page_size]
                next_cur = (
                    sign_cursor(
                        self.settings,
                        {
                            "purpose": "alerts-audit-cursor",
                            "generation": self.settings.dataset_generation,
                            "episode_id": episode_id,
                            "audit_kind": "acknowledgements",
                            "page_size": audit_page_size,
                            "b_ver": int(page[-1]["acknowledgement_version"]),
                        },
                    )
                    if has_more and page
                    else None
                )
                audit_payload = {
                    "kind": "acknowledgements",
                    "items": [build_acknowledgement_dto(r) for r in page],
                    "next_cursor": next_cur,
                }
            elif audit_kind == "replacements":
                b_repl: tuple[datetime, int, int, int, int] | None = None
                if audit_cursor is not None:
                    decoded = verify_cursor(self.settings, audit_cursor)
                    if (
                        decoded.get("purpose") != "alerts-audit-cursor"
                        or decoded.get("generation") != self.settings.dataset_generation
                        or decoded.get("episode_id") != episode_id
                        or decoded.get("audit_kind") != "replacements"
                        or decoded.get("page_size") != audit_page_size
                    ):
                        fail(400, "CURSOR_INVALID")
                    b_vals = decoded.get("b_repl")
                    if (
                        not isinstance(b_vals, list)
                        or len(b_vals) != 5
                        or any(type(x) is not int for x in b_vals)
                    ):
                        fail(400, "CURSOR_INVALID")
                    b_repl = (
                        datetime_from_utc_ms(b_vals[0]),
                        b_vals[1],
                        b_vals[2],
                        b_vals[3],
                        b_vals[4],
                    )
                repl_rows = repo.list_audit_replacements(
                    session,
                    episode_id=eid,
                    boundary=b_repl,
                    limit=audit_page_size + 1,
                )
                has_more = len(repl_rows) > audit_page_size
                page = repl_rows[:audit_page_size]
                next_cur = (
                    sign_cursor(
                        self.settings,
                        {
                            "purpose": "alerts-audit-cursor",
                            "generation": self.settings.dataset_generation,
                            "episode_id": episode_id,
                            "audit_kind": "replacements",
                            "page_size": audit_page_size,
                            "b_repl": [
                                utc_ms_from_datetime(page[-1]["created_at"]),
                                int(page[-1]["old_episode_id"]),
                                int(page[-1]["old_revision_no"]),
                                int(page[-1]["new_episode_id"]),
                                int(page[-1]["new_revision_no"]),
                            ],
                        },
                    )
                    if has_more and page
                    else None
                )
                audit_payload = {
                    "kind": "replacements",
                    "items": [build_replacement_dto(r) for r in page],
                    "next_cursor": next_cur,
                }

            return {
                "as_of": as_of_iso,
                "episode": detail,
                "evaluation": evaluation,
                "audit": audit_payload,
            }

    def create_acknowledgement(
        self,
        cookie: str | None,
        csrf: str | None,
        *,
        episode_id: str,
        raw_body: bytes,
    ) -> tuple[int, dict[str, Any]]:
        eid = int(episode_id)
        body = parse_acknowledgement_body(raw_body)

        for attempt in range(MAX_DEADLOCK_RETRIES):
            try:
                with web_session_transaction(
                    self.database,
                    self.settings,
                    cookie,
                    csrf,
                    write=True,
                    propagate_deadlock=True,
                ) as (session, auth_ctx):
                    node_id = repo.resolve_episode_node_id(session, eid)
                    if node_id is None:
                        fail(404, "NOT_FOUND")
                    parents = devices_repo.resolve_node_parents(session, str(node_id))
                    if parents is None:
                        fail(404, "NOT_FOUND")

                    farm_ids = [int(parents["farm_id"])]
                    gw_id = parents["gateway_id"]
                    if gw_id is not None:
                        gw_farm_id = devices_repo.resolve_gateway_farm_id(session, str(gw_id))
                        if gw_farm_id is not None:
                            farm_ids.append(int(gw_farm_id))
                    mc_repo.lock_farms_ordered(session, farm_ids)
                    mc_repo.lock_zones_ordered(session, [int(parents["zone_id"])])
                    if gw_id is not None:
                        devices_repo.lock_gateway(session, gw_id)
                    devices_repo.lock_node(session, node_id)

                    ep = repo.lock_alert_episode_by_id(session, eid)
                    if ep is None:
                        fail(404, "NOT_FOUND")
                    if ep.get("current_revision_no") is None:
                        raise storage_error()

                    current_ack_row = repo.get_alert_acknowledgement(
                        session, eid, int(ep["acknowledgement_version"])
                    )
                    current_state = {
                        "episode_id": str(ep["episode_id"]),
                        "is_current": bool(ep["is_current"]),
                        "lifecycle_status": ep["lifecycle_status"],
                        "current_revision_no": int(ep["current_revision_no"]),
                        "acknowledgement_version": int(ep["acknowledgement_version"]),
                        "acknowledged": current_ack_row is not None,
                    }

                    # FL-11 §6-7: Verify viewed_revision_no belongs to episode & version first.
                    viewed_rev = repo.get_alert_revision(session, eid, body.viewed_revision_no)
                    if viewed_rev is None:
                        fail(400, "INPUT_INVALID", "viewed_revision_no không thuộc cảnh báo này")
                    if int(viewed_rev["acknowledgement_version"]) != body.acknowledgement_version:
                        fail(
                            409,
                            "ALERT_VERSION_CONFLICT",
                            "viewed_revision_no không khớp với acknowledgement_version yêu cầu",
                            details={"current_state": current_state},
                        )

                    # Exact retry lookup for (episode_id, requested acknowledgement_version).
                    existing_req_ack = repo.get_alert_acknowledgement(
                        session, eid, body.acknowledgement_version
                    )
                    if existing_req_ack is not None:
                        return 200, {
                            "acknowledgement": build_acknowledgement_dto(existing_req_ack),
                            "current_state": current_state,
                        }

                    # Reject stale or retired episode before insert.
                    if (
                        not bool(ep["is_current"])
                        or int(ep["acknowledgement_version"]) != body.acknowledgement_version
                    ):
                        fail(
                            409,
                            "ALERT_VERSION_CONFLICT",
                            "Cảnh báo đã thay đổi phiên bản hoặc không còn hiện hành",
                            details={"current_state": current_state},
                        )

                    now_dt = repo.get_db_utc_now(session)
                    repo.insert_alert_acknowledgement(
                        session,
                        episode_id=eid,
                        acknowledgement_version=body.acknowledgement_version,
                        acknowledged_revision_no=body.viewed_revision_no,
                        admin_id=int(auth_ctx["admin_id"]),
                        acknowledged_at=now_dt,
                        note=body.note,
                    )
                    inserted = repo.get_alert_acknowledgement(
                        session, eid, body.acknowledgement_version
                    )
                    if inserted is None:
                        raise storage_error()
                    current_state["acknowledged"] = True
                    return 201, {
                        "acknowledgement": build_acknowledgement_dto(inserted),
                        "current_state": current_state,
                    }
            except SQLAlchemyError as exc:
                if (_is_deadlock_error(exc) or _is_pk_ack_violation(exc)) and (
                    attempt + 1 < MAX_DEADLOCK_RETRIES
                ):
                    self.sleep_fn(random.uniform(0.05, 0.20))
                    continue
                raise storage_error() from None
        raise storage_error()
