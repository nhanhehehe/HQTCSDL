from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.core.web import utc_ms_from_datetime
from app.modules.alerts import repository as repo
from app.modules.alerts.threshold_codec import (
    PHASE_CONFIG,
    PHASE_PERIOD_END,
    PHASE_PERIOD_START,
    PHASE_SAMPLE,
    CheckpointEpisode,
    EventKey,
    ThresholdCheckpoint,
    ThresholdStateError,
    datetime_from_utc_ms,
    encode_threshold_metadata,
    parse_and_validate_checkpoint,
)
from app.modules.areas import repository as areas_repo
from app.modules.devices import repository as devices_repo
from app.modules.monitoring_config.schemas import METRIC_ORDER


@dataclass(frozen=True)
class ThresholdEvaluationResult:
    episodes_created: int
    episodes_updated: int
    episodes_retired: int
    replacements_created: int
    watermark_measurement_id: int | None
    frontier_key: EventKey | None


@dataclass(frozen=True)
class _PeriodEndEvent:
    key: EventKey
    period_id: int
    ended_at: datetime
    end_reason: str


@dataclass(frozen=True)
class _ConfigEvent:
    key: EventKey
    config_id: int
    config_no: int
    effective_from: datetime
    enabled: bool
    lower_value: Decimal | None
    upper_value: Decimal | None


@dataclass(frozen=True)
class _PeriodStartEvent:
    key: EventKey
    period_id: int
    started_at: datetime
    start_config_id: int


@dataclass(frozen=True)
class _SampleEvent:
    key: EventKey
    measurement_id: int
    t_node: int
    persisted_at: datetime
    value: Decimal


_MetricEvent = _PeriodEndEvent | _ConfigEvent | _PeriodStartEvent | _SampleEvent


@dataclass
class _WorkingEpisode:
    metric_code: str
    direction: str
    config_id: int
    period_id: int
    start_key: EventKey
    start_event_time: int
    start_backend_at: datetime
    start_measurement_id: int
    lower_snapshot: Decimal
    upper_snapshot: Decimal
    bad_measurement_ids: list[int]
    clipped_config_id: int | None = None
    clipped_lower: Decimal | None = None
    clipped_upper: Decimal | None = None
    end_key: EventKey | None = None
    end_event_time: int | None = None
    end_backend_at: datetime | None = None
    closed_at: datetime | None = None
    end_reason: str | None = None
    end_measurement_id: int | None = None


@dataclass(frozen=True)
class _ReplayedEpisode:
    metric_code: str
    direction: str
    config_id: int
    period_id: int
    start_key: EventKey
    start_event_time: int
    start_backend_at: datetime
    start_measurement_id: int
    lower_snapshot: Decimal
    upper_snapshot: Decimal
    bad_measurement_ids: tuple[int, ...]
    end_key: EventKey | None
    end_event_time: int | None
    end_backend_at: datetime | None
    closed_at: datetime | None
    end_reason: str | None
    end_measurement_id: int | None
    # Prefix snapshot clipped at F_old (when F_old is not None and start_key <= F_old)
    clipped_config_id: int | None
    clipped_lower: Decimal | None
    clipped_upper: Decimal | None
    clipped_end_key: EventKey | None
    clipped_end_reason: str | None
    clipped_end_measurement_id: int | None

    @property
    def is_open(self) -> bool:
        return self.end_key is None

    @property
    def lifecycle_status(self) -> str:
        return "OPEN" if self.end_key is None else "CLOSED"

    @property
    def semantic_key(self) -> tuple[Any, ...]:
        return (
            self.metric_code,
            self.start_key,
            self.direction,
            self.start_measurement_id,
        )


def _classify_value(value: Decimal, lower: Decimal, upper: Decimal) -> str | None:
    if value < lower:
        return "LOW"
    if value > upper:
        return "HIGH"
    return None


def _to_decimal_9_2(raw: Any) -> Decimal | None:
    if raw is None:
        return None
    d = raw if isinstance(raw, Decimal) else Decimal(str(raw))
    return Decimal(f"{d:.2f}")


def _clipped_intervals_overlap(
    old_ep: CheckpointEpisode, new_ep: _ReplayedEpisode, f_old: EventKey
) -> bool:
    """Check half-open interval overlap [start, end) or [start, +inf) clipped at F_old."""
    s_old = old_ep.start_key
    e_old = old_ep.end_key if (old_ep.end_key is not None and old_ep.end_key <= f_old) else None

    s_new = new_ep.start_key
    e_new = new_ep.end_key if (new_ep.end_key is not None and new_ep.end_key <= f_old) else None

    if s_new > f_old:
        return False

    if e_old is None and e_new is None:
        return True
    if e_old is None:
        assert e_new is not None
        return s_old < e_new
    if e_new is None:
        return s_new < e_old
    return s_old < e_new and s_new < e_old


def _prefix_matches_at_frontier(
    old_ep: CheckpointEpisode, new_ep: _ReplayedEpisode, f_old: EventKey | None
) -> bool:
    if f_old is None or new_ep.start_key > f_old:
        return False
    return (
        old_ep.metric_code == new_ep.metric_code
        and old_ep.direction == new_ep.direction
        and old_ep.period_id == new_ep.period_id
        and old_ep.start_key == new_ep.start_key
        and old_ep.start_measurement_id == new_ep.start_measurement_id
        and old_ep.config_id == new_ep.clipped_config_id
        and old_ep.lower_snapshot == new_ep.clipped_lower
        and old_ep.upper_snapshot == new_ep.clipped_upper
        and old_ep.end_key == new_ep.clipped_end_key
        and old_ep.end_reason == new_ep.clipped_end_reason
        and old_ep.end_measurement_id == new_ep.clipped_end_measurement_id
    )


def _live_revision_matches_replayed(live_row: dict[str, Any], new_ep: _ReplayedEpisode) -> bool:
    """Compare live DB revision against replayed episode (ignoring only bad_measurement_ids)."""
    expected_status = "OPEN" if new_ep.is_open else "CLOSED"
    if (
        live_row["metric_code"] != new_ep.metric_code
        or live_row.get("direction") != new_ep.direction
        or live_row["lifecycle_status"] != expected_status
        or live_row.get("period_id") != new_ep.period_id
        or live_row.get("start_measurement_id") != new_ep.start_measurement_id
        or live_row.get("start_event_time") != new_ep.start_event_time
        or live_row.get("end_reason") != new_ep.end_reason
        or live_row.get("end_measurement_id") != new_ep.end_measurement_id
        or live_row.get("end_event_time") != new_ep.end_event_time
    ):
        return False

    live_lo = _to_decimal_9_2(live_row.get("lower_snapshot"))
    live_hi = _to_decimal_9_2(live_row.get("upper_snapshot"))
    if live_lo != new_ep.lower_snapshot or live_hi != new_ep.upper_snapshot:
        return False

    if new_ep.end_reason == "THRESHOLD_DISABLED":
        if new_ep.closed_at is None or live_row.get("closed_at") is None:
            return False
        if utc_ms_from_datetime(live_row["closed_at"]) != utc_ms_from_datetime(new_ep.closed_at):
            return False
        return True

    if int(live_row["config_id"]) != new_ep.config_id:
        return False
    if new_ep.closed_at is not None:
        if live_row.get("closed_at") is None:
            return False
        if utc_ms_from_datetime(live_row["closed_at"]) != utc_ms_from_datetime(new_ep.closed_at):
            return False
    return True


def _replay_metric_chronicle(
    *,
    metric_code: str,
    configs: list[dict[str, Any]],
    periods: list[dict[str, Any]],
    measurements: list[dict[str, Any]],
    f_old: EventKey | None,
) -> tuple[list[_ReplayedEpisode], EventKey | None]:
    events: list[_MetricEvent] = []
    max_key: EventKey | None = None

    def _track_key(key: EventKey) -> EventKey:
        nonlocal max_key
        if max_key is None or key > max_key:
            max_key = key
        return key

    for cfg in configs:
        settings_map = cfg.get("settings") or {}
        if set(settings_map.keys()) != set(METRIC_ORDER):
            raise ThresholdStateError(
                "THRESHOLD_STATE_INCONSISTENT",
                f"THRESHOLD NodeConfigVersion {cfg['config_id']} is missing metric settings",
            )
        setting = settings_map[metric_code]
        enabled = bool(setting["enabled"])
        lo = _to_decimal_9_2(setting["lower_value"])
        hi = _to_decimal_9_2(setting["upper_value"])
        if enabled and (lo is None or hi is None or lo >= hi):
            raise ThresholdStateError(
                "THRESHOLD_STATE_INCONSISTENT",
                f"Enabled ThresholdSetting {cfg['config_id']}/{metric_code} has invalid bounds",
            )
        eff_dt = cfg["effective_from"]
        key = _track_key(
            EventKey(
                event_time_ms=utc_ms_from_datetime(eff_dt),
                phase=PHASE_CONFIG,
                tie_a=int(cfg["config_no"]),
                tie_b=0,
            )
        )
        events.append(
            _ConfigEvent(
                key=key,
                config_id=int(cfg["config_id"]),
                config_no=int(cfg["config_no"]),
                effective_from=eff_dt,
                enabled=enabled,
                lower_value=lo,
                upper_value=hi,
            )
        )

    for period in periods:
        if period.get("metric_code") != metric_code:
            continue
        pid = int(period["period_id"])
        started_dt = period["started_at"]
        start_key = _track_key(
            EventKey(
                event_time_ms=utc_ms_from_datetime(started_dt),
                phase=PHASE_PERIOD_START,
                tie_a=pid,
                tie_b=0,
            )
        )
        events.append(
            _PeriodStartEvent(
                key=start_key,
                period_id=pid,
                started_at=started_dt,
                start_config_id=int(period["start_config_id"]),
            )
        )
        ended_dt = period.get("ended_at")
        if ended_dt is not None:
            end_reason = period.get("end_reason")
            if not end_reason:
                raise ThresholdStateError(
                    "THRESHOLD_STATE_INCONSISTENT",
                    f"Closed MonitoringPeriod {pid} is missing end_reason",
                )
            end_key = _track_key(
                EventKey(
                    event_time_ms=utc_ms_from_datetime(ended_dt),
                    phase=PHASE_PERIOD_END,
                    tie_a=pid,
                    tie_b=0,
                )
            )
            events.append(
                _PeriodEndEvent(
                    key=end_key,
                    period_id=pid,
                    ended_at=ended_dt,
                    end_reason=end_reason,
                )
            )

    for meas in measurements:
        values_map = meas.get("values") or {}
        if set(values_map.keys()) != set(METRIC_ORDER):
            raise ThresholdStateError(
                "THRESHOLD_STATE_INCONSISTENT",
                f"Measurement {meas['measurement_id']} does not have all 5 MeasurementValue rows",
            )
        val_row = values_map[metric_code]
        t_node = int(meas["t_node"])
        sample_key = _track_key(
            EventKey(
                event_time_ms=t_node * 1000,
                phase=PHASE_SAMPLE,
                tie_a=int(meas["boot_id"]),
                tie_b=int(meas["seq"]),
            )
        )
        if val_row["read_status"] != "OK":
            continue
        val = _to_decimal_9_2(val_row.get("value"))
        if val is None:
            raise ThresholdStateError(
                "THRESHOLD_STATE_INCONSISTENT",
                f"MeasurementValue {meas['measurement_id']}/{metric_code} is OK with NULL value",
            )
        events.append(
            _SampleEvent(
                key=sample_key,
                measurement_id=int(meas["measurement_id"]),
                t_node=t_node,
                persisted_at=meas["persisted_at"],
                value=val,
            )
        )

    events.sort(key=lambda ev: ev.key)

    active_config: _ConfigEvent | None = None
    active_period: _PeriodStartEvent | None = None
    ended_period_ids: set[int] = set()
    last_eligible_sample: _SampleEvent | None = None
    open_ep: _WorkingEpisode | None = None
    finished: list[_ReplayedEpisode] = []

    def _finalize_episode(
        ep: _WorkingEpisode,
        *,
        end_key: EventKey | None = None,
        end_event_time: int | None = None,
        end_backend_at: datetime | None = None,
        closed_at: datetime | None = None,
        end_reason: str | None = None,
        end_measurement_id: int | None = None,
    ) -> _ReplayedEpisode:
        if f_old is not None and end_key is not None and end_key <= f_old:
            clipped_end_key: EventKey | None = end_key
            clipped_end_reason: str | None = end_reason
            clipped_end_mid: int | None = end_measurement_id
        else:
            clipped_end_key = None
            clipped_end_reason = None
            clipped_end_mid = None
        return _ReplayedEpisode(
            metric_code=ep.metric_code,
            direction=ep.direction,
            config_id=ep.config_id,
            period_id=ep.period_id,
            start_key=ep.start_key,
            start_event_time=ep.start_event_time,
            start_backend_at=ep.start_backend_at,
            start_measurement_id=ep.start_measurement_id,
            lower_snapshot=ep.lower_snapshot,
            upper_snapshot=ep.upper_snapshot,
            bad_measurement_ids=tuple(ep.bad_measurement_ids),
            end_key=end_key,
            end_event_time=end_event_time,
            end_backend_at=end_backend_at,
            closed_at=closed_at,
            end_reason=end_reason,
            end_measurement_id=end_measurement_id,
            clipped_config_id=ep.clipped_config_id,
            clipped_lower=ep.clipped_lower,
            clipped_upper=ep.clipped_upper,
            clipped_end_key=clipped_end_key,
            clipped_end_reason=clipped_end_reason,
            clipped_end_measurement_id=clipped_end_mid,
        )

    def _open_new_episode(
        sample: _SampleEvent,
        *,
        direction: str,
        cfg: _ConfigEvent,
        period: _PeriodStartEvent,
    ) -> _WorkingEpisode:
        assert cfg.lower_value is not None and cfg.upper_value is not None
        in_old_prefix = f_old is not None and sample.key <= f_old
        return _WorkingEpisode(
            metric_code=metric_code,
            direction=direction,
            config_id=cfg.config_id,
            period_id=period.period_id,
            start_key=sample.key,
            start_event_time=sample.t_node,
            start_backend_at=sample.persisted_at,
            start_measurement_id=sample.measurement_id,
            lower_snapshot=cfg.lower_value,
            upper_snapshot=cfg.upper_value,
            bad_measurement_ids=[sample.measurement_id],
            clipped_config_id=cfg.config_id if in_old_prefix else None,
            clipped_lower=cfg.lower_value if in_old_prefix else None,
            clipped_upper=cfg.upper_value if in_old_prefix else None,
        )

    for ev in events:
        if isinstance(ev, _PeriodEndEvent):
            ended_period_ids.add(ev.period_id)
            if active_period is not None and active_period.period_id == ev.period_id:
                active_period = None
            if ev.end_reason == "THRESHOLD_DISABLED" and open_ep is not None:
                finished.append(
                    _finalize_episode(
                        open_ep,
                        end_key=ev.key,
                        end_event_time=None,
                        end_backend_at=ev.ended_at,
                        closed_at=ev.ended_at,
                        end_reason="THRESHOLD_DISABLED",
                        end_measurement_id=None,
                    )
                )
                open_ep = None
            continue

        if isinstance(ev, _ConfigEvent):
            active_config = ev
            if open_ep is not None:
                if not ev.enabled:
                    finished.append(
                        _finalize_episode(
                            open_ep,
                            end_key=ev.key,
                            end_event_time=None,
                            end_backend_at=ev.effective_from,
                            closed_at=ev.effective_from,
                            end_reason="THRESHOLD_DISABLED",
                            end_measurement_id=None,
                        )
                    )
                    open_ep = None
                elif active_period is not None:
                    assert ev.lower_value is not None and ev.upper_value is not None
                    if last_eligible_sample is None:
                        raise ThresholdStateError(
                            "THRESHOLD_STATE_INCONSISTENT",
                            "Open threshold episode has no prior eligible sample at CONFIG_CHANGED",
                        )
                    new_dir = _classify_value(
                        last_eligible_sample.value, ev.lower_value, ev.upper_value
                    )
                    if new_dir == open_ep.direction:
                        open_ep.config_id = ev.config_id
                        open_ep.lower_snapshot = ev.lower_value
                        open_ep.upper_snapshot = ev.upper_value
                        if f_old is not None and ev.key <= f_old:
                            open_ep.clipped_config_id = ev.config_id
                            open_ep.clipped_lower = ev.lower_value
                            open_ep.clipped_upper = ev.upper_value
                    else:
                        # Close with CONFIG_CHANGED; never open from old sample.
                        finished.append(
                            _finalize_episode(
                                open_ep,
                                end_key=ev.key,
                                end_event_time=None,
                                end_backend_at=ev.effective_from,
                                closed_at=ev.effective_from,
                                end_reason="CONFIG_CHANGED",
                                end_measurement_id=None,
                            )
                        )
                        open_ep = None
            continue

        if isinstance(ev, _PeriodStartEvent):
            if ev.period_id not in ended_period_ids:
                active_period = ev
            continue

        # _SampleEvent (phase=3)
        if active_period is None or active_config is None or not active_config.enabled:
            continue
        assert active_config.lower_value is not None and active_config.upper_value is not None
        last_eligible_sample = ev
        breach_dir = _classify_value(ev.value, active_config.lower_value, active_config.upper_value)
        if open_ep is None:
            if breach_dir is not None:
                open_ep = _open_new_episode(
                    ev, direction=breach_dir, cfg=active_config, period=active_period
                )
        else:
            if breach_dir == open_ep.direction:
                open_ep.bad_measurement_ids.append(ev.measurement_id)
            else:
                reason = "RECOVERED" if breach_dir is None else "DIRECTION_CHANGED"
                finished.append(
                    _finalize_episode(
                        open_ep,
                        end_key=ev.key,
                        end_event_time=ev.t_node,
                        end_backend_at=ev.persisted_at,
                        closed_at=ev.persisted_at,
                        end_reason=reason,
                        end_measurement_id=ev.measurement_id,
                    )
                )
                open_ep = None
                if breach_dir is not None:
                    open_ep = _open_new_episode(
                        ev, direction=breach_dir, cfg=active_config, period=active_period
                    )

    if open_ep is not None:
        finished.append(_finalize_episode(open_ep))
    return finished, max_key


def _match_and_build_components(
    *,
    old_eps: list[CheckpointEpisode],
    new_eps: list[_ReplayedEpisode],
    f_old: EventKey | None,
) -> tuple[
    dict[int, int],
    dict[int, int],
    list[tuple[list[int], list[int], list[tuple[int, int]]]],
]:
    """Match ID-preserving (old, new) pairs and partition bipartite lineage components."""
    # Step 1: Greedy ID preservation requires BOTH shared bad measurement IDs AND same direction.
    candidates: list[tuple[Any, ...]] = []
    for o_idx, old_ep in enumerate(old_eps):
        old_bad = set(old_ep.bad_measurement_ids)
        for n_idx, new_ep in enumerate(new_eps):
            if old_ep.direction != new_ep.direction:
                continue
            shared_count = len(old_bad.intersection(new_ep.bad_measurement_ids))
            if shared_count <= 0:
                continue
            candidates.append(
                (
                    -shared_count,
                    abs(new_ep.start_key.event_time_ms - old_ep.start_key.event_time_ms),
                    old_ep.start_key,
                    new_ep.start_key,
                    old_ep.episode_id,
                    new_ep.semantic_key,
                    o_idx,
                    n_idx,
                )
            )
    candidates.sort(key=lambda item: item[:6])

    old_to_new: dict[int, int] = {}
    new_to_old: dict[int, int] = {}
    for item in candidates:
        o_idx = item[6]
        n_idx = item[7]
        if o_idx in old_to_new or n_idx in new_to_old:
            continue
        old_to_new[o_idx] = n_idx
        new_to_old[n_idx] = o_idx

    # Step 2: Lineage graph (shared bad IDs OR clipped interval overlap at F_old).
    edges: set[tuple[int, int]] = set(old_to_new.items())
    if f_old is not None:
        for o_idx, old_ep in enumerate(old_eps):
            old_bad = set(old_ep.bad_measurement_ids)
            for n_idx, new_ep in enumerate(new_eps):
                if (o_idx, n_idx) in edges:
                    continue
                if old_bad.intersection(new_ep.bad_measurement_ids) or _clipped_intervals_overlap(
                    old_ep, new_ep, f_old
                ):
                    edges.add((o_idx, n_idx))

    adj_old: dict[int, list[int]] = defaultdict(list)
    adj_new: dict[int, list[int]] = defaultdict(list)
    for o_idx, n_idx in sorted(edges):
        adj_old[o_idx].append(n_idx)
        adj_new[n_idx].append(o_idx)

    visited_old: set[int] = set()
    visited_new: set[int] = set()
    components: list[tuple[list[int], list[int], list[tuple[int, int]]]] = []

    for o_start in range(len(old_eps)):
        if o_start in visited_old:
            continue
        comp_olds: list[int] = []
        comp_news: list[int] = []
        stack: list[tuple[str, int]] = [("O", o_start)]
        visited_old.add(o_start)
        while stack:
            kind, idx = stack.pop()
            if kind == "O":
                comp_olds.append(idx)
                for n_idx in adj_old[idx]:
                    if n_idx not in visited_new:
                        visited_new.add(n_idx)
                        stack.append(("N", n_idx))
            else:
                comp_news.append(idx)
                for o_idx in adj_new[idx]:
                    if o_idx not in visited_old:
                        visited_old.add(o_idx)
                        stack.append(("O", o_idx))
        comp_olds.sort()
        comp_news.sort()
        comp_edges = [
            (o_idx, n_idx) for o_idx in comp_olds for n_idx in comp_news if (o_idx, n_idx) in edges
        ]
        components.append((comp_olds, comp_news, comp_edges))

    for n_start in range(len(new_eps)):
        if n_start in visited_new:
            continue
        visited_new.add(n_start)
        components.append(([], [n_start], []))

    return old_to_new, new_to_old, components


def evaluate_threshold_in_tx(
    session,
    *,
    node_id: int,
    eval_state: dict[str, Any],
    observed_now: datetime,
    evaluation_generation: int,
) -> ThresholdEvaluationResult:
    """Replay all THRESHOLD events for one Node inside an active locked TX.

    Commits updated AlertEpisode/AlertRevision/AlertReplacement projection and the
    3 EvaluationState checkpoint columns (+ clearing THRESHOLD's own error state).
    Does NOT clear full_replay_required/replay_from_t_node or advance completed_generation.
    """
    current_episodes = repo.lock_current_threshold_episodes_with_revisions(session, node_id)
    has_any_ep = bool(current_episodes) or repo.has_any_threshold_episode(session, node_id)
    ckpt_ctx = parse_and_validate_checkpoint(
        eval_state.get("threshold_checkpoint_json"),
        current_episodes=current_episodes,
        has_any_threshold_episode=has_any_ep,
    )

    configs = repo.list_threshold_configs_with_settings(session, node_id)
    periods = repo.list_threshold_periods(session, node_id)
    watermark_mid = repo.get_max_raw_measurement_id(session, node_id)
    measurements = repo.list_valid_measurements_with_values(session, node_id)

    old_cp = ckpt_ctx.checkpoint
    f_old = old_cp.frontier_key if old_cp is not None else None
    old_by_metric: dict[str, list[CheckpointEpisode]] = {m: [] for m in METRIC_ORDER}
    if old_cp is not None:
        for ep in old_cp.projection:
            old_by_metric[ep.metric_code].append(ep)

    new_by_metric: dict[str, list[_ReplayedEpisode]] = {}
    global_frontier: EventKey | None = None
    for metric_code in METRIC_ORDER:
        replayed, metric_max = _replay_metric_chronicle(
            metric_code=metric_code,
            configs=configs,
            periods=periods,
            measurements=measurements,
            f_old=f_old,
        )
        new_by_metric[metric_code] = replayed
        if metric_max is not None and (global_frontier is None or metric_max > global_frontier):
            global_frontier = metric_max

        open_count = sum(1 for ep in replayed if ep.is_open)
        if open_count > 1:
            raise ThresholdStateError(
                "THRESHOLD_STATE_INCONSISTENT",
                f"Metric {metric_code} produced {open_count} OPEN episodes",
            )

    episodes_created = 0
    episodes_updated = 0
    episodes_retired = 0
    replacements_created = 0

    retire_ops: list[dict[str, Any]] = []
    update_ops: list[dict[str, Any]] = []
    create_ops: list[dict[str, Any]] = []
    pending_replacements: list[dict[str, Any]] = []
    # Track final CheckpointEpisode per (metric_code, n_idx)
    final_checkpoint_entries: list[CheckpointEpisode] = []

    for metric_code in METRIC_ORDER:
        old_eps = old_by_metric[metric_code]
        new_eps = new_by_metric[metric_code]
        old_to_new, new_to_old, components = _match_and_build_components(
            old_eps=old_eps, new_eps=new_eps, f_old=f_old
        )

        for comp_olds, comp_news, comp_edges in components:
            if not comp_olds and len(comp_news) == 1:
                # Brand new episode with no lineage.
                n_idx = comp_news[0]
                new_ep = new_eps[n_idx]
                create_ops.append(
                    {
                        "metric_code": metric_code,
                        "n_idx": n_idx,
                        "new_ep": new_ep,
                        "transition": "PROGRESS",
                    }
                )
                continue

            if not comp_news and len(comp_olds) >= 1:
                # Completely removed (no shared bad IDs and no clipped overlap at F_old).
                for o_idx in comp_olds:
                    old_ep = old_eps[o_idx]
                    live_row = ckpt_ctx.live_by_episode_id[old_ep.episode_id]
                    retire_ops.append(
                        {
                            "old_ep": old_ep,
                            "live_row": live_row,
                            "transition": "REPLAY_REMOVED",
                        }
                    )
                continue

            if len(comp_olds) == 1 and len(comp_news) == 1:
                o_idx = comp_olds[0]
                n_idx = comp_news[0]
                old_ep = old_eps[o_idx]
                new_ep = new_eps[n_idx]
                live_row = ckpt_ctx.live_by_episode_id[old_ep.episode_id]
                live_rev_no = int(live_row["revision_no"])

                if old_to_new.get(o_idx) == n_idx:
                    # Same direction + shared bad IDs -> ID preserved.
                    # Check Step 2 FL-08 disable closure so OPEN->disable->replay never duplicates.
                    if (
                        old_ep.episode_id in ckpt_ctx.disabled_open_episode_ids
                        and _live_revision_matches_replayed(live_row, new_ep)
                    ):
                        final_checkpoint_entries.append(
                            CheckpointEpisode(
                                episode_id=old_ep.episode_id,
                                revision_no=live_rev_no,
                                acknowledgement_version=int(live_row["acknowledgement_version"]),
                                metric_code=new_ep.metric_code,
                                direction=new_ep.direction,
                                config_id=int(live_row["config_id"]),
                                period_id=new_ep.period_id,
                                start_key=new_ep.start_key,
                                end_key=new_ep.end_key,
                                end_reason=new_ep.end_reason,
                                start_measurement_id=new_ep.start_measurement_id,
                                end_measurement_id=new_ep.end_measurement_id,
                                lower_snapshot=new_ep.lower_snapshot,
                                upper_snapshot=new_ep.upper_snapshot,
                                bad_measurement_ids=new_ep.bad_measurement_ids,
                            )
                        )
                        continue

                    if (
                        old_ep.episode_id not in ckpt_ctx.disabled_open_episode_ids
                        and _prefix_matches_at_frontier(old_ep, new_ep, f_old)
                    ):
                        if _live_revision_matches_replayed(live_row, new_ep):
                            # Unchanged or bad_measurement_ids membership-only update.
                            final_checkpoint_entries.append(
                                CheckpointEpisode(
                                    episode_id=old_ep.episode_id,
                                    revision_no=live_rev_no,
                                    acknowledgement_version=int(
                                        live_row["acknowledgement_version"]
                                    ),
                                    metric_code=new_ep.metric_code,
                                    direction=new_ep.direction,
                                    config_id=new_ep.config_id,
                                    period_id=new_ep.period_id,
                                    start_key=new_ep.start_key,
                                    end_key=new_ep.end_key,
                                    end_reason=new_ep.end_reason,
                                    start_measurement_id=new_ep.start_measurement_id,
                                    end_measurement_id=new_ep.end_measurement_id,
                                    lower_snapshot=new_ep.lower_snapshot,
                                    upper_snapshot=new_ep.upper_snapshot,
                                    bad_measurement_ids=new_ep.bad_measurement_ids,
                                )
                            )
                        else:
                            # Forward progress after F_old: no ack version bump, no replacement row.
                            next_rev = live_rev_no + 1
                            ack_ver = int(live_row["acknowledgement_version"])
                            update_ops.append(
                                {
                                    "old_ep": old_ep,
                                    "live_row": live_row,
                                    "new_ep": new_ep,
                                    "new_rev_no": next_rev,
                                    "ack_ver": ack_ver,
                                    "transition": "PROGRESS",
                                }
                            )
                    else:
                        # Semantic 1:1 rewrite affecting prefix <= F_old.
                        next_rev = live_rev_no + 1
                        ack_ver = int(live_row["acknowledgement_version"]) + 1
                        update_ops.append(
                            {
                                "old_ep": old_ep,
                                "live_row": live_row,
                                "new_ep": new_ep,
                                "new_rev_no": next_rev,
                                "ack_ver": ack_ver,
                                "transition": "REPLAY_REWRITE",
                            }
                        )
                        pending_replacements.append(
                            {
                                "old_episode_id": old_ep.episode_id,
                                "old_revision_no": live_rev_no,
                                "new_ref": ("EXISTING", old_ep.episode_id, next_rev),
                                "reason": "REWRITE",
                            }
                        )
                else:
                    # 1:1 replacement across distinct episode IDs (e.g., direction flipped).
                    retire_ops.append(
                        {
                            "old_ep": old_ep,
                            "live_row": live_row,
                            "transition": "REPLAY_REPLACED",
                        }
                    )
                    create_ops.append(
                        {
                            "metric_code": metric_code,
                            "n_idx": n_idx,
                            "new_ep": new_ep,
                            "transition": "REPLAY_REPLACED",
                        }
                    )
                    pending_replacements.append(
                        {
                            "old_episode_id": old_ep.episode_id,
                            "old_revision_no": live_rev_no,
                            "new_ref": ("NEW", metric_code, n_idx),
                            "reason": "REPLACED",
                        }
                    )
                continue

            # Multi-node component (SPLIT, MERGE, or many-to-many REPLACED).
            if len(comp_olds) == 1 and len(comp_news) > 1:
                comp_reason = "SPLIT"
            elif len(comp_olds) > 1 and len(comp_news) == 1:
                comp_reason = "MERGE"
            else:
                comp_reason = "REPLACED"

            new_target_ref_by_idx: dict[int, tuple[Any, ...]] = {}
            for o_idx in comp_olds:
                old_ep = old_eps[o_idx]
                live_row = ckpt_ctx.live_by_episode_id[old_ep.episode_id]
                live_rev_no = int(live_row["revision_no"])
                matched_n_idx = old_to_new.get(o_idx)
                if matched_n_idx is not None and matched_n_idx in comp_news:
                    new_ep = new_eps[matched_n_idx]
                    next_rev = live_rev_no + 1
                    ack_ver = int(live_row["acknowledgement_version"]) + 1
                    update_ops.append(
                        {
                            "old_ep": old_ep,
                            "live_row": live_row,
                            "new_ep": new_ep,
                            "new_rev_no": next_rev,
                            "ack_ver": ack_ver,
                            "transition": "REPLAY_REPLACED",
                        }
                    )
                    new_target_ref_by_idx[matched_n_idx] = (
                        "EXISTING",
                        old_ep.episode_id,
                        next_rev,
                    )
                else:
                    retire_ops.append(
                        {
                            "old_ep": old_ep,
                            "live_row": live_row,
                            "transition": "REPLAY_REPLACED",
                        }
                    )

            for n_idx in comp_news:
                if n_idx not in new_target_ref_by_idx:
                    new_ep = new_eps[n_idx]
                    create_ops.append(
                        {
                            "metric_code": metric_code,
                            "n_idx": n_idx,
                            "new_ep": new_ep,
                            "transition": "REPLAY_REPLACED",
                        }
                    )
                    new_target_ref_by_idx[n_idx] = ("NEW", metric_code, n_idx)

            for o_idx, n_idx in comp_edges:
                old_ep = old_eps[o_idx]
                live_rev_no = int(ckpt_ctx.live_by_episode_id[old_ep.episode_id]["revision_no"])
                pending_replacements.append(
                    {
                        "old_episode_id": old_ep.episode_id,
                        "old_revision_no": live_rev_no,
                        "new_ref": new_target_ref_by_idx[n_idx],
                        "reason": comp_reason,
                    }
                )

    # Step A: Retire and update existing episodes BEFORE inserting any new OPEN episode.
    for op in retire_ops:
        old_ep = op["old_ep"]
        live_row = op["live_row"]
        transition = op["transition"]
        new_rev_no = int(live_row["revision_no"]) + 1
        was_open = live_row.get("end_reason") is None
        repo.insert_alert_revision(
            session,
            episode_id=old_ep.episode_id,
            revision_no=new_rev_no,
            config_id=int(live_row["config_id"]),
            period_id=live_row.get("period_id"),
            direction=live_row.get("direction"),
            start_event_time=live_row.get("start_event_time"),
            end_event_time=live_row.get("end_event_time"),
            start_backend_at=live_row.get("start_backend_at"),
            end_backend_at=observed_now if was_open else live_row.get("end_backend_at"),
            closed_at=observed_now if was_open else live_row.get("closed_at"),
            end_reason=transition if was_open else live_row.get("end_reason"),
            start_measurement_id=live_row.get("start_measurement_id"),
            end_measurement_id=live_row.get("end_measurement_id"),
            lower_snapshot=_to_decimal_9_2(live_row.get("lower_snapshot")),
            upper_snapshot=_to_decimal_9_2(live_row.get("upper_snapshot")),
            timeout_snapshot=None,
            evaluated_at=observed_now,
            evaluation_generation=evaluation_generation,
            acknowledgement_version=int(live_row["acknowledgement_version"]),
            threshold_metadata_json=encode_threshold_metadata(
                is_current=False, transition=transition
            ),
        )
        repo.update_alert_episode_pointer(
            session,
            episode_id=old_ep.episode_id,
            current_revision_no=new_rev_no,
            lifecycle_status="CLOSED",
            is_current=False,
        )
        episodes_retired += 1

    for op in update_ops:
        old_ep = op["old_ep"]
        new_ep = op["new_ep"]
        new_rev_no = op["new_rev_no"]
        ack_ver = op["ack_ver"]
        transition = op["transition"]
        repo.insert_alert_revision(
            session,
            episode_id=old_ep.episode_id,
            revision_no=new_rev_no,
            config_id=new_ep.config_id,
            period_id=new_ep.period_id,
            direction=new_ep.direction,
            start_event_time=new_ep.start_event_time,
            end_event_time=new_ep.end_event_time,
            start_backend_at=new_ep.start_backend_at,
            end_backend_at=new_ep.end_backend_at,
            closed_at=new_ep.closed_at,
            end_reason=new_ep.end_reason,
            start_measurement_id=new_ep.start_measurement_id,
            end_measurement_id=new_ep.end_measurement_id,
            lower_snapshot=new_ep.lower_snapshot,
            upper_snapshot=new_ep.upper_snapshot,
            timeout_snapshot=None,
            evaluated_at=observed_now,
            evaluation_generation=evaluation_generation,
            acknowledgement_version=ack_ver,
            threshold_metadata_json=encode_threshold_metadata(
                is_current=True, transition=transition
            ),
        )
        repo.update_alert_episode_pointer(
            session,
            episode_id=old_ep.episode_id,
            current_revision_no=new_rev_no,
            lifecycle_status=new_ep.lifecycle_status,
            acknowledgement_version=ack_ver,
        )
        final_checkpoint_entries.append(
            CheckpointEpisode(
                episode_id=old_ep.episode_id,
                revision_no=new_rev_no,
                acknowledgement_version=ack_ver,
                metric_code=new_ep.metric_code,
                direction=new_ep.direction,
                config_id=new_ep.config_id,
                period_id=new_ep.period_id,
                start_key=new_ep.start_key,
                end_key=new_ep.end_key,
                end_reason=new_ep.end_reason,
                start_measurement_id=new_ep.start_measurement_id,
                end_measurement_id=new_ep.end_measurement_id,
                lower_snapshot=new_ep.lower_snapshot,
                upper_snapshot=new_ep.upper_snapshot,
                bad_measurement_ids=new_ep.bad_measurement_ids,
            )
        )
        episodes_updated += 1

    # Step B: Insert newly created episodes (current_revision_no=NULL -> revision -> pointer).
    created_ids_by_key: dict[tuple[str, int], tuple[int, int]] = {}
    for op in create_ops:
        metric_code = op["metric_code"]
        n_idx = op["n_idx"]
        new_ep = op["new_ep"]
        transition = op["transition"]
        new_episode_id = repo.insert_alert_episode(
            session,
            node_id=node_id,
            alert_kind="THRESHOLD",
            metric_code=metric_code,
            lifecycle_status=new_ep.lifecycle_status,
            is_current=True,
            first_detected_at=observed_now,
            acknowledgement_version=1,
        )
        repo.insert_alert_revision(
            session,
            episode_id=new_episode_id,
            revision_no=1,
            config_id=new_ep.config_id,
            period_id=new_ep.period_id,
            direction=new_ep.direction,
            start_event_time=new_ep.start_event_time,
            end_event_time=new_ep.end_event_time,
            start_backend_at=new_ep.start_backend_at,
            end_backend_at=new_ep.end_backend_at,
            closed_at=new_ep.closed_at,
            end_reason=new_ep.end_reason,
            start_measurement_id=new_ep.start_measurement_id,
            end_measurement_id=new_ep.end_measurement_id,
            lower_snapshot=new_ep.lower_snapshot,
            upper_snapshot=new_ep.upper_snapshot,
            timeout_snapshot=None,
            evaluated_at=observed_now,
            evaluation_generation=evaluation_generation,
            acknowledgement_version=1,
            threshold_metadata_json=encode_threshold_metadata(
                is_current=True, transition=transition
            ),
        )
        repo.update_alert_episode_pointer(
            session,
            episode_id=new_episode_id,
            current_revision_no=1,
        )
        created_ids_by_key[(metric_code, n_idx)] = (new_episode_id, 1)
        final_checkpoint_entries.append(
            CheckpointEpisode(
                episode_id=new_episode_id,
                revision_no=1,
                acknowledgement_version=1,
                metric_code=new_ep.metric_code,
                direction=new_ep.direction,
                config_id=new_ep.config_id,
                period_id=new_ep.period_id,
                start_key=new_ep.start_key,
                end_key=new_ep.end_key,
                end_reason=new_ep.end_reason,
                start_measurement_id=new_ep.start_measurement_id,
                end_measurement_id=new_ep.end_measurement_id,
                lower_snapshot=new_ep.lower_snapshot,
                upper_snapshot=new_ep.upper_snapshot,
                bad_measurement_ids=new_ep.bad_measurement_ids,
            )
        )
        episodes_created += 1

    # Step C: Insert AlertReplacement lineage after all revisions and pointers are valid.
    for repl in pending_replacements:
        ref = repl["new_ref"]
        if ref[0] == "EXISTING":
            new_ep_id, new_rev_no = int(ref[1]), int(ref[2])
        else:
            new_ep_id, new_rev_no = created_ids_by_key[(ref[1], int(ref[2]))]
        repo.insert_alert_replacement(
            session,
            old_episode_id=int(repl["old_episode_id"]),
            old_revision_no=int(repl["old_revision_no"]),
            new_episode_id=new_ep_id,
            new_revision_no=new_rev_no,
            created_at=observed_now,
            evaluation_generation=evaluation_generation,
            reason=repl["reason"],
        )
        replacements_created += 1

    # Step D: Persist codec-1 checkpoint and clear THRESHOLD's own error state.
    final_checkpoint_entries.sort(key=lambda ep: ep.episode_id)
    new_checkpoint = ThresholdCheckpoint(
        processed_generation=evaluation_generation,
        watermark_measurement_id=watermark_mid,
        frontier_key=global_frontier,
        projection=tuple(final_checkpoint_entries),
    )
    last_event_at = (
        datetime_from_utc_ms(global_frontier.event_time_ms) if global_frontier is not None else None
    )
    repo.update_threshold_checkpoint(
        session,
        node_id=node_id,
        last_threshold_measurement_id=watermark_mid,
        last_threshold_event_at=last_event_at,
        threshold_checkpoint_json=new_checkpoint.to_json(),
    )

    return ThresholdEvaluationResult(
        episodes_created=episodes_created,
        episodes_updated=episodes_updated,
        episodes_retired=episodes_retired,
        replacements_created=replacements_created,
        watermark_measurement_id=watermark_mid,
        frontier_key=global_frontier,
    )


def evaluate_threshold_node(
    session,
    *,
    node_id: int,
    observed_now: datetime | None = None,
) -> ThresholdEvaluationResult:
    """Lock Farm->Zone->Gateway->Node->Runtime/State and run THRESHOLD replay for a single Node."""
    parents = devices_repo.resolve_node_parents(session, str(node_id))
    if parents is None:
        raise ThresholdStateError("THRESHOLD_STATE_INCONSISTENT", "Node does not exist")

    farm_ids = [int(parents["farm_id"])]
    gw_id = parents["gateway_id"]
    if gw_id is not None:
        gw_farm_id = devices_repo.resolve_gateway_farm_id(session, str(gw_id))
        if gw_farm_id is None:
            raise ThresholdStateError("THRESHOLD_STATE_INCONSISTENT", "Assigned Gateway missing")
        farm_ids.append(int(gw_farm_id))

    locked_farms: dict[int, dict[str, Any]] = {}
    for fid in sorted(set(farm_ids)):
        row = areas_repo.get_farm(session, str(fid), lock=True)
        if row is not None:
            locked_farms[fid] = row
    zone = areas_repo.get_zone(session, str(parents["zone_id"]), lock=True)
    gateway = devices_repo.lock_gateway(session, gw_id) if gw_id is not None else None
    node = devices_repo.lock_node(session, node_id)

    farm = locked_farms.get(int(parents["farm_id"]))
    if farm is None or zone is None or node is None:
        raise ThresholdStateError("THRESHOLD_STATE_INCONSISTENT", "Node parent hierarchy missing")
    if (
        int(node["zone_id"]) != int(parents["zone_id"])
        or node["gateway_id"] != gw_id
        or int(zone["farm_id"]) != int(parents["farm_id"])
    ):
        raise ThresholdStateError(
            "THRESHOLD_STATE_INCONSISTENT", "Node hierarchy changed concurrently"
        )
    if gw_id is not None and gateway is None:
        raise ThresholdStateError("THRESHOLD_STATE_INCONSISTENT", "Assigned Gateway missing")

    runtime = repo.lock_node_runtime(session, node_id)
    eval_state = repo.lock_evaluation_state(session, node_id)
    if runtime is None or eval_state is None:
        raise ThresholdStateError(
            "THRESHOLD_STATE_INCONSISTENT",
            "NodeRuntime or EvaluationState row is missing",
        )

    now_dt = observed_now if observed_now is not None else repo.get_db_utc_now(session)
    return evaluate_threshold_in_tx(
        session,
        node_id=node_id,
        eval_state=eval_state,
        observed_now=now_dt,
        evaluation_generation=int(eval_state["desired_generation"]),
    )
