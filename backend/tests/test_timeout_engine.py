from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.core.config import NodeTimeProfile
from app.modules.alerts import timeout_engine
from app.modules.alerts.timeout_engine import (
    TimeoutStateError,
    build_evaluation_view,
    evaluate_timeout_in_tx,
    evaluate_timeout_node,
    reconcile_open_timeout,
)


def _dt(hour: int, minute: int, second: int = 0, ms: int = 0) -> datetime:
    return datetime(2026, 11, 20, hour, minute, second, ms * 1000)


class _FakeAlertStore:
    def __init__(self):
        self.now = _dt(10, 0, 0)
        self.next_episode_id = 1
        self.next_period_id = 1
        self.farms: dict[int, dict] = {}
        self.zones: dict[int, dict] = {}
        self.gateways: dict[int, dict] = {}
        self.nodes: dict[int, dict] = {}
        self.runtimes: dict[int, dict] = {}
        self.eval_states: dict[int, dict] = {}
        self.configs: dict[int, dict] = {}
        self.periods: dict[int, dict] = {}
        self.measurements: list[dict] = []
        self.episodes: dict[int, dict] = {}
        self.revisions: dict[tuple[int, int], dict] = {}

    def snapshot(self):
        return deepcopy(self.__dict__)

    def restore(self, snap):
        self.__dict__.update(snap)


def _patch_fake_repos(monkeypatch, store: _FakeAlertStore):
    monkeypatch.setattr(timeout_engine.repo, "get_db_utc_now", lambda _s: store.now)
    monkeypatch.setattr(
        timeout_engine.repo,
        "lock_node_runtime",
        lambda _s, nid: deepcopy(store.runtimes.get(nid)),
    )
    monkeypatch.setattr(
        timeout_engine.repo,
        "lock_evaluation_state",
        lambda _s, nid: deepcopy(store.eval_states.get(nid)),
    )

    def _lock_open_timeout_episode(_s, nid):
        for ep in store.episodes.values():
            if (
                ep["node_id"] == nid
                and ep["alert_kind"] == "TIMEOUT"
                and ep["is_current"]
                and ep["lifecycle_status"] == "OPEN"
            ):
                return deepcopy(ep)
        return None

    monkeypatch.setattr(
        timeout_engine.repo, "lock_open_timeout_episode", _lock_open_timeout_episode
    )
    monkeypatch.setattr(
        timeout_engine.repo,
        "get_alert_revision",
        lambda _s, eid, rev_no: deepcopy(store.revisions.get((eid, rev_no))),
    )

    def _find_first_measurement_after(_s, nid, baseline):
        floor = 0 if baseline is None else int(baseline)
        candidates = [
            m for m in store.measurements if m["node_id"] == nid and m["measurement_id"] > floor
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda m: m["measurement_id"])
        return deepcopy(candidates[0])

    monkeypatch.setattr(
        timeout_engine.repo, "find_first_measurement_after", _find_first_measurement_after
    )

    def _insert_alert_episode(_s, **kwargs):
        eid = store.next_episode_id
        store.next_episode_id += 1
        store.episodes[eid] = {
            "episode_id": eid,
            "node_id": kwargs["node_id"],
            "alert_kind": kwargs["alert_kind"],
            "metric_code": kwargs["metric_code"],
            "lifecycle_status": kwargs["lifecycle_status"],
            "is_current": kwargs["is_current"],
            "current_revision_no": None,
            "first_detected_at": kwargs["first_detected_at"],
            "acknowledgement_version": kwargs["acknowledgement_version"],
        }
        return eid

    monkeypatch.setattr(timeout_engine.repo, "insert_alert_episode", _insert_alert_episode)

    def _insert_alert_revision(_s, **kwargs):
        key = (kwargs["episode_id"], kwargs["revision_no"])
        assert key not in store.revisions
        store.revisions[key] = deepcopy(kwargs)

    monkeypatch.setattr(timeout_engine.repo, "insert_alert_revision", _insert_alert_revision)

    def _update_episode_ptr(
        _s,
        *,
        episode_id,
        current_revision_no,
        lifecycle_status=None,
        is_current=None,
        acknowledgement_version=None,
    ):
        ep = store.episodes[episode_id]
        ep["current_revision_no"] = current_revision_no
        if lifecycle_status is not None:
            ep["lifecycle_status"] = lifecycle_status
        if is_current is not None:
            ep["is_current"] = is_current
        if acknowledgement_version is not None:
            ep["acknowledgement_version"] = acknowledgement_version
        return 1

    monkeypatch.setattr(timeout_engine.repo, "update_alert_episode_pointer", _update_episode_ptr)

    def _get_open_timeout_period(_s, nid, *, lock=False):
        for p in store.periods.values():
            if p["node_id"] == nid and p["monitoring_kind"] == "TIMEOUT" and p["ended_at"] is None:
                return deepcopy(p)
        return None

    monkeypatch.setattr(timeout_engine.repo, "get_open_timeout_period", _get_open_timeout_period)
    monkeypatch.setattr(
        timeout_engine.repo,
        "get_timeout_config_by_id",
        lambda _s, nid, cid: (
            deepcopy(store.configs[cid])
            if cid in store.configs
            and store.configs[cid]["node_id"] == nid
            and store.configs[cid]["config_kind"] == "TIMEOUT"
            else None
        ),
    )

    def _insert_period(_s, **kwargs):
        pid = store.next_period_id
        store.next_period_id += 1
        store.periods[pid] = {
            "period_id": pid,
            "node_id": kwargs["node_id"],
            "monitoring_kind": kwargs["monitoring_kind"],
            "metric_code": kwargs["metric_code"],
            "started_at": kwargs["started_at"],
            "ended_at": None,
            "start_config_id": kwargs["start_config_id"],
            "start_reason": kwargs["start_reason"],
            "end_reason": None,
        }
        return pid

    monkeypatch.setattr(timeout_engine.repo, "insert_monitoring_period", _insert_period)

    def _close_period(_s, *, period_id, node_id, ended_at, end_reason):
        p = store.periods[period_id]
        assert p["node_id"] == node_id and p["ended_at"] is None
        p["ended_at"] = ended_at
        p["end_reason"] = end_reason
        return 1

    monkeypatch.setattr(timeout_engine.repo, "close_monitoring_period", _close_period)

    def _update_active_period(_s, *, node_id, active_period_id):
        store.runtimes[node_id]["active_period_id"] = active_period_id
        return 1

    monkeypatch.setattr(
        timeout_engine.repo, "update_node_runtime_active_period", _update_active_period
    )

    monkeypatch.setattr(
        timeout_engine.devices_repo,
        "resolve_node_parents",
        lambda _s, nid: (
            {
                "node_id": int(nid),
                "zone_id": store.nodes[int(nid)]["zone_id"],
                "farm_id": store.zones[store.nodes[int(nid)]["zone_id"]]["farm_id"],
                "gateway_id": store.nodes[int(nid)]["gateway_id"],
            }
            if int(nid) in store.nodes
            else None
        ),
    )
    monkeypatch.setattr(
        timeout_engine.areas_repo,
        "get_farm",
        lambda _s, fid, lock=False: deepcopy(store.farms.get(int(fid))),
    )
    monkeypatch.setattr(
        timeout_engine.areas_repo,
        "get_zone",
        lambda _s, zid, lock=False: deepcopy(store.zones.get(int(zid))),
    )
    monkeypatch.setattr(
        timeout_engine.devices_repo,
        "lock_gateway",
        lambda _s, gid: deepcopy(store.gateways.get(int(gid))),
    )
    monkeypatch.setattr(
        timeout_engine.devices_repo,
        "lock_node",
        lambda _s, nid: deepcopy(store.nodes.get(int(nid))),
    )


def _seed_basic_node(
    store: _FakeAlertStore,
    *,
    node_id: int = 101,
    device_code: str = "NODE-01",
    timeout_minutes: int = 5,
    started_at: datetime | None = None,
    last_persisted_at: datetime | None = None,
    last_measurement_id: int | None = None,
    with_open_period: bool = True,
):
    store.farms[1] = {"farm_id": 1, "archived_at": None}
    store.zones[11] = {"zone_id": 11, "farm_id": 1, "archived_at": None}
    store.gateways[201] = {"gateway_id": 201, "farm_id": 1, "receiving_enabled": True}
    store.nodes[node_id] = {
        "node_id": node_id,
        "device_code": device_code,
        "zone_id": 11,
        "gateway_id": 201,
        "receiving_enabled": True,
    }
    store.configs[50] = {
        "config_id": 50,
        "node_id": node_id,
        "config_kind": "TIMEOUT",
        "config_no": 1,
        "timeout_minutes": timeout_minutes,
        "effective_from": _dt(9, 0),
        "effective_to": None,
    }
    period_id = None
    if with_open_period:
        period_id = store.next_period_id
        store.next_period_id += 1
        store.periods[period_id] = {
            "period_id": period_id,
            "node_id": node_id,
            "monitoring_kind": "TIMEOUT",
            "metric_code": None,
            "started_at": started_at or _dt(10, 0),
            "ended_at": None,
            "start_config_id": 50,
            "start_reason": "CONFIG_ENABLED",
            "end_reason": None,
        }
    store.runtimes[node_id] = {
        "node_id": node_id,
        "current_config_id": None,
        "current_timeout_config_id": 50,
        "active_period_id": period_id,
        "last_measurement_id": last_measurement_id,
        "last_new_persisted_at": last_persisted_at,
    }
    store.eval_states[node_id] = {
        "node_id": node_id,
        "desired_generation": 1,
        "completed_generation": 1,
        "last_success_at": None,
        "last_error_code": None,
        "timeout_last_error_code": None,
    }


def _settings(profiles: dict[str, NodeTimeProfile] | None = None):
    return SimpleNamespace(
        node_time_profiles=profiles
        if profiles is not None
        else {
            "NODE-01": NodeTimeProfile(
                sampling_interval_seconds=60,
                normal_delivery_budget_seconds=60,
            )
        }
    )


def test_exact_ms_boundary_and_null_baseline_without_l(monkeypatch):
    store = _FakeAlertStore()
    _patch_fake_repos(monkeypatch, store)
    _seed_basic_node(store, started_at=_dt(10, 0), last_persisted_at=None, last_measurement_id=None)

    # E = 4m59.999s = 299999 ms < 300000 ms -> no episode
    res_before = evaluate_timeout_in_tx(
        None,
        node_id=101,
        device_code="NODE-01",
        structurally_eligible=True,
        runtime=deepcopy(store.runtimes[101]),
        observed_now=_dt(10, 4, 59, 999),
        settings=_settings(),
        evaluation_generation=1,
    )
    assert not res_before.episode_opened
    assert store.episodes == {}

    # E = 5m00.000s = 300000 ms -> opens episode with NULL opening baseline
    res_at = evaluate_timeout_in_tx(
        None,
        node_id=101,
        device_code="NODE-01",
        structurally_eligible=True,
        runtime=deepcopy(store.runtimes[101]),
        observed_now=_dt(10, 5, 0, 0),
        settings=_settings(),
        evaluation_generation=1,
    )
    assert res_at.episode_opened
    ep = store.episodes[1]
    rev = store.revisions[(1, 1)]
    assert ep["alert_kind"] == "TIMEOUT"
    assert ep["metric_code"] is None
    assert ep["lifecycle_status"] == "OPEN"
    assert ep["current_revision_no"] == 1
    assert ep["first_detected_at"] == _dt(10, 5, 0, 0)
    assert rev["start_backend_at"] == _dt(10, 5, 0, 0)
    assert rev["start_measurement_id"] is None
    assert rev["timeout_snapshot"] == 5


def test_anchor_uses_max_s_and_l_across_resume(monkeypatch):
    store = _FakeAlertStore()
    _patch_fake_repos(monkeypatch, store)
    # Resumed at S=10:20 while historical L=10:00 -> anchor=10:20, due=10:25
    _seed_basic_node(
        store,
        started_at=_dt(10, 20),
        last_persisted_at=_dt(10, 0),
        last_measurement_id=10,
    )

    res_early = evaluate_timeout_in_tx(
        None,
        node_id=101,
        device_code="NODE-01",
        structurally_eligible=True,
        runtime=deepcopy(store.runtimes[101]),
        observed_now=_dt(10, 24, 59, 999),
        settings=_settings(),
        evaluation_generation=1,
    )
    assert not res_early.episode_opened

    res_due = evaluate_timeout_in_tx(
        None,
        node_id=101,
        device_code="NODE-01",
        structurally_eligible=True,
        runtime=deepcopy(store.runtimes[101]),
        observed_now=_dt(10, 25, 0, 0),
        settings=_settings(),
        evaluation_generation=1,
    )
    assert res_due.episode_opened
    rev = store.revisions[(1, 1)]
    assert rev["start_backend_at"] == _dt(10, 25)
    assert rev["start_measurement_id"] == 10


def test_delayed_tick_closes_a_by_witness_and_opens_b_at_actual_now(monkeypatch):
    store = _FakeAlertStore()
    _patch_fake_repos(monkeypatch, store)
    _seed_basic_node(
        store,
        started_at=_dt(10, 0),
        last_persisted_at=_dt(10, 0),
        last_measurement_id=10,
    )

    # Open episode A at 10:05
    evaluate_timeout_in_tx(
        None,
        node_id=101,
        device_code="NODE-01",
        structurally_eligible=True,
        runtime=deepcopy(store.runtimes[101]),
        observed_now=_dt(10, 5),
        settings=_settings(),
        evaluation_generation=1,
    )
    assert store.episodes[1]["lifecycle_status"] == "OPEN"

    # Measurement M=11 arrives at 10:06, and M=12 arrives at 10:07 while worker was paused
    store.measurements.append({"measurement_id": 11, "node_id": 101, "persisted_at": _dt(10, 6)})
    store.measurements.append({"measurement_id": 12, "node_id": 101, "persisted_at": _dt(10, 7)})
    # Other node's earlier measurement must be ignored
    store.measurements.append({"measurement_id": 9, "node_id": 999, "persisted_at": _dt(10, 5, 30)})
    store.runtimes[101]["last_measurement_id"] = 12
    store.runtimes[101]["last_new_persisted_at"] = _dt(10, 7)

    # Worker runs at 10:20 (E = 10:20 - 10:07 = 13m >= 5m)
    res = evaluate_timeout_in_tx(
        None,
        node_id=101,
        device_code="NODE-01",
        structurally_eligible=True,
        runtime=deepcopy(store.runtimes[101]),
        observed_now=_dt(10, 20),
        settings=_settings(),
        evaluation_generation=3,
    )
    assert res.reconciled_witness
    assert res.episode_opened
    ep_a = store.episodes[1]
    rev_a2 = store.revisions[(1, 2)]
    assert ep_a["lifecycle_status"] == "CLOSED"
    assert ep_a["current_revision_no"] == 2
    assert ep_a["first_detected_at"] == _dt(10, 5)
    assert rev_a2["end_reason"] == "NEW_DATA_STORED"
    assert rev_a2["end_measurement_id"] == 11
    assert rev_a2["end_backend_at"] == _dt(10, 6)
    assert rev_a2["closed_at"] == _dt(10, 6)
    assert rev_a2["evaluated_at"] == _dt(10, 20)
    assert rev_a2["start_measurement_id"] == 10

    ep_b = store.episodes[2]
    rev_b1 = store.revisions[(2, 1)]
    assert ep_b["lifecycle_status"] == "OPEN"
    assert ep_b["first_detected_at"] == _dt(10, 20)
    assert rev_b1["start_backend_at"] == _dt(10, 12)
    assert rev_b1["start_measurement_id"] == 12


def test_multiple_measurements_same_millisecond_select_smallest_id(monkeypatch):
    store = _FakeAlertStore()
    _patch_fake_repos(monkeypatch, store)
    _seed_basic_node(
        store,
        started_at=_dt(10, 0),
        last_persisted_at=_dt(10, 0),
        last_measurement_id=100,
    )
    evaluate_timeout_in_tx(
        None,
        node_id=101,
        device_code="NODE-01",
        structurally_eligible=True,
        runtime=deepcopy(store.runtimes[101]),
        observed_now=_dt(10, 5),
        settings=_settings(),
        evaluation_generation=1,
    )

    same_ms = _dt(10, 6, 0, 123)
    store.measurements.extend(
        [
            {"measurement_id": 102, "node_id": 101, "persisted_at": same_ms},
            {"measurement_id": 101, "node_id": 101, "persisted_at": same_ms},
            {"measurement_id": 103, "node_id": 101, "persisted_at": _dt(10, 7)},
        ]
    )

    rec = reconcile_open_timeout(
        None, node_id=101, observed_now=_dt(10, 8), evaluation_generation=2
    )
    assert rec.closed
    assert store.revisions[(1, 2)]["end_measurement_id"] == 101
    assert store.revisions[(1, 2)]["end_backend_at"] == same_ms


def test_witness_closes_episode_before_pause_applied(monkeypatch):
    store = _FakeAlertStore()
    _patch_fake_repos(monkeypatch, store)
    _seed_basic_node(
        store,
        started_at=_dt(10, 0),
        last_persisted_at=_dt(10, 0),
        last_measurement_id=1,
    )
    evaluate_timeout_in_tx(
        None,
        node_id=101,
        device_code="NODE-01",
        structurally_eligible=True,
        runtime=deepcopy(store.runtimes[101]),
        observed_now=_dt(10, 5),
        settings=_settings(),
        evaluation_generation=1,
    )
    store.measurements.append({"measurement_id": 2, "node_id": 101, "persisted_at": _dt(10, 6)})
    store.runtimes[101]["last_measurement_id"] = 2
    store.runtimes[101]["last_new_persisted_at"] = _dt(10, 6)

    res = evaluate_timeout_in_tx(
        None,
        node_id=101,
        device_code="NODE-01",
        structurally_eligible=False,
        runtime=deepcopy(store.runtimes[101]),
        observed_now=_dt(10, 8),
        settings=_settings(),
        evaluation_generation=2,
    )
    assert res.reconciled_witness
    assert res.period_changed
    assert not res.episode_closed
    assert store.revisions[(1, 2)]["end_reason"] == "NEW_DATA_STORED"
    assert store.revisions[(1, 2)]["end_measurement_id"] == 2
    assert store.periods[1]["end_reason"] == "MONITORING_PAUSED"


def test_config_t_increase_decrease_and_keep_overdue_id(monkeypatch):
    store = _FakeAlertStore()
    _patch_fake_repos(monkeypatch, store)
    _seed_basic_node(
        store,
        timeout_minutes=5,
        started_at=_dt(10, 0),
        last_persisted_at=_dt(10, 0),
        last_measurement_id=10,
    )
    evaluate_timeout_in_tx(
        None,
        node_id=101,
        device_code="NODE-01",
        structurally_eligible=True,
        runtime=deepcopy(store.runtimes[101]),
        observed_now=_dt(10, 5),
        settings=_settings(),
        evaluation_generation=1,
    )

    # T 5 -> 8 at 10:10 (still overdue: 10 >= 8) -> keep ID=1, append revision 2
    store.configs[51] = {
        "config_id": 51,
        "node_id": 101,
        "config_kind": "TIMEOUT",
        "config_no": 2,
        "timeout_minutes": 8,
        "effective_from": _dt(10, 10),
        "effective_to": None,
    }
    store.configs[50]["effective_to"] = _dt(10, 10)
    store.runtimes[101]["current_timeout_config_id"] = 51

    res_keep = evaluate_timeout_in_tx(
        None,
        node_id=101,
        device_code="NODE-01",
        structurally_eligible=True,
        runtime=deepcopy(store.runtimes[101]),
        observed_now=_dt(10, 10),
        settings=_settings(),
        evaluation_generation=2,
        is_config_change=True,
    )
    assert res_keep.episode_updated
    assert store.episodes[1]["lifecycle_status"] == "OPEN"
    assert store.episodes[1]["current_revision_no"] == 2
    assert store.episodes[1]["first_detected_at"] == _dt(10, 5)
    assert store.revisions[(1, 2)]["start_backend_at"] == _dt(10, 5)
    assert store.revisions[(1, 2)]["start_measurement_id"] == 10
    assert store.revisions[(1, 2)]["timeout_snapshot"] == 8

    # T 8 -> 15 at 10:12 (no longer overdue: 12 < 15) -> close CONFIG_CHANGED
    store.configs[52] = {
        "config_id": 52,
        "node_id": 101,
        "config_kind": "TIMEOUT",
        "config_no": 3,
        "timeout_minutes": 15,
        "effective_from": _dt(10, 12),
        "effective_to": None,
    }
    store.configs[51]["effective_to"] = _dt(10, 12)
    store.runtimes[101]["current_timeout_config_id"] = 52

    res_close = evaluate_timeout_in_tx(
        None,
        node_id=101,
        device_code="NODE-01",
        structurally_eligible=True,
        runtime=deepcopy(store.runtimes[101]),
        observed_now=_dt(10, 12),
        settings=_settings(),
        evaluation_generation=3,
        is_config_change=True,
    )
    assert res_close.episode_closed
    assert store.episodes[1]["lifecycle_status"] == "CLOSED"
    assert store.revisions[(1, 3)]["end_reason"] == "CONFIG_CHANGED"
    assert store.revisions[(1, 3)]["end_measurement_id"] is None

    # T 15 -> 8 at 10:13 -> overdue again (13 >= 8) -> opens new ID=2 with first_detected_at=10:13
    store.configs[53] = {
        "config_id": 53,
        "node_id": 101,
        "config_kind": "TIMEOUT",
        "config_no": 4,
        "timeout_minutes": 8,
        "effective_from": _dt(10, 13),
        "effective_to": None,
    }
    store.configs[52]["effective_to"] = _dt(10, 13)
    store.runtimes[101]["current_timeout_config_id"] = 53

    res_open = evaluate_timeout_in_tx(
        None,
        node_id=101,
        device_code="NODE-01",
        structurally_eligible=True,
        runtime=deepcopy(store.runtimes[101]),
        observed_now=_dt(10, 13),
        settings=_settings(),
        evaluation_generation=4,
        is_config_change=True,
    )
    assert res_open.episode_opened
    assert store.episodes[2]["first_detected_at"] == _dt(10, 13)
    assert store.revisions[(2, 1)]["start_backend_at"] == _dt(10, 8)


def test_profile_missing_and_recovery_g4_d04_and_d08(monkeypatch):
    store = _FakeAlertStore()
    _patch_fake_repos(monkeypatch, store)
    _seed_basic_node(
        store,
        timeout_minutes=5,
        last_persisted_at=_dt(9, 50),
        last_measurement_id=7,
        with_open_period=False,
    )

    # Missing profile: repeated ticks do not open period and keep L untouched
    for minute in (0, 5, 10):
        store.now = _dt(10, minute)
        res = evaluate_timeout_node(None, node_id=101, settings=_settings({}))
        assert not res.period_changed
        assert res.active_period_id is None
        assert store.runtimes[101]["last_new_persisted_at"] == _dt(9, 50)

    # Profile returns but incompatible (T=5 -> 300s <= 300s) -> stays paused
    store.now = _dt(10, 12)
    res_incompat = evaluate_timeout_node(
        None,
        node_id=101,
        settings=_settings(
            {
                "NODE-01": NodeTimeProfile(
                    sampling_interval_seconds=240, normal_delivery_budget_seconds=60
                )
            }
        ),
    )
    assert not res_incompat.period_changed
    assert res_incompat.active_period_id is None

    # Profile returns compatible (T=5 -> 300s > 120s) -> opens ONE period with S=10:15
    store.now = _dt(10, 15)
    compatible = _settings()
    res_rec1 = evaluate_timeout_node(None, node_id=101, settings=compatible)
    assert res_rec1.period_changed
    assert res_rec1.active_period_id == 1
    assert store.periods[1]["start_reason"] == "PROFILE_RECOVERED"
    assert store.periods[1]["started_at"] == _dt(10, 15)
    assert store.runtimes[101]["last_new_persisted_at"] == _dt(9, 50)

    # Subsequent tick does not reopen/reset S; open period keeps ticking if profile drops
    store.now = _dt(10, 20)
    res_rec2 = evaluate_timeout_node(None, node_id=101, settings=_settings({}))
    assert not res_rec2.period_changed
    assert res_rec2.episode_opened
    assert store.periods[1]["started_at"] == _dt(10, 15)
    assert store.episodes[1]["first_detected_at"] == _dt(10, 20)


def test_clock_and_state_invariants_raise_timeout_state_error(monkeypatch):
    store = _FakeAlertStore()
    _patch_fake_repos(monkeypatch, store)
    _seed_basic_node(store, started_at=_dt(10, 0), last_persisted_at=_dt(10, 2))

    # Now < anchor -> TIMEOUT_CLOCK_INVALID
    with pytest.raises(TimeoutStateError) as exc1:
        evaluate_timeout_in_tx(
            None,
            node_id=101,
            device_code="NODE-01",
            structurally_eligible=True,
            runtime=deepcopy(store.runtimes[101]),
            observed_now=_dt(10, 1),
            settings=_settings(),
            evaluation_generation=1,
        )
    assert exc1.value.code == "TIMEOUT_CLOCK_INVALID"

    # Mismatched NodeRuntime.active_period_id vs open period -> TIMEOUT_STATE_INCONSISTENT
    bad_runtime = deepcopy(store.runtimes[101])
    bad_runtime["active_period_id"] = 999
    with pytest.raises(TimeoutStateError) as exc2:
        evaluate_timeout_in_tx(
            None,
            node_id=101,
            device_code="NODE-01",
            structurally_eligible=True,
            runtime=bad_runtime,
            observed_now=_dt(10, 7),
            settings=_settings(),
            evaluation_generation=1,
        )
    assert exc2.value.code == "TIMEOUT_STATE_INCONSISTENT"


def test_witness_clock_bounds_and_inconsistent_open_episode(monkeypatch):
    store = _FakeAlertStore()
    _patch_fake_repos(monkeypatch, store)
    _seed_basic_node(
        store, started_at=_dt(10, 0), last_persisted_at=_dt(10, 0), last_measurement_id=1
    )
    evaluate_timeout_in_tx(
        None,
        node_id=101,
        device_code="NODE-01",
        structurally_eligible=True,
        runtime=deepcopy(store.runtimes[101]),
        observed_now=_dt(10, 5),
        settings=_settings(),
        evaluation_generation=1,
    )

    # Witness earlier than first_detected_at -> TIMEOUT_CLOCK_INVALID
    store.measurements.append({"measurement_id": 2, "node_id": 101, "persisted_at": _dt(10, 4)})
    with pytest.raises(TimeoutStateError) as exc_early:
        reconcile_open_timeout(None, node_id=101, observed_now=_dt(10, 6), evaluation_generation=1)
    assert exc_early.value.code == "TIMEOUT_CLOCK_INVALID"

    # Witness later than observed_now -> TIMEOUT_CLOCK_INVALID
    store.measurements[0]["persisted_at"] = _dt(10, 7)
    with pytest.raises(TimeoutStateError) as exc_future:
        reconcile_open_timeout(None, node_id=101, observed_now=_dt(10, 6), evaluation_generation=1)
    assert exc_future.value.code == "TIMEOUT_CLOCK_INVALID"

    # Open episode while E < T and no witness/config change -> TIMEOUT_STATE_INCONSISTENT
    store.measurements.clear()
    store.runtimes[101]["last_new_persisted_at"] = _dt(10, 4)
    with pytest.raises(TimeoutStateError) as exc_inconsistent:
        evaluate_timeout_in_tx(
            None,
            node_id=101,
            device_code="NODE-01",
            structurally_eligible=True,
            runtime=deepcopy(store.runtimes[101]),
            observed_now=_dt(10, 6),
            settings=_settings(),
            evaluation_generation=1,
        )
    assert exc_inconsistent.value.code == "TIMEOUT_STATE_INCONSISTENT"


def test_build_evaluation_view_combines_engine_errors_with_stable_precedence():
    now_dt = datetime(2026, 11, 20, 10, 0, 0, tzinfo=UTC).replace(tzinfo=None)
    assert build_evaluation_view(
        {
            "desired_generation": 4,
            "completed_generation": 4,
            "last_success_at": now_dt,
            "last_error_code": None,
            "timeout_last_error_code": None,
        }
    ) == {
        "desired_generation": "4",
        "completed_generation": "4",
        "status": "UP_TO_DATE",
        "last_success_at": "2026-11-20T10:00:00.000Z",
        "last_error_code": None,
    }
    assert (
        build_evaluation_view(
            {
                "desired_generation": 5,
                "completed_generation": 4,
                "last_success_at": now_dt,
                "last_error_code": None,
                "timeout_last_error_code": None,
            }
        )["status"]
        == "PENDING"
    )

    # TIMEOUT error surfaces even when desired == completed (G4-D11)
    timeout_err = build_evaluation_view(
        {
            "desired_generation": 4,
            "completed_generation": 4,
            "last_success_at": now_dt,
            "last_error_code": None,
            "timeout_last_error_code": "TIMEOUT_CLOCK_INVALID",
        }
    )
    assert timeout_err["status"] == "ERROR"
    assert timeout_err["last_error_code"] == "TIMEOUT_CLOCK_INVALID"

    # THRESHOLD error takes deterministic precedence when both fail (G4-D05)
    both_err = build_evaluation_view(
        {
            "desired_generation": 5,
            "completed_generation": 4,
            "last_success_at": now_dt,
            "last_error_code": "THRESHOLD_CHECKPOINT_CORRUPT",
            "timeout_last_error_code": "TIMEOUT_CLOCK_INVALID",
        }
    )
    assert both_err["status"] == "ERROR"
    assert both_err["last_error_code"] == "THRESHOLD_CHECKPOINT_CORRUPT"
