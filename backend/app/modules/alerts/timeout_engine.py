from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from app.core.web import serialize_utc, utc_ms_from_datetime
from app.modules.alerts import repository as repo
from app.modules.areas import repository as areas_repo
from app.modules.devices import repository as devices_repo


class TimeoutStateError(Exception):
    """Raised when DB clock or persisted timeout state violates invariants."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ReconcileResult:
    closed: bool
    open_episode: dict[str, Any] | None
    current_revision: dict[str, Any] | None
    witness: dict[str, Any] | None


@dataclass(frozen=True)
class TimeoutEvaluationResult:
    reconciled_witness: bool
    period_changed: bool
    episode_opened: bool
    episode_closed: bool
    episode_updated: bool
    active_period_id: int | None


def build_evaluation_view(state_row: dict[str, Any]) -> dict[str, Any]:
    """Combine THRESHOLD (M2) and TIMEOUT (M3) engine error/generation status."""
    error_code = state_row.get("last_error_code") or state_row.get("timeout_last_error_code")
    desired = int(state_row["desired_generation"])
    completed = int(state_row["completed_generation"])
    if error_code is not None:
        status = "ERROR"
    elif completed < desired:
        status = "PENDING"
    else:
        status = "UP_TO_DATE"
    return {
        "desired_generation": str(desired),
        "completed_generation": str(completed),
        "status": status,
        "last_success_at": serialize_utc(state_row.get("last_success_at")),
        "last_error_code": error_code,
    }


def _load_open_episode_and_revision(
    session, node_id: int
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    open_ep = repo.lock_open_timeout_episode(session, node_id)
    if open_ep is None:
        return None, None
    rev_no = open_ep.get("current_revision_no")
    if rev_no is None:
        raise TimeoutStateError(
            "TIMEOUT_STATE_INCONSISTENT",
            "Open timeout episode is missing current_revision_no",
        )
    rev = repo.get_alert_revision(session, int(open_ep["episode_id"]), int(rev_no))
    if rev is None or rev.get("end_reason") is not None:
        raise TimeoutStateError(
            "TIMEOUT_STATE_INCONSISTENT",
            "Open timeout episode revision is missing or already closed",
        )
    return open_ep, rev


def reconcile_open_timeout(
    session,
    *,
    node_id: int,
    observed_now: datetime,
    evaluation_generation: int,
) -> ReconcileResult:
    """Close an OPEN timeout episode at the first Measurement stored after its opening baseline."""
    open_ep, current_rev = _load_open_episode_and_revision(session, node_id)
    if open_ep is None or current_rev is None:
        return ReconcileResult(closed=False, open_episode=None, current_revision=None, witness=None)

    witness = repo.find_first_measurement_after(
        session, node_id, current_rev.get("start_measurement_id")
    )
    if witness is None:
        return ReconcileResult(
            closed=False, open_episode=open_ep, current_revision=current_rev, witness=None
        )

    witness_ms = utc_ms_from_datetime(witness["persisted_at"])
    detected_ms = utc_ms_from_datetime(open_ep["first_detected_at"])
    observed_ms = utc_ms_from_datetime(observed_now)
    if witness_ms < detected_ms or witness_ms > observed_ms:
        raise TimeoutStateError(
            "TIMEOUT_CLOCK_INVALID",
            "Witness persisted_at violates timeout detection or current UTC clock bounds",
        )

    episode_id = int(open_ep["episode_id"])
    new_rev_no = int(current_rev["revision_no"]) + 1
    repo.insert_alert_revision(
        session,
        episode_id=episode_id,
        revision_no=new_rev_no,
        config_id=int(current_rev["config_id"]),
        period_id=current_rev["period_id"],
        direction=None,
        start_event_time=None,
        end_event_time=None,
        start_backend_at=current_rev["start_backend_at"],
        end_backend_at=witness["persisted_at"],
        closed_at=witness["persisted_at"],
        end_reason="NEW_DATA_STORED",
        start_measurement_id=current_rev["start_measurement_id"],
        end_measurement_id=int(witness["measurement_id"]),
        lower_snapshot=None,
        upper_snapshot=None,
        timeout_snapshot=current_rev["timeout_snapshot"],
        evaluated_at=observed_now,
        evaluation_generation=evaluation_generation,
        acknowledgement_version=int(open_ep["acknowledgement_version"]),
        threshold_metadata_json=None,
    )
    repo.update_alert_episode_pointer(
        session,
        episode_id=episode_id,
        current_revision_no=new_rev_no,
        lifecycle_status="CLOSED",
    )
    return ReconcileResult(closed=True, open_episode=None, current_revision=None, witness=witness)


def _close_open_timeout_episode(
    session,
    *,
    open_ep: dict[str, Any],
    current_rev: dict[str, Any],
    config_id: int,
    timeout_snapshot: int | None,
    closed_at: datetime,
    evaluated_at: datetime,
    end_reason: str,
    evaluation_generation: int,
) -> None:
    if utc_ms_from_datetime(closed_at) < utc_ms_from_datetime(open_ep["first_detected_at"]):
        raise TimeoutStateError(
            "TIMEOUT_CLOCK_INVALID",
            "Timeout closure time precedes episode first_detected_at",
        )
    episode_id = int(open_ep["episode_id"])
    new_rev_no = int(current_rev["revision_no"]) + 1
    repo.insert_alert_revision(
        session,
        episode_id=episode_id,
        revision_no=new_rev_no,
        config_id=int(config_id),
        period_id=current_rev["period_id"],
        direction=None,
        start_event_time=None,
        end_event_time=None,
        start_backend_at=current_rev["start_backend_at"],
        end_backend_at=closed_at,
        closed_at=closed_at,
        end_reason=end_reason,
        start_measurement_id=current_rev["start_measurement_id"],
        end_measurement_id=None,
        lower_snapshot=None,
        upper_snapshot=None,
        timeout_snapshot=timeout_snapshot,
        evaluated_at=evaluated_at,
        evaluation_generation=evaluation_generation,
        acknowledgement_version=int(open_ep["acknowledgement_version"]),
        threshold_metadata_json=None,
    )
    repo.update_alert_episode_pointer(
        session,
        episode_id=episode_id,
        current_revision_no=new_rev_no,
        lifecycle_status="CLOSED",
    )


def _append_timeout_config_revision(
    session,
    *,
    open_ep: dict[str, Any],
    current_rev: dict[str, Any],
    config_id: int,
    timeout_snapshot: int,
    evaluated_at: datetime,
    evaluation_generation: int,
) -> None:
    episode_id = int(open_ep["episode_id"])
    new_rev_no = int(current_rev["revision_no"]) + 1
    repo.insert_alert_revision(
        session,
        episode_id=episode_id,
        revision_no=new_rev_no,
        config_id=int(config_id),
        period_id=current_rev["period_id"],
        direction=None,
        start_event_time=None,
        end_event_time=None,
        start_backend_at=current_rev["start_backend_at"],
        end_backend_at=None,
        closed_at=None,
        end_reason=None,
        start_measurement_id=current_rev["start_measurement_id"],
        end_measurement_id=None,
        lower_snapshot=None,
        upper_snapshot=None,
        timeout_snapshot=int(timeout_snapshot),
        evaluated_at=evaluated_at,
        evaluation_generation=evaluation_generation,
        acknowledgement_version=int(open_ep["acknowledgement_version"]),
        threshold_metadata_json=None,
    )
    repo.update_alert_episode_pointer(
        session,
        episode_id=episode_id,
        current_revision_no=new_rev_no,
    )


def _open_timeout_episode(
    session,
    *,
    node_id: int,
    config_id: int,
    period_id: int,
    timeout_minutes: int,
    anchor_dt: datetime,
    detected_at: datetime,
    opening_last_measurement_id: int | None,
    evaluation_generation: int,
) -> int:
    episode_id = repo.insert_alert_episode(
        session,
        node_id=node_id,
        alert_kind="TIMEOUT",
        metric_code=None,
        lifecycle_status="OPEN",
        is_current=True,
        first_detected_at=detected_at,
        acknowledgement_version=1,
    )
    due_at = anchor_dt + timedelta(minutes=int(timeout_minutes))
    repo.insert_alert_revision(
        session,
        episode_id=episode_id,
        revision_no=1,
        config_id=int(config_id),
        period_id=int(period_id),
        direction=None,
        start_event_time=None,
        end_event_time=None,
        start_backend_at=due_at,
        end_backend_at=None,
        closed_at=None,
        end_reason=None,
        start_measurement_id=opening_last_measurement_id,
        end_measurement_id=None,
        lower_snapshot=None,
        upper_snapshot=None,
        timeout_snapshot=int(timeout_minutes),
        evaluated_at=detected_at,
        evaluation_generation=evaluation_generation,
        acknowledgement_version=1,
        threshold_metadata_json=None,
    )
    repo.update_alert_episode_pointer(
        session,
        episode_id=episode_id,
        current_revision_no=1,
    )
    return episode_id


def evaluate_timeout_in_tx(
    session,
    *,
    node_id: int,
    device_code: str,
    structurally_eligible: bool,
    runtime: dict[str, Any],
    observed_now: datetime,
    settings,
    evaluation_generation: int,
    is_config_change: bool = False,
    skip_reconcile: bool = False,
    open_period_reason: str = "PROFILE_RECOVERED",
) -> TimeoutEvaluationResult:
    """Evaluate and persist TIMEOUT period and AlertEpisode state inside an active locked TX."""
    reconciled_witness = False
    if skip_reconcile:
        open_ep, current_rev = _load_open_episode_and_revision(session, node_id)
    else:
        reconcile = reconcile_open_timeout(
            session,
            node_id=node_id,
            observed_now=observed_now,
            evaluation_generation=evaluation_generation,
        )
        reconciled_witness = reconcile.closed
        open_ep = reconcile.open_episode
        current_rev = reconcile.current_revision

    open_period = repo.get_open_timeout_period(session, node_id, lock=True)
    runtime_active_id = runtime.get("active_period_id")
    if (runtime_active_id is None) != (open_period is None) or (
        open_period is not None and int(runtime_active_id) != int(open_period["period_id"])
    ):
        raise TimeoutStateError(
            "TIMEOUT_STATE_INCONSISTENT",
            "NodeRuntime.active_period_id does not match open TIMEOUT MonitoringPeriod",
        )

    period_changed = False
    episode_opened = False
    episode_closed = False
    episode_updated = False
    active_period_id = int(open_period["period_id"]) if open_period is not None else None
    now_ms = utc_ms_from_datetime(observed_now)

    if not structurally_eligible:
        if open_period is not None:
            if now_ms < utc_ms_from_datetime(open_period["started_at"]):
                raise TimeoutStateError(
                    "TIMEOUT_CLOCK_INVALID",
                    "Observed DB UTC time precedes active timeout period started_at",
                )
            repo.close_monitoring_period(
                session,
                period_id=int(open_period["period_id"]),
                node_id=node_id,
                ended_at=observed_now,
                end_reason="MONITORING_PAUSED",
            )
            repo.update_node_runtime_active_period(session, node_id=node_id, active_period_id=None)
            active_period_id = None
            period_changed = True
        if open_ep is not None and current_rev is not None:
            _close_open_timeout_episode(
                session,
                open_ep=open_ep,
                current_rev=current_rev,
                config_id=int(current_rev["config_id"]),
                timeout_snapshot=current_rev["timeout_snapshot"],
                closed_at=observed_now,
                evaluated_at=observed_now,
                end_reason="MONITORING_PAUSED",
                evaluation_generation=evaluation_generation,
            )
            episode_closed = True
        return TimeoutEvaluationResult(
            reconciled_witness=reconciled_witness,
            period_changed=period_changed,
            episode_opened=False,
            episode_closed=episode_closed,
            episode_updated=False,
            active_period_id=active_period_id,
        )

    timeout_config_id = runtime.get("current_timeout_config_id")
    if timeout_config_id is None:
        if open_period is not None or open_ep is not None:
            raise TimeoutStateError(
                "TIMEOUT_STATE_INCONSISTENT",
                "Open timeout period or episode exists without current_timeout_config_id",
            )
        return TimeoutEvaluationResult(
            reconciled_witness=reconciled_witness,
            period_changed=False,
            episode_opened=False,
            episode_closed=False,
            episode_updated=False,
            active_period_id=None,
        )

    timeout_cfg = repo.get_timeout_config_by_id(session, node_id, int(timeout_config_id))
    if (
        timeout_cfg is None
        or timeout_cfg.get("effective_to") is not None
        or timeout_cfg.get("timeout_minutes") is None
    ):
        raise TimeoutStateError(
            "TIMEOUT_STATE_INCONSISTENT",
            "Active TIMEOUT NodeConfigVersion is missing, closed, or invalid",
        )
    timeout_minutes = int(timeout_cfg["timeout_minutes"])

    if open_period is None:
        if open_ep is not None:
            raise TimeoutStateError(
                "TIMEOUT_STATE_INCONSISTENT",
                "Open timeout episode exists without an active TIMEOUT MonitoringPeriod",
            )
        profile = settings.node_time_profiles.get(device_code)
        if profile is None or timeout_minutes * 60 <= profile.stale_after_seconds:
            return TimeoutEvaluationResult(
                reconciled_witness=reconciled_witness,
                period_changed=False,
                episode_opened=False,
                episode_closed=False,
                episode_updated=False,
                active_period_id=None,
            )
        new_period_id = repo.insert_monitoring_period(
            session,
            node_id=node_id,
            monitoring_kind="TIMEOUT",
            metric_code=None,
            started_at=observed_now,
            start_config_id=int(timeout_config_id),
            start_reason=open_period_reason,
        )
        repo.update_node_runtime_active_period(
            session, node_id=node_id, active_period_id=new_period_id
        )
        open_period = {
            "period_id": new_period_id,
            "started_at": observed_now,
            "start_config_id": int(timeout_config_id),
        }
        active_period_id = new_period_id
        period_changed = True

    s_dt = open_period["started_at"]
    l_dt = runtime.get("last_new_persisted_at")
    s_ms = utc_ms_from_datetime(s_dt)
    l_ms = utc_ms_from_datetime(l_dt) if l_dt is not None else None
    anchor_dt = s_dt if (l_ms is None or s_ms >= l_ms) else l_dt
    anchor_ms = s_ms if (l_ms is None or s_ms >= l_ms) else l_ms

    if now_ms < anchor_ms:
        raise TimeoutStateError(
            "TIMEOUT_CLOCK_INVALID",
            "Observed DB UTC time precedes max(S, L)",
        )

    elapsed_ms = now_ms - anchor_ms
    is_overdue = elapsed_ms >= timeout_minutes * 60_000

    if open_ep is None:
        if is_overdue:
            _open_timeout_episode(
                session,
                node_id=node_id,
                config_id=int(timeout_config_id),
                period_id=int(open_period["period_id"]),
                timeout_minutes=timeout_minutes,
                anchor_dt=anchor_dt,
                detected_at=observed_now,
                opening_last_measurement_id=runtime.get("last_measurement_id"),
                evaluation_generation=evaluation_generation,
            )
            episode_opened = True
    else:
        assert current_rev is not None
        if now_ms < utc_ms_from_datetime(open_ep["first_detected_at"]):
            raise TimeoutStateError(
                "TIMEOUT_CLOCK_INVALID",
                "Observed DB UTC time precedes open episode first_detected_at",
            )
        config_changed_on_episode = int(current_rev["config_id"]) != int(timeout_config_id) or (
            current_rev.get("timeout_snapshot") != timeout_minutes
        )
        if is_overdue:
            if config_changed_on_episode:
                _append_timeout_config_revision(
                    session,
                    open_ep=open_ep,
                    current_rev=current_rev,
                    config_id=int(timeout_config_id),
                    timeout_snapshot=timeout_minutes,
                    evaluated_at=observed_now,
                    evaluation_generation=evaluation_generation,
                )
                episode_updated = True
        else:
            if is_config_change or config_changed_on_episode:
                _close_open_timeout_episode(
                    session,
                    open_ep=open_ep,
                    current_rev=current_rev,
                    config_id=int(timeout_config_id),
                    timeout_snapshot=timeout_minutes,
                    closed_at=observed_now,
                    evaluated_at=observed_now,
                    end_reason="CONFIG_CHANGED",
                    evaluation_generation=evaluation_generation,
                )
                episode_closed = True
            else:
                raise TimeoutStateError(
                    "TIMEOUT_STATE_INCONSISTENT",
                    "Open timeout episode exists with E < T and no witness or config change",
                )

    return TimeoutEvaluationResult(
        reconciled_witness=reconciled_witness,
        period_changed=period_changed,
        episode_opened=episode_opened,
        episode_closed=episode_closed,
        episode_updated=episode_updated,
        active_period_id=active_period_id,
    )


def evaluate_timeout_node(
    session,
    *,
    node_id: int,
    settings,
    observed_now: datetime | None = None,
) -> TimeoutEvaluationResult:
    """Lock Farm->Zone->Gateway->Node->Runtime/State and evaluate TIMEOUT for a single Node."""
    parents = devices_repo.resolve_node_parents(session, str(node_id))
    if parents is None:
        raise TimeoutStateError("TIMEOUT_STATE_INCONSISTENT", "Node does not exist")

    farm = areas_repo.get_farm(session, str(parents["farm_id"]), lock=True)
    zone = areas_repo.get_zone(session, str(parents["zone_id"]), lock=True)
    gateway = (
        devices_repo.lock_gateway(session, parents["gateway_id"])
        if parents["gateway_id"] is not None
        else None
    )
    node = devices_repo.lock_node(session, node_id)
    if farm is None or zone is None or node is None:
        raise TimeoutStateError("TIMEOUT_STATE_INCONSISTENT", "Node parent hierarchy missing")
    if (
        int(node["zone_id"]) != int(parents["zone_id"])
        or node["gateway_id"] != parents["gateway_id"]
    ):
        raise TimeoutStateError(
            "TIMEOUT_STATE_INCONSISTENT",
            "Node hierarchy changed concurrently",
        )
    if node["gateway_id"] is not None and gateway is None:
        raise TimeoutStateError("TIMEOUT_STATE_INCONSISTENT", "Assigned Gateway missing")

    runtime = repo.lock_node_runtime(session, node_id)
    eval_state = repo.lock_evaluation_state(session, node_id)
    if runtime is None or eval_state is None:
        raise TimeoutStateError(
            "TIMEOUT_STATE_INCONSISTENT",
            "NodeRuntime or EvaluationState row is missing",
        )

    now_dt = observed_now if observed_now is not None else repo.get_db_utc_now(session)
    structurally_eligible = (
        farm["archived_at"] is None
        and zone["archived_at"] is None
        and bool(node["receiving_enabled"])
        and gateway is not None
        and int(gateway["farm_id"]) == int(zone["farm_id"])
        and bool(gateway["receiving_enabled"])
    )
    return evaluate_timeout_in_tx(
        session,
        node_id=node_id,
        device_code=node["device_code"],
        structurally_eligible=structurally_eligible,
        runtime=runtime,
        observed_now=now_dt,
        settings=settings,
        evaluation_generation=int(eval_state["desired_generation"]),
    )
