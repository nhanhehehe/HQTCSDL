import base64
import json
import random
import time
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from app.core.web import fail, web_session_transaction
from app.modules.alerts import repository as alerts_repo
from app.modules.alerts.timeout_engine import (
    TimeoutStateError,
    evaluate_timeout_in_tx,
    reconcile_open_timeout,
)
from app.modules.auth.service import storage_error
from app.modules.devices import repository as devices_repo
from app.modules.monitoring_config import repository as repo
from app.modules.monitoring_config.schemas import (
    METRIC_ORDER,
    parse_monitoring_patch_body,
    parse_receiving_patch_body,
)

MAX_DEADLOCK_RETRIES = 3


def _is_deadlock_error(exc: SQLAlchemyError) -> bool:
    return "1205" in str(getattr(exc, "orig", ""))


def _encode_row_version(raw: bytes | bytearray | memoryview) -> str:
    return base64.b64encode(bytes(raw)).decode()


class MonitoringConfigService:
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

    def _run_write_with_deadlock_retry(
        self,
        cookie: str | None,
        csrf: str | None,
        fn: Callable[[Any, dict[str, Any]], dict[str, Any]],
    ) -> dict[str, Any]:
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
                    return fn(session, auth_ctx)
            except SQLAlchemyError as exc:
                if _is_deadlock_error(exc) and attempt + 1 < MAX_DEADLOCK_RETRIES:
                    self.sleep_fn(random.uniform(0.05, 0.20))
                    continue
                raise storage_error() from None
        raise storage_error()

    def _lock_single_node_hierarchy(
        self, session, node_id: int
    ) -> tuple[
        dict[str, Any],
        dict[str, Any],
        dict[str, Any],
        dict[str, Any] | None,
        dict[str, Any],
        dict[str, Any],
        bool,
    ]:
        parents = devices_repo.resolve_node_parents(session, str(node_id))
        if parents is None:
            fail(404, "NOT_FOUND")

        farm_ids = [int(parents["farm_id"])]
        gw_id = parents["gateway_id"]
        if gw_id is not None:
            gw_farm_id = devices_repo.resolve_gateway_farm_id(session, str(gw_id))
            if gw_farm_id is None:
                raise storage_error()
            farm_ids.append(int(gw_farm_id))

        farms = repo.lock_farms_ordered(session, farm_ids)
        zones = repo.lock_zones_ordered(session, [int(parents["zone_id"])])
        gateway = devices_repo.lock_gateway(session, gw_id) if gw_id is not None else None
        node = devices_repo.lock_node(session, node_id)

        farm = farms.get(int(parents["farm_id"]))
        zone = zones.get(int(parents["zone_id"]))
        if farm is None or zone is None or node is None:
            fail(404, "NOT_FOUND")
        if (
            int(node["zone_id"]) != int(parents["zone_id"])
            or node["gateway_id"] != gw_id
            or int(zone["farm_id"]) != int(parents["farm_id"])
        ):
            raise storage_error()
        if gw_id is not None and gateway is None:
            raise storage_error()

        runtime = alerts_repo.lock_node_runtime(session, node_id)
        eval_state = alerts_repo.lock_evaluation_state(session, node_id)
        if runtime is None or eval_state is None:
            raise storage_error()

        structurally_eligible = (
            farm["archived_at"] is None
            and zone["archived_at"] is None
            and bool(node["receiving_enabled"])
            and gateway is not None
            and int(gateway["farm_id"]) == int(zone["farm_id"])
            and bool(gateway["receiving_enabled"])
        )
        return node, zone, farm, gateway, runtime, eval_state, structurally_eligible

    def _sync_node_monitoring_on_eligibility_change(
        self,
        session,
        *,
        node_id: int,
        device_code: str,
        now_eligible: bool,
        runtime: dict[str, Any],
        eval_state: dict[str, Any],
        observed_now,
    ) -> None:
        current_gen = int(eval_state["desired_generation"])
        try:
            reconcile = reconcile_open_timeout(
                session,
                node_id=node_id,
                observed_now=observed_now,
                evaluation_generation=current_gen,
            )
        except TimeoutStateError:
            raise storage_error() from None

        threshold_period_changed = False
        open_th_periods = repo.get_open_threshold_periods(session, node_id, lock=True)
        if not now_eligible:
            for metric_code in METRIC_ORDER:
                open_p = open_th_periods.get(metric_code)
                if open_p is not None:
                    alerts_repo.close_monitoring_period(
                        session,
                        period_id=int(open_p["period_id"]),
                        node_id=node_id,
                        ended_at=observed_now,
                        end_reason="MONITORING_PAUSED",
                    )
                    threshold_period_changed = True
        else:
            threshold_cfg = repo.get_current_config_version(
                session, node_id=node_id, config_kind="THRESHOLD", lock=True
            )
            if threshold_cfg is not None:
                th_settings = repo.get_threshold_settings(session, int(threshold_cfg["config_id"]))
                for metric_code in METRIC_ORDER:
                    if (
                        bool(th_settings[metric_code]["enabled"])
                        and metric_code not in open_th_periods
                    ):
                        alerts_repo.insert_monitoring_period(
                            session,
                            node_id=node_id,
                            monitoring_kind="THRESHOLD",
                            metric_code=metric_code,
                            started_at=observed_now,
                            start_config_id=int(threshold_cfg["config_id"]),
                            start_reason="RECEIVING_ENABLED",
                        )
                        threshold_period_changed = True

        try:
            timeout_res = evaluate_timeout_in_tx(
                session,
                node_id=node_id,
                device_code=device_code,
                structurally_eligible=now_eligible,
                runtime=runtime,
                observed_now=observed_now,
                settings=self.settings,
                evaluation_generation=current_gen,
                is_config_change=False,
                skip_reconcile=True,
                open_period_reason="RECEIVING_ENABLED",
            )
        except TimeoutStateError:
            raise storage_error() from None

        timeout_changed = (
            reconcile.closed
            or timeout_res.period_changed
            or timeout_res.episode_closed
            or timeout_res.episode_opened
        )
        if threshold_period_changed or timeout_changed:
            repo.bump_evaluation_generation(
                session,
                node_id=node_id,
                full_replay=threshold_period_changed,
            )

    def get_monitoring(self, cookie: str | None, *, node_id: str) -> dict[str, Any]:
        nid = int(node_id)
        try:
            with web_session_transaction(self.database, self.settings, cookie) as (session, _):
                ctx = repo.get_node_context(session, nid)
                if ctx is None:
                    fail(404, "NOT_FOUND")
                return repo.build_monitoring_state(
                    session,
                    node_id=nid,
                    device_code=ctx["device_code"],
                    settings=self.settings,
                    lock=False,
                )
        except SQLAlchemyError:
            raise storage_error() from None

    def patch_monitoring(
        self, cookie: str | None, csrf: str | None, *, node_id: str, raw_body: bytes
    ) -> dict[str, Any]:
        nid = int(node_id)

        def _op(session, auth_ctx: dict[str, Any]) -> dict[str, Any]:
            body = parse_monitoring_patch_body(raw_body)
            node, _, _, _, runtime, eval_state, structurally_eligible = (
                self._lock_single_node_hierarchy(session, nid)
            )

            threshold_cfg = repo.get_current_config_version(
                session, node_id=nid, config_kind="THRESHOLD", lock=True
            )
            timeout_cfg = repo.get_current_config_version(
                session, node_id=nid, config_kind="TIMEOUT", lock=True
            )
            actual_threshold_id = (
                int(threshold_cfg["config_id"]) if threshold_cfg is not None else None
            )
            actual_timeout_id = int(timeout_cfg["config_id"]) if timeout_cfg is not None else None
            if actual_threshold_id != runtime.get(
                "current_config_id"
            ) or actual_timeout_id != runtime.get("current_timeout_config_id"):
                raise storage_error()

            conflicts: dict[str, str | None] = {}
            if (
                body.thresholds is not None
                and body.expected_threshold_config_id != actual_threshold_id
            ):
                conflicts["current_threshold_config_id"] = (
                    str(actual_threshold_id) if actual_threshold_id is not None else None
                )
            if (
                body.timeout_minutes is not None
                and body.expected_timeout_config_id != actual_timeout_id
            ):
                conflicts["current_timeout_config_id"] = (
                    str(actual_timeout_id) if actual_timeout_id is not None else None
                )
            if conflicts:
                fail(
                    409,
                    "CONFIG_VERSION_CONFLICT",
                    "Cấu hình đã thay đổi, cần đọc lại",
                    details=conflicts,
                )

            thresholds_changed = False
            merged_settings: dict[str, dict[str, Any]] = {}
            if body.thresholds is not None:
                existing_settings = (
                    repo.get_threshold_settings(session, actual_threshold_id)
                    if actual_threshold_id is not None
                    else {
                        code: {"enabled": False, "lower_value": None, "upper_value": None}
                        for code in METRIC_ORDER
                    }
                )
                for metric_code in METRIC_ORDER:
                    cur = existing_settings[metric_code]
                    cur_en = bool(cur["enabled"])
                    cur_lo = Decimal(cur["lower_value"]) if cur["lower_value"] is not None else None
                    cur_hi = Decimal(cur["upper_value"]) if cur["upper_value"] is not None else None
                    patch = body.thresholds.get(metric_code)
                    if patch is None:
                        new_en, new_lo, new_hi = cur_en, cur_lo, cur_hi
                    else:
                        new_en = patch.enabled if patch.enabled is not None else cur_en
                        new_lo = patch.lower if patch.has_lower else cur_lo
                        new_hi = patch.upper if patch.has_upper else cur_hi

                    if new_lo is None and new_hi is None:
                        if new_en:
                            fail(
                                400,
                                "INPUT_INVALID",
                                f"Phải cấu hình đủ ngưỡng dưới/trên khi bật {metric_code}",
                            )
                    elif new_lo is None or new_hi is None:
                        fail(400, "INPUT_INVALID", f"Thiếu một đầu ngưỡng cho {metric_code}")
                    elif new_lo >= new_hi:
                        fail(
                            400,
                            "INPUT_INVALID",
                            f"Ngưỡng dưới phải nhỏ hơn ngưỡng trên cho {metric_code}",
                        )

                    if (new_en, new_lo, new_hi) != (cur_en, cur_lo, cur_hi):
                        thresholds_changed = True
                    merged_settings[metric_code] = {
                        "enabled": new_en,
                        "lower_value": new_lo,
                        "upper_value": new_hi,
                        "prev_enabled": cur_en,
                    }

            cur_timeout_minutes = (
                int(timeout_cfg["timeout_minutes"]) if timeout_cfg is not None else None
            )
            timeout_changed = (
                body.timeout_minutes is not None and body.timeout_minutes != cur_timeout_minutes
            )
            if timeout_changed:
                assert body.timeout_minutes is not None
                profile = self.settings.node_time_profiles.get(node["device_code"])
                if profile is None:
                    fail(
                        400,
                        "PROFILE_MISSING",
                        "Thiếu profile thời gian của Node để thay đổi timeout",
                    )
                min_timeout = (profile.stale_after_seconds // 60) + 1
                if body.timeout_minutes * 60 <= profile.stale_after_seconds:
                    fail(
                        400,
                        "INPUT_INVALID",
                        f"timeout_minutes phải lớn hơn {profile.stale_after_seconds} giây "
                        f"(tối thiểu {min_timeout} phút)",
                        details={
                            "reason": "TIMEOUT_PROFILE_INCOMPATIBLE",
                            "field": "timeout_minutes",
                            "min_timeout_minutes": min_timeout,
                        },
                    )

            if not thresholds_changed and not timeout_changed:
                return repo.build_monitoring_state(
                    session,
                    node_id=nid,
                    device_code=node["device_code"],
                    settings=self.settings,
                    lock=True,
                )

            now_c = alerts_repo.get_db_utc_now(session)
            pre_gen = int(eval_state["desired_generation"])
            new_gen = repo.bump_evaluation_generation(
                session, node_id=nid, full_replay=thresholds_changed
            )

            if timeout_changed:
                assert body.timeout_minutes is not None
                try:
                    reconcile_open_timeout(
                        session,
                        node_id=nid,
                        observed_now=now_c,
                        evaluation_generation=pre_gen,
                    )
                except TimeoutStateError:
                    raise storage_error() from None

                if timeout_cfg is not None:
                    assert actual_timeout_id is not None
                    repo.close_current_config_version(
                        session,
                        config_id=actual_timeout_id,
                        node_id=nid,
                        config_kind="TIMEOUT",
                        effective_to=now_c,
                    )
                next_t_no = repo.get_max_config_no(session, node_id=nid, config_kind="TIMEOUT") + 1
                new_t_cfg_id = repo.insert_config_version(
                    session,
                    node_id=nid,
                    config_kind="TIMEOUT",
                    config_no=next_t_no,
                    timeout_minutes=body.timeout_minutes,
                    effective_from=now_c,
                    created_by=int(auth_ctx["admin_id"]),
                    change_reason="USER_UPDATE",
                )
                repo.update_node_runtime_config_pointer(
                    session,
                    node_id=nid,
                    column="current_timeout_config_id",
                    config_id=new_t_cfg_id,
                )
                runtime["current_timeout_config_id"] = new_t_cfg_id
                try:
                    evaluate_timeout_in_tx(
                        session,
                        node_id=nid,
                        device_code=node["device_code"],
                        structurally_eligible=structurally_eligible,
                        runtime=runtime,
                        observed_now=now_c,
                        settings=self.settings,
                        evaluation_generation=new_gen,
                        is_config_change=True,
                        skip_reconcile=True,
                        open_period_reason="CONFIG_ENABLED",
                    )
                except TimeoutStateError:
                    raise storage_error() from None

            if thresholds_changed:
                if threshold_cfg is not None:
                    assert actual_threshold_id is not None
                    repo.close_current_config_version(
                        session,
                        config_id=actual_threshold_id,
                        node_id=nid,
                        config_kind="THRESHOLD",
                        effective_to=now_c,
                    )
                next_th_no = (
                    repo.get_max_config_no(session, node_id=nid, config_kind="THRESHOLD") + 1
                )
                new_th_cfg_id = repo.insert_config_version(
                    session,
                    node_id=nid,
                    config_kind="THRESHOLD",
                    config_no=next_th_no,
                    timeout_minutes=None,
                    effective_from=now_c,
                    created_by=int(auth_ctx["admin_id"]),
                    change_reason="USER_UPDATE",
                )
                repo.insert_threshold_settings(
                    session, config_id=new_th_cfg_id, settings_by_metric=merged_settings
                )
                repo.update_node_runtime_config_pointer(
                    session,
                    node_id=nid,
                    column="current_config_id",
                    config_id=new_th_cfg_id,
                )
                open_th_periods = repo.get_open_threshold_periods(session, nid, lock=True)
                for metric_code in METRIC_ORDER:
                    item = merged_settings[metric_code]
                    new_en = bool(item["enabled"])
                    open_p = open_th_periods.get(metric_code)
                    if not new_en:
                        if open_p is not None:
                            alerts_repo.close_monitoring_period(
                                session,
                                period_id=int(open_p["period_id"]),
                                node_id=nid,
                                ended_at=now_c,
                                end_reason="THRESHOLD_DISABLED",
                            )
                        open_ep = alerts_repo.lock_open_threshold_episode(session, nid, metric_code)
                        if open_ep is not None:
                            rev_no = open_ep.get("current_revision_no")
                            if rev_no is None:
                                raise storage_error()
                            cur_rev = alerts_repo.get_alert_revision(
                                session, int(open_ep["episode_id"]), int(rev_no)
                            )
                            if cur_rev is None or cur_rev.get("end_reason") is not None:
                                raise storage_error()
                            new_rev_no = int(rev_no) + 1
                            alerts_repo.insert_alert_revision(
                                session,
                                episode_id=int(open_ep["episode_id"]),
                                revision_no=new_rev_no,
                                config_id=new_th_cfg_id,
                                period_id=cur_rev["period_id"],
                                direction=cur_rev["direction"],
                                start_event_time=cur_rev["start_event_time"],
                                end_event_time=None,
                                start_backend_at=cur_rev["start_backend_at"],
                                end_backend_at=now_c,
                                closed_at=now_c,
                                end_reason="THRESHOLD_DISABLED",
                                start_measurement_id=cur_rev["start_measurement_id"],
                                end_measurement_id=None,
                                lower_snapshot=cur_rev["lower_snapshot"],
                                upper_snapshot=cur_rev["upper_snapshot"],
                                timeout_snapshot=None,
                                evaluated_at=now_c,
                                evaluation_generation=new_gen,
                                acknowledgement_version=int(open_ep["acknowledgement_version"]),
                                threshold_metadata_json=json.dumps(
                                    {
                                        "codec": 1,
                                        "is_current": True,
                                        "transition": "THRESHOLD_DISABLED",
                                    },
                                    separators=(",", ":"),
                                ),
                            )
                            alerts_repo.update_alert_episode_pointer(
                                session,
                                episode_id=int(open_ep["episode_id"]),
                                current_revision_no=new_rev_no,
                                lifecycle_status="CLOSED",
                            )
                    else:
                        if structurally_eligible and open_p is None:
                            alerts_repo.insert_monitoring_period(
                                session,
                                node_id=nid,
                                monitoring_kind="THRESHOLD",
                                metric_code=metric_code,
                                started_at=now_c,
                                start_config_id=new_th_cfg_id,
                                start_reason="CONFIG_ENABLED",
                            )
                        elif not structurally_eligible and open_p is not None:
                            raise storage_error()

            return repo.build_monitoring_state(
                session,
                node_id=nid,
                device_code=node["device_code"],
                settings=self.settings,
                lock=True,
            )

        return self._run_write_with_deadlock_retry(cookie, csrf, _op)

    def patch_node_receiving(
        self, cookie: str | None, csrf: str | None, *, node_id: str, raw_body: bytes
    ) -> dict[str, Any]:
        nid = int(node_id)

        def _op(session, _auth_ctx: dict[str, Any]) -> dict[str, Any]:
            body = parse_receiving_patch_body(raw_body)
            node, zone, farm, gateway, runtime, eval_state, was_eligible = (
                self._lock_single_node_hierarchy(session, nid)
            )
            if bytes(node["row_ver"]) != body.expected_row_version:
                fail(409, "VERSION_CONFLICT")

            current_enabled = bool(node["receiving_enabled"])
            if current_enabled == body.receiving_enabled:
                mon = repo.build_monitoring_state(
                    session,
                    node_id=nid,
                    device_code=node["device_code"],
                    settings=self.settings,
                    lock=True,
                )
                return {
                    "profile_row_version": _encode_row_version(node["row_ver"]),
                    "receiving_enabled": current_enabled,
                    "monitoring": mon,
                    "evaluation": mon["evaluation"],
                }

            if body.receiving_enabled and (
                farm["archived_at"] is not None or zone["archived_at"] is not None
            ):
                fail(
                    409,
                    "AREA_ARCHIVED",
                    "Không thể bật quyền nhận khi Farm hoặc Zone đang lưu trữ",
                )

            now_c = alerts_repo.get_db_utc_now(session)
            updated = repo.update_node_receiving(
                session,
                node_id=nid,
                expected_version=body.expected_row_version,
                enabled=body.receiving_enabled,
            )
            if updated is None:
                fail(409, "VERSION_CONFLICT")

            now_eligible = (
                farm["archived_at"] is None
                and zone["archived_at"] is None
                and body.receiving_enabled
                and gateway is not None
                and int(gateway["farm_id"]) == int(zone["farm_id"])
                and bool(gateway["receiving_enabled"])
            )
            self._sync_node_monitoring_on_eligibility_change(
                session,
                node_id=nid,
                device_code=node["device_code"],
                now_eligible=now_eligible,
                runtime=runtime,
                eval_state=eval_state,
                observed_now=now_c,
            )
            mon = repo.build_monitoring_state(
                session,
                node_id=nid,
                device_code=node["device_code"],
                settings=self.settings,
                lock=True,
            )
            return {
                "profile_row_version": _encode_row_version(updated["row_ver"]),
                "receiving_enabled": bool(updated["receiving_enabled"]),
                "monitoring": mon,
                "evaluation": mon["evaluation"],
            }

        return self._run_write_with_deadlock_retry(cookie, csrf, _op)

    def patch_gateway_receiving(
        self, cookie: str | None, csrf: str | None, *, gateway_id: str, raw_body: bytes
    ) -> dict[str, Any]:
        gid = int(gateway_id)

        def _op(session, _auth_ctx: dict[str, Any]) -> dict[str, Any]:
            body = parse_receiving_patch_body(raw_body)
            ctx = repo.resolve_gateway_context(session, gid)
            if ctx is None:
                fail(404, "NOT_FOUND")

            farms = repo.lock_farms_ordered(session, ctx["farm_ids"])
            zones = repo.lock_zones_ordered(session, ctx["zone_ids"])
            gw = devices_repo.lock_gateway(session, gid)
            if gw is None or int(gw["farm_id"]) != ctx["farm_id"]:
                fail(404, "NOT_FOUND")
            nodes = repo.lock_gateway_nodes(session, gid)

            node_states: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []
            for n in nodes:
                n_id = int(n["node_id"])
                z_id = int(n["zone_id"])
                if z_id not in zones or int(zones[z_id]["farm_id"]) not in farms:
                    raise storage_error()
                runtime = alerts_repo.lock_node_runtime(session, n_id)
                eval_state = alerts_repo.lock_evaluation_state(session, n_id)
                if runtime is None or eval_state is None:
                    raise storage_error()
                node_states.append((n, runtime, eval_state))

            if bytes(gw["row_ver"]) != body.expected_row_version:
                fail(409, "VERSION_CONFLICT")

            current_enabled = bool(gw["receiving_enabled"])
            if current_enabled == body.receiving_enabled:
                affected = [
                    repo.build_monitoring_state(
                        session,
                        node_id=int(n["node_id"]),
                        device_code=n["device_code"],
                        settings=self.settings,
                        lock=True,
                    )
                    for n in nodes
                ]
                return {
                    "profile_row_version": _encode_row_version(gw["row_ver"]),
                    "receiving_enabled": current_enabled,
                    "affected_nodes": affected,
                }

            gw_farm = farms.get(int(gw["farm_id"]))
            if gw_farm is None:
                fail(404, "NOT_FOUND")
            if body.receiving_enabled and gw_farm["archived_at"] is not None:
                fail(
                    409,
                    "AREA_ARCHIVED",
                    "Không thể bật quyền nhận khi Farm của Gateway đang lưu trữ",
                )

            now_c = alerts_repo.get_db_utc_now(session)
            updated_gw = repo.update_gateway_receiving(
                session,
                gateway_id=gid,
                expected_version=body.expected_row_version,
                enabled=body.receiving_enabled,
            )
            if updated_gw is None:
                fail(409, "VERSION_CONFLICT")

            for n, runtime, eval_state in node_states:
                n_id = int(n["node_id"])
                z = zones[int(n["zone_id"])]
                f = farms[int(z["farm_id"])]
                area_ok = (
                    f["archived_at"] is None
                    and z["archived_at"] is None
                    and int(z["farm_id"]) == int(gw["farm_id"])
                )
                now_eligible = area_ok and bool(n["receiving_enabled"]) and body.receiving_enabled
                self._sync_node_monitoring_on_eligibility_change(
                    session,
                    node_id=n_id,
                    device_code=n["device_code"],
                    now_eligible=now_eligible,
                    runtime=runtime,
                    eval_state=eval_state,
                    observed_now=now_c,
                )

            affected = [
                repo.build_monitoring_state(
                    session,
                    node_id=int(n["node_id"]),
                    device_code=n["device_code"],
                    settings=self.settings,
                    lock=True,
                )
                for n in nodes
            ]
            return {
                "profile_row_version": _encode_row_version(updated_gw["row_ver"]),
                "receiving_enabled": bool(updated_gw["receiving_enabled"]),
                "affected_nodes": affected,
            }

        return self._run_write_with_deadlock_retry(cookie, csrf, _op)
