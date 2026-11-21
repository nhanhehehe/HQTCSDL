from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from app.modules.alerts import threshold_engine
from app.modules.alerts.threshold_codec import (
    PHASE_SAMPLE,
    CheckpointEpisode,
    EventKey,
    ThresholdCheckpoint,
    ThresholdStateError,
    encode_threshold_metadata,
    parse_and_validate_checkpoint,
    parse_checkpoint_json,
)
from app.modules.alerts.threshold_engine import (
    evaluate_threshold_in_tx,
    evaluate_threshold_node,
)
from app.modules.monitoring_config.schemas import METRIC_ORDER

BASE = datetime(2026, 11, 20, 10, 0, 0)


def _dt(seconds: int) -> datetime:
    return BASE + timedelta(seconds=seconds)


def _epoch_sec(dt: datetime) -> int:
    return int((dt - datetime(1970, 1, 1)).total_seconds())


class _FakeThresholdStore:
    def __init__(self, *, node_id: int = 101):
        self.node_id = node_id
        self.now = _dt(1000)
        self.next_config_id = 1
        self.next_config_no = 1
        self.next_period_id = 1
        self.next_measurement_id = 1
        self.next_episode_id = 1
        self.farms = {1: {"farm_id": 1, "archived_at": None}}
        self.zones = {11: {"zone_id": 11, "farm_id": 1, "archived_at": None}}
        self.gateways = {201: {"gateway_id": 201, "farm_id": 1, "receiving_enabled": True}}
        self.nodes = {
            node_id: {
                "node_id": node_id,
                "device_code": "NODE-01",
                "zone_id": 11,
                "gateway_id": 201,
                "receiving_enabled": True,
            }
        }
        self.runtimes = {
            node_id: {
                "node_id": node_id,
                "current_config_id": None,
                "current_timeout_config_id": None,
                "active_period_id": None,
                "last_measurement_id": None,
                "last_new_persisted_at": None,
            }
        }
        self.eval_states = {
            node_id: {
                "node_id": node_id,
                "desired_generation": 1,
                "completed_generation": 0,
                "replay_from_t_node": 100,
                "full_replay_required": True,
                "last_success_at": None,
                "last_error_code": "PREV_ERR",
                "retry_after": _dt(500),
                "last_threshold_measurement_id": None,
                "last_threshold_event_at": None,
                "threshold_checkpoint_json": None,
                "timeout_last_error_code": "TIMEOUT_ERR",
                "timeout_retry_after": _dt(600),
            }
        }
        self.configs: list[dict] = []
        self.periods: list[dict] = []
        self.measurements: list[dict] = []
        self.episodes: dict[int, dict] = {}
        self.revisions: dict[tuple[int, int], dict] = {}
        self.replacements: list[dict] = []
        self.acknowledgements: list[dict] = []
        self.lock_log: list[str] = []

    def add_config(
        self,
        *,
        effective_from: datetime,
        temp_enabled: bool = True,
        temp_low: Decimal | None = Decimal("18.00"),
        temp_high: Decimal | None = Decimal("30.00"),
        overrides: dict[str, tuple[bool, Decimal | None, Decimal | None]] | None = None,
    ) -> int:
        cid = self.next_config_id
        self.next_config_id += 1
        cno = self.next_config_no
        self.next_config_no += 1
        settings = {
            m: {
                "config_id": cid,
                "metric_code": m,
                "enabled": False,
                "lower_value": None,
                "upper_value": None,
            }
            for m in METRIC_ORDER
        }
        settings["temperature"] = {
            "config_id": cid,
            "metric_code": "temperature",
            "enabled": temp_enabled,
            "lower_value": temp_low,
            "upper_value": temp_high,
        }
        if overrides:
            for metric_code, (en, lo, hi) in overrides.items():
                settings[metric_code] = {
                    "config_id": cid,
                    "metric_code": metric_code,
                    "enabled": en,
                    "lower_value": lo,
                    "upper_value": hi,
                }
        self.configs.append(
            {
                "config_id": cid,
                "node_id": self.node_id,
                "config_kind": "THRESHOLD",
                "config_no": cno,
                "effective_from": effective_from,
                "effective_to": None,
                "settings": settings,
            }
        )
        return cid

    def add_period(
        self,
        *,
        metric_code: str = "temperature",
        started_at: datetime,
        start_config_id: int,
        ended_at: datetime | None = None,
        end_reason: str | None = None,
    ) -> int:
        pid = self.next_period_id
        self.next_period_id += 1
        self.periods.append(
            {
                "period_id": pid,
                "node_id": self.node_id,
                "monitoring_kind": "THRESHOLD",
                "metric_code": metric_code,
                "started_at": started_at,
                "ended_at": ended_at,
                "start_config_id": start_config_id,
                "start_reason": "CONFIG_ENABLED",
                "end_reason": end_reason,
            }
        )
        return pid

    def add_measurement(
        self,
        *,
        t_node: int | None,
        persisted_at: datetime,
        temp: Decimal | None = Decimal("25.00"),
        temp_status: str = "OK",
        boot_id: int = 1,
        seq: int | None = None,
        overrides: dict[str, tuple[Decimal | None, str]] | None = None,
    ) -> int:
        mid = self.next_measurement_id
        self.next_measurement_id += 1
        values = {
            "temperature": {
                "measurement_id": mid,
                "metric_code": "temperature",
                "value": temp if temp_status == "OK" else None,
                "read_status": temp_status,
            },
            "airHumidity": {
                "measurement_id": mid,
                "metric_code": "airHumidity",
                "value": Decimal("60.00"),
                "read_status": "OK",
            },
            "pressure": {
                "measurement_id": mid,
                "metric_code": "pressure",
                "value": Decimal("1010.00"),
                "read_status": "OK",
            },
            "light": {
                "measurement_id": mid,
                "metric_code": "light",
                "value": Decimal("500.0"),
                "read_status": "OK",
            },
            "soilMoisture": {
                "measurement_id": mid,
                "metric_code": "soilMoisture",
                "value": Decimal("50.0"),
                "read_status": "OK",
            },
        }
        if overrides:
            for metric_code, (val, st) in overrides.items():
                values[metric_code] = {
                    "measurement_id": mid,
                    "metric_code": metric_code,
                    "value": val if st == "OK" else None,
                    "read_status": st,
                }
        self.measurements.append(
            {
                "measurement_id": mid,
                "node_id": self.node_id,
                "boot_id": boot_id,
                "seq": seq if seq is not None else mid,
                "t_node": t_node,
                "time_status": "VALID" if t_node is not None else "UNKNOWN",
                "persisted_at": persisted_at,
                "values": values,
            }
        )
        return mid


def _patch_store(monkeypatch, store: _FakeThresholdStore):
    monkeypatch.setattr(threshold_engine.repo, "get_db_utc_now", lambda _s: store.now)
    monkeypatch.setattr(
        threshold_engine.devices_repo,
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
        threshold_engine.devices_repo,
        "resolve_gateway_farm_id",
        lambda _s, gid: store.gateways[int(gid)]["farm_id"] if int(gid) in store.gateways else None,
    )

    def _get_farm(_s, fid, lock=False):
        if lock:
            store.lock_log.append(f"farm:{fid}")
        return deepcopy(store.farms.get(int(fid)))

    def _get_zone(_s, zid, lock=False):
        if lock:
            store.lock_log.append(f"zone:{zid}")
        return deepcopy(store.zones.get(int(zid)))

    def _lock_gw(_s, gid):
        store.lock_log.append(f"gateway:{gid}")
        return deepcopy(store.gateways.get(int(gid)))

    def _lock_node(_s, nid):
        store.lock_log.append(f"node:{nid}")
        return deepcopy(store.nodes.get(int(nid)))

    def _lock_runtime(_s, nid):
        store.lock_log.append(f"runtime:{nid}")
        return deepcopy(store.runtimes.get(int(nid)))

    def _lock_eval(_s, nid):
        store.lock_log.append(f"eval:{nid}")
        return deepcopy(store.eval_states.get(int(nid)))

    monkeypatch.setattr(threshold_engine.areas_repo, "get_farm", _get_farm)
    monkeypatch.setattr(threshold_engine.areas_repo, "get_zone", _get_zone)
    monkeypatch.setattr(threshold_engine.devices_repo, "lock_gateway", _lock_gw)
    monkeypatch.setattr(threshold_engine.devices_repo, "lock_node", _lock_node)
    monkeypatch.setattr(threshold_engine.repo, "lock_node_runtime", _lock_runtime)
    monkeypatch.setattr(threshold_engine.repo, "lock_evaluation_state", _lock_eval)

    monkeypatch.setattr(
        threshold_engine.repo,
        "list_threshold_configs_with_settings",
        lambda _s, nid: deepcopy([c for c in store.configs if c["node_id"] == nid]),
    )
    monkeypatch.setattr(
        threshold_engine.repo,
        "list_threshold_periods",
        lambda _s, nid: deepcopy([p for p in store.periods if p["node_id"] == nid]),
    )
    monkeypatch.setattr(
        threshold_engine.repo,
        "get_max_raw_measurement_id",
        lambda _s, nid: max(
            (m["measurement_id"] for m in store.measurements if m["node_id"] == nid),
            default=None,
        ),
    )
    monkeypatch.setattr(
        threshold_engine.repo,
        "list_valid_measurements_with_values",
        lambda _s, nid: deepcopy(
            [
                m
                for m in store.measurements
                if m["node_id"] == nid and m["time_status"] == "VALID" and m["t_node"] is not None
            ]
        ),
    )
    monkeypatch.setattr(
        threshold_engine.repo,
        "has_any_threshold_episode",
        lambda _s, nid: any(
            ep["node_id"] == nid and ep["alert_kind"] == "THRESHOLD"
            for ep in store.episodes.values()
        ),
    )

    def _lock_current_eps(_s, nid):
        out = []
        for eid in sorted(store.episodes):
            ep = store.episodes[eid]
            if ep["node_id"] == nid and ep["alert_kind"] == "THRESHOLD" and ep["is_current"]:
                rev_no = ep["current_revision_no"]
                rev = store.revisions.get((eid, rev_no), {}) if rev_no is not None else {}
                out.append({**deepcopy(ep), **deepcopy(rev)})
        return out

    monkeypatch.setattr(
        threshold_engine.repo, "lock_current_threshold_episodes_with_revisions", _lock_current_eps
    )

    def _insert_ep(_s, **kwargs):
        # Enforce filtered unique index UX_AlertEpisode_open_threshold in fake store.
        if kwargs["is_current"] and kwargs["lifecycle_status"] == "OPEN":
            for existing in store.episodes.values():
                if (
                    existing["node_id"] == kwargs["node_id"]
                    and existing["alert_kind"] == kwargs["alert_kind"]
                    and existing["metric_code"] == kwargs["metric_code"]
                    and existing["is_current"]
                    and existing["lifecycle_status"] == "OPEN"
                ):
                    raise AssertionError("UX_AlertEpisode_open_threshold violated")
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

    def _insert_rev(_s, **kwargs):
        key = (kwargs["episode_id"], kwargs["revision_no"])
        assert key not in store.revisions, f"Duplicate revision {key}"
        store.revisions[key] = deepcopy(kwargs)

    def _update_ptr(_s, *, episode_id, current_revision_no, **kwargs):
        ep = store.episodes[episode_id]
        assert (episode_id, current_revision_no) in store.revisions
        if kwargs.get("lifecycle_status") == "OPEN" and kwargs.get("is_current", ep["is_current"]):
            for other_id, other in store.episodes.items():
                if (
                    other_id != episode_id
                    and other["node_id"] == ep["node_id"]
                    and other["metric_code"] == ep["metric_code"]
                    and other["is_current"]
                    and other["lifecycle_status"] == "OPEN"
                ):
                    raise AssertionError("UX_AlertEpisode_open_threshold violated on update")
        ep["current_revision_no"] = current_revision_no
        for k, v in kwargs.items():
            if v is not None:
                ep[k] = v
        return 1

    def _insert_repl(_s, **kwargs):
        old_key = (kwargs["old_episode_id"], kwargs["old_revision_no"])
        new_key = (kwargs["new_episode_id"], kwargs["new_revision_no"])
        assert old_key in store.revisions and new_key in store.revisions
        assert (
            kwargs["old_episode_id"] != kwargs["new_episode_id"]
            or kwargs["old_revision_no"] < kwargs["new_revision_no"]
        )
        store.replacements.append(deepcopy(kwargs))

    def _update_ckpt(
        _s,
        *,
        node_id,
        last_threshold_measurement_id,
        last_threshold_event_at,
        threshold_checkpoint_json,
    ):
        st = store.eval_states[node_id]
        st["last_threshold_measurement_id"] = last_threshold_measurement_id
        st["last_threshold_event_at"] = last_threshold_event_at
        st["threshold_checkpoint_json"] = threshold_checkpoint_json
        st["last_error_code"] = None
        st["retry_after"] = None
        return 1

    monkeypatch.setattr(threshold_engine.repo, "insert_alert_episode", _insert_ep)
    monkeypatch.setattr(threshold_engine.repo, "insert_alert_revision", _insert_rev)
    monkeypatch.setattr(threshold_engine.repo, "update_alert_episode_pointer", _update_ptr)
    monkeypatch.setattr(threshold_engine.repo, "insert_alert_replacement", _insert_repl)
    monkeypatch.setattr(threshold_engine.repo, "update_threshold_checkpoint", _update_ckpt)


def _run_replay(store: _FakeThresholdStore, *, at_sec: int = 1000):
    store.now = _dt(at_sec)
    return evaluate_threshold_node(None, node_id=store.node_id, observed_now=store.now)


def test_codec_roundtrip_and_corrupt_missing_guards():
    cp = ThresholdCheckpoint(
        processed_generation=4,
        watermark_measurement_id=12,
        frontier_key=EventKey(1_700_000_000_000, PHASE_SAMPLE, 2, 10),
        projection=(
            CheckpointEpisode(
                episode_id=5,
                revision_no=2,
                acknowledgement_version=1,
                metric_code="temperature",
                direction="HIGH",
                config_id=3,
                period_id=7,
                start_key=EventKey(1_699_999_900_000, PHASE_SAMPLE, 2, 9),
                end_key=None,
                end_reason=None,
                start_measurement_id=11,
                end_measurement_id=None,
                lower_snapshot=Decimal("18.00"),
                upper_snapshot=Decimal("30.50"),
                bad_measurement_ids=(11, 12),
            ),
        ),
    )
    decoded = parse_checkpoint_json(cp.to_json())
    assert decoded == cp

    for bad_json in (
        "",
        "{",
        '{"codec":2,"processed_generation":0,"watermark_measurement_id":null,"frontier_key":null,"projection":[]}',
        '{"codec":1,"codec":1,"processed_generation":0,"watermark_measurement_id":null,"frontier_key":null,"projection":[]}',
        '{"codec":1,"processed_generation":true,"watermark_measurement_id":null,"frontier_key":null,"projection":[]}',
        '{"codec":1,"processed_generation":NaN,"watermark_measurement_id":null,"frontier_key":null,"projection":[]}',
    ):
        with pytest.raises(ThresholdStateError) as exc:
            parse_checkpoint_json(bad_json)
        assert exc.value.code == "THRESHOLD_CHECKPOINT_CORRUPT"

    with pytest.raises(ThresholdStateError) as missing_exc:
        parse_and_validate_checkpoint(None, current_episodes=[], has_any_threshold_episode=True)
    assert missing_exc.value.code == "THRESHOLD_CHECKPOINT_MISSING"


def test_event_key_same_ms_phases_and_numeric_boot_seq(monkeypatch):
    store = _FakeThresholdStore()
    _patch_store(monkeypatch, store)

    t0 = _dt(100)
    t0_epoch = _epoch_sec(t0)
    c1 = store.add_config(effective_from=t0, temp_low=Decimal("18.00"), temp_high=Decimal("30.00"))
    # Period 1 ends at t0; Sample at t0 must NOT see Period 1 as active.
    store.add_period(
        started_at=_dt(50), ended_at=t0, start_config_id=c1, end_reason="MONITORING_PAUSED"
    )
    # Zero-length Period 2 at t0 (started == ended == t0) must NOT activate Sample at t0.
    store.add_period(started_at=t0, ended_at=t0, start_config_id=c1, end_reason="MONITORING_PAUSED")
    store.add_measurement(
        t_node=t0_epoch, persisted_at=_dt(101), temp=Decimal("40.00"), boot_id=1, seq=1
    )

    res1 = _run_replay(store, at_sec=110)
    assert res1.episodes_created == 0
    assert store.eval_states[store.node_id]["last_threshold_measurement_id"] == 1

    # Period 3 starts at t1; Sample at t1 IS eligible.
    # Also test numeric ordering: boot=1,seq=10 arrives after boot=1,seq=2 (same t_node).
    t1 = _dt(200)
    t1_epoch = _epoch_sec(t1)
    store.add_period(started_at=t1, start_config_id=c1)
    m_seq10 = store.add_measurement(
        t_node=t1_epoch, persisted_at=_dt(202), temp=Decimal("25.00"), boot_id=1, seq=10
    )
    m_seq2 = store.add_measurement(
        t_node=t1_epoch, persisted_at=_dt(201), temp=Decimal("35.00"), boot_id=1, seq=2
    )

    res2 = _run_replay(store, at_sec=210)
    assert res2.episodes_created == 1
    ep = store.episodes[1]
    rev = store.revisions[(1, 1)]
    assert ep["lifecycle_status"] == "CLOSED"
    assert rev["start_measurement_id"] == m_seq2
    assert rev["end_measurement_id"] == m_seq10
    assert rev["end_reason"] == "RECOVERED"
    assert store.lock_log[:6] == [
        "farm:1",
        "zone:11",
        "gateway:201",
        "node:101",
        "runtime:101",
        "eval:101",
    ]
    # Threshold engine must NOT clear full_replay_required or bump completed_generation.
    assert store.eval_states[store.node_id]["full_replay_required"] is True
    assert store.eval_states[store.node_id]["completed_generation"] == 0
    assert store.eval_states[store.node_id]["last_error_code"] is None
    assert store.eval_states[store.node_id]["timeout_last_error_code"] == "TIMEOUT_ERR"


def test_config_changed_keep_close_and_never_open_from_old_sample(monkeypatch):
    store = _FakeThresholdStore()
    _patch_store(monkeypatch, store)

    c1 = store.add_config(
        effective_from=_dt(10), temp_low=Decimal("20.00"), temp_high=Decimal("30.00")
    )
    store.add_period(started_at=_dt(10), start_config_id=c1)
    # Sample at t=20 breaches HIGH (35 > 30).
    store.add_measurement(t_node=_epoch_sec(_dt(20)), persisted_at=_dt(21), temp=Decimal("35.00"))
    _run_replay(store, at_sec=25)
    assert store.episodes[1]["lifecycle_status"] == "OPEN"

    # Config at t=30 widens upper to 33.00 -> 35 > 33 (same direction HIGH) -> keep OPEN.
    store.add_config(effective_from=_dt(30), temp_low=Decimal("20.00"), temp_high=Decimal("33.00"))
    res_keep = _run_replay(store, at_sec=35)
    assert res_keep.episodes_updated == 1
    assert res_keep.replacements_created == 0
    assert store.episodes[1]["lifecycle_status"] == "OPEN"
    assert store.episodes[1]["acknowledgement_version"] == 1
    assert store.revisions[(1, 2)]["upper_snapshot"] == Decimal("33.00")

    # Config at t=40 raises bounds to [36.00, 40.00] -> 35 flips from HIGH to LOW!
    # Must close with CONFIG_CHANGED and MUST NOT open a new LOW episode from old sample (t=20).
    store.add_config(effective_from=_dt(40), temp_low=Decimal("36.00"), temp_high=Decimal("40.00"))
    res_flip = _run_replay(store, at_sec=45)
    assert res_flip.episodes_created == 0
    assert res_flip.episodes_updated == 1
    assert store.episodes[1]["lifecycle_status"] == "CLOSED"
    assert store.revisions[(1, 3)]["end_reason"] == "CONFIG_CHANGED"
    assert len(store.episodes) == 1

    # Config at t=50 narrows to [36.00, 38.00] when no episode is OPEN;
    # old sample 35 < 36 must NOT open.
    store.add_config(effective_from=_dt(50), temp_low=Decimal("36.00"), temp_high=Decimal("38.00"))
    res_no_open = _run_replay(store, at_sec=55)
    assert res_no_open.episodes_created == 0
    assert len(store.episodes) == 1

    # Next eligible sample at t=60 (35 < 36) opens the new LOW episode.
    m_next = store.add_measurement(
        t_node=_epoch_sec(_dt(60)), persisted_at=_dt(61), temp=Decimal("35.00")
    )
    res_next = _run_replay(store, at_sec=65)
    assert res_next.episodes_created == 1
    assert store.episodes[2]["lifecycle_status"] == "OPEN"
    assert store.revisions[(2, 1)]["direction"] == "LOW"
    assert store.revisions[(2, 1)]["start_measurement_id"] == m_next


def test_open_disable_threshold_then_replay_does_not_duplicate_revision(monkeypatch):
    store = _FakeThresholdStore()
    _patch_store(monkeypatch, store)

    c1 = store.add_config(
        effective_from=_dt(10), temp_low=Decimal("18.00"), temp_high=Decimal("30.00")
    )
    pid = store.add_period(started_at=_dt(10), start_config_id=c1)
    m1 = store.add_measurement(
        t_node=_epoch_sec(_dt(20)), persisted_at=_dt(21), temp=Decimal("35.00")
    )
    _run_replay(store, at_sec=25)
    assert store.episodes[1]["current_revision_no"] == 1
    assert store.episodes[1]["lifecycle_status"] == "OPEN"

    # Simulate Step 2 FL-08 disable threshold at t=50.
    disable_dt = _dt(50)
    c2 = store.add_config(
        effective_from=disable_dt,
        temp_enabled=False,
        temp_low=Decimal("18.00"),
        temp_high=Decimal("30.00"),
    )
    store.periods[0]["ended_at"] = disable_dt
    store.periods[0]["end_reason"] = "THRESHOLD_DISABLED"
    rev1 = store.revisions[(1, 1)]
    store.revisions[(1, 2)] = {
        "episode_id": 1,
        "revision_no": 2,
        "config_id": c2,
        "period_id": pid,
        "direction": rev1["direction"],
        "start_event_time": rev1["start_event_time"],
        "end_event_time": None,
        "start_backend_at": rev1["start_backend_at"],
        "end_backend_at": disable_dt,
        "closed_at": disable_dt,
        "end_reason": "THRESHOLD_DISABLED",
        "start_measurement_id": m1,
        "end_measurement_id": None,
        "lower_snapshot": Decimal("18.00"),
        "upper_snapshot": Decimal("30.00"),
        "timeout_snapshot": None,
        "evaluated_at": disable_dt,
        "evaluation_generation": 2,
        "acknowledgement_version": 1,
        "threshold_metadata_json": encode_threshold_metadata(
            is_current=True, transition="PROGRESS"
        ),
    }
    store.episodes[1]["current_revision_no"] = 2
    store.episodes[1]["lifecycle_status"] = "CLOSED"
    store.eval_states[store.node_id]["desired_generation"] = 2

    res = _run_replay(store, at_sec=55)
    assert res.episodes_created == 0
    assert res.episodes_updated == 0
    assert res.episodes_retired == 0
    assert res.replacements_created == 0
    assert store.episodes[1]["current_revision_no"] == 2
    assert (1, 3) not in store.revisions
    cp = parse_checkpoint_json(store.eval_states[store.node_id]["threshold_checkpoint_json"])
    assert cp.projection[0].revision_no == 2
    assert cp.projection[0].end_reason == "THRESHOLD_DISABLED"


def test_membership_progress_no_revision_and_late_rewrite_bumps_ack_once(monkeypatch):
    store = _FakeThresholdStore()
    _patch_store(monkeypatch, store)

    c1 = store.add_config(
        effective_from=_dt(10), temp_low=Decimal("18.00"), temp_high=Decimal("30.00")
    )
    store.add_period(started_at=_dt(10), start_config_id=c1)
    m1 = store.add_measurement(
        t_node=_epoch_sec(_dt(30)), persisted_at=_dt(31), temp=Decimal("35.00")
    )
    _run_replay(store, at_sec=35)
    assert store.episodes[1]["current_revision_no"] == 1

    # Add another bad sample + an UNKNOWN measurement; OPEN episode stays OPEN -> NO new revision!
    m2 = store.add_measurement(
        t_node=_epoch_sec(_dt(40)), persisted_at=_dt(41), temp=Decimal("36.00")
    )
    m_unk = store.add_measurement(t_node=None, persisted_at=_dt(42), temp=Decimal("99.00"))
    res_mem = _run_replay(store, at_sec=45)
    assert res_mem.episodes_updated == 0
    assert res_mem.episodes_created == 0
    assert store.episodes[1]["current_revision_no"] == 1
    assert res_mem.watermark_measurement_id == m_unk
    cp = parse_checkpoint_json(store.eval_states[store.node_id]["threshold_checkpoint_json"])
    assert cp.projection[0].bad_measurement_ids == (m1, m2)

    # Late sample at t=20 shifts start_key earlier than F_old -> REPLAY_REWRITE + ack_ver bump once.
    m_late = store.add_measurement(
        t_node=_epoch_sec(_dt(20)), persisted_at=_dt(50), temp=Decimal("34.00")
    )
    res_rewrite = _run_replay(store, at_sec=55)
    assert res_rewrite.episodes_updated == 1
    assert res_rewrite.replacements_created == 1
    ep = store.episodes[1]
    assert ep["current_revision_no"] == 2
    assert ep["acknowledgement_version"] == 2
    assert ep["first_detected_at"] == _dt(35)  # preserved
    assert store.revisions[(1, 2)]["start_measurement_id"] == m_late
    assert store.replacements[0]["reason"] == "REWRITE"
    assert store.replacements[0]["old_episode_id"] == 1
    assert store.replacements[0]["new_episode_id"] == 1


def test_split_merge_and_direction_flip_lineage(monkeypatch):
    store = _FakeThresholdStore()
    _patch_store(monkeypatch, store)

    c1 = store.add_config(
        effective_from=_dt(10), temp_low=Decimal("18.00"), temp_high=Decimal("30.00")
    )
    store.add_period(started_at=_dt(10), start_config_id=c1)
    m1 = store.add_measurement(
        t_node=_epoch_sec(_dt(20)), persisted_at=_dt(21), temp=Decimal("35.00")
    )
    m2 = store.add_measurement(
        t_node=_epoch_sec(_dt(40)), persisted_at=_dt(41), temp=Decimal("36.00")
    )
    _run_replay(store, at_sec=45)
    assert len(store.episodes) == 1

    # Late normal sample at t=30 splits Episode 1 into [t=20..30) and [t=40..OPEN].
    store.add_measurement(t_node=_epoch_sec(_dt(30)), persisted_at=_dt(50), temp=Decimal("25.00"))
    res_split = _run_replay(store, at_sec=55)
    assert res_split.episodes_updated == 1
    assert res_split.episodes_created == 1
    assert res_split.replacements_created == 2
    assert {r["reason"] for r in store.replacements} == {"SPLIT"}
    assert store.episodes[1]["acknowledgement_version"] == 2
    assert store.episodes[2]["first_detected_at"] == _dt(55)
    assert {
        store.revisions[(1, 2)]["start_measurement_id"],
        store.revisions[(2, 1)]["start_measurement_id"],
    } == {m1, m2}

    # Use a fresh node for REPLAY_REMOVED (no overlap, no shared bad IDs).
    store2 = _FakeThresholdStore(node_id=102)
    _patch_store(monkeypatch, store2)
    hc1 = store2.add_config(
        effective_from=_dt(10),
        temp_enabled=False,
        temp_low=None,
        temp_high=None,
        overrides={"airHumidity": (True, Decimal("40.00"), Decimal("80.00"))},
    )
    store2.add_period(metric_code="airHumidity", started_at=_dt(10), start_config_id=hc1)
    # Episode A: [20..30)
    store2.add_measurement(
        t_node=_epoch_sec(_dt(20)),
        persisted_at=_dt(21),
        temp_status="READ_ERROR",
        overrides={"airHumidity": (Decimal("90.00"), "OK")},
    )
    store2.add_measurement(
        t_node=_epoch_sec(_dt(30)),
        persisted_at=_dt(31),
        temp_status="READ_ERROR",
        overrides={"airHumidity": (Decimal("60.00"), "OK")},
    )
    # Episode B: [40..OPEN]
    store2.add_measurement(
        t_node=_epoch_sec(_dt(40)),
        persisted_at=_dt(41),
        temp_status="READ_ERROR",
        overrides={"airHumidity": (Decimal("91.00"), "OK")},
    )
    _run_replay(store2, at_sec=45)
    assert len(store2.episodes) == 2

    # Late config at t=15 raising upper bound to 95.00:
    # both Episode A and B vanish -> REPLAY_REMOVED (no overlap with any new episode).
    store2.add_config(
        effective_from=_dt(15),
        temp_enabled=False,
        temp_low=None,
        temp_high=None,
        overrides={"airHumidity": (True, Decimal("40.00"), Decimal("95.00"))},
    )
    res_rem = _run_replay(store2, at_sec=50)
    assert res_rem.episodes_retired == 2
    assert res_rem.replacements_created == 0
    assert store2.episodes[1]["is_current"] is False
    assert store2.episodes[2]["is_current"] is False
    assert "REPLAY_REMOVED" in store2.revisions[(1, 2)]["threshold_metadata_json"]


def test_direction_flip_and_clipped_overlap_lineage_without_shared_ids(monkeypatch):
    store = _FakeThresholdStore()
    _patch_store(monkeypatch, store)

    c1 = store.add_config(
        effective_from=_dt(10), temp_low=Decimal("20.00"), temp_high=Decimal("30.00")
    )
    store.add_period(started_at=_dt(10), start_config_id=c1)
    # Sample at t=30 (15.0 < 20.0 -> LOW)
    store.add_measurement(t_node=_epoch_sec(_dt(30)), persisted_at=_dt(31), temp=Decimal("15.00"))
    _run_replay(store, at_sec=35)
    assert (
        store.episodes[1]["direction"]
        if "direction" in store.episodes[1]
        else store.revisions[(1, 1)]["direction"] == "LOW"
    )

    # Late config at t=25 changes bounds to [5.00, 10.00]:
    # sample at t=30 (15.0 > 10.0) flips LOW -> HIGH.
    # Same bad_measurement_id=1, DIFFERENT direction -> must retire 1 and replace with 2.
    store.add_config(effective_from=_dt(25), temp_low=Decimal("5.00"), temp_high=Decimal("10.00"))
    res = _run_replay(store, at_sec=40)
    assert res.episodes_retired == 1
    assert res.episodes_created == 1
    assert res.replacements_created == 1
    assert store.episodes[1]["is_current"] is False
    assert "REPLAY_REPLACED" in store.revisions[(1, 2)]["threshold_metadata_json"]
    assert store.episodes[2]["is_current"] is True
    assert store.revisions[(2, 1)]["direction"] == "HIGH"
    assert store.replacements[0]["reason"] == "REPLACED"

    # Now test clipped interval overlap WITHOUT shared bad IDs:
    store2 = _FakeThresholdStore(node_id=103)
    _patch_store(monkeypatch, store2)
    c_init = store2.add_config(
        effective_from=_dt(10), temp_low=Decimal("20.00"), temp_high=Decimal("30.00")
    )
    store2.add_period(started_at=_dt(10), start_config_id=c_init)
    # Old episode: [15, 25) LOW (bad_ids={1}); t=25 has 20.00 (normal in both configs).
    store2.add_measurement(t_node=_epoch_sec(_dt(15)), persisted_at=_dt(16), temp=Decimal("15.00"))
    store2.add_measurement(t_node=_epoch_sec(_dt(25)), persisted_at=_dt(26), temp=Decimal("20.00"))
    _run_replay(store2, at_sec=28)

    # Late config at t=12 sets [10.00, 20.00], plus late samples at t=18 (35 HIGH) & t=22 (20 ok):
    # old LOW [15, 25) disappears and new HIGH [18, 22) overlaps [15, 25) at F_old=25
    # with ZERO shared bad IDs -> REPLAY_REPLACED + AlertReplacement(reason="REPLACED").
    store2.add_config(effective_from=_dt(12), temp_low=Decimal("10.00"), temp_high=Decimal("20.00"))
    store2.add_measurement(t_node=_epoch_sec(_dt(18)), persisted_at=_dt(29), temp=Decimal("35.00"))
    store2.add_measurement(t_node=_epoch_sec(_dt(22)), persisted_at=_dt(30), temp=Decimal("20.00"))
    res_ov = _run_replay(store2, at_sec=32)
    assert res_ov.episodes_retired == 1
    assert res_ov.episodes_created == 1
    assert res_ov.replacements_created == 1
    assert "REPLAY_REPLACED" in store2.revisions[(1, 2)]["threshold_metadata_json"]
    assert store2.replacements[0]["reason"] == "REPLACED"


def test_merge_preserves_primary_id_and_retires_secondary(monkeypatch):
    store = _FakeThresholdStore()
    _patch_store(monkeypatch, store)

    c1 = store.add_config(
        effective_from=_dt(10), temp_low=Decimal("18.00"), temp_high=Decimal("30.00")
    )
    store.add_period(started_at=_dt(10), start_config_id=c1)
    # Ep 1: [20..30)
    m1 = store.add_measurement(
        t_node=_epoch_sec(_dt(20)), persisted_at=_dt(21), temp=Decimal("35.00")
    )
    m2 = store.add_measurement(
        t_node=_epoch_sec(_dt(30)), persisted_at=_dt(31), temp=Decimal("25.00")
    )
    # Ep 2: [40..OPEN]
    m3 = store.add_measurement(
        t_node=_epoch_sec(_dt(40)), persisted_at=_dt(41), temp=Decimal("36.00")
    )
    _run_replay(store, at_sec=45)
    assert len(store.episodes) == 2

    # Late config at t=25 sets upper=22 -> sample m2 (25 > 22) becomes HIGH,
    # bridging Ep 1 and Ep 2 into one!
    store.add_config(effective_from=_dt(25), temp_low=Decimal("18.00"), temp_high=Decimal("22.00"))
    res_merge = _run_replay(store, at_sec=50)
    assert res_merge.episodes_updated == 1
    assert res_merge.episodes_retired == 1
    assert res_merge.replacements_created == 2
    assert {r["reason"] for r in store.replacements} == {"MERGE"}
    # Ep 1 (earlier start_key) kept ID, bumped ack_ver 1 -> 2; Ep 2 retired.
    assert store.episodes[1]["is_current"] is True
    assert store.episodes[1]["acknowledgement_version"] == 2
    assert store.episodes[2]["is_current"] is False
    cp = parse_checkpoint_json(store.eval_states[store.node_id]["threshold_checkpoint_json"])
    assert cp.projection[0].bad_measurement_ids == (m1, m2, m3)


def test_pause_keeps_open_episode_and_reopen_recovers(monkeypatch):
    store = _FakeThresholdStore()
    _patch_store(monkeypatch, store)

    c1 = store.add_config(
        effective_from=_dt(10), temp_low=Decimal("18.00"), temp_high=Decimal("30.00")
    )
    store.add_period(
        started_at=_dt(10), ended_at=_dt(30), start_config_id=c1, end_reason="MONITORING_PAUSED"
    )
    store.add_measurement(t_node=_epoch_sec(_dt(20)), persisted_at=_dt(21), temp=Decimal("35.00"))
    # Normal sample while paused at t=35 must be ignored.
    store.add_measurement(t_node=_epoch_sec(_dt(35)), persisted_at=_dt(36), temp=Decimal("25.00"))
    _run_replay(store, at_sec=40)
    assert store.episodes[1]["lifecycle_status"] == "OPEN"

    # Resume period at t=45 and send normal sample at t=50 -> closes PROGRESS without ack bump.
    store.add_period(started_at=_dt(45), start_config_id=c1)
    m_rec = store.add_measurement(
        t_node=_epoch_sec(_dt(50)), persisted_at=_dt(51), temp=Decimal("25.00")
    )
    res = _run_replay(store, at_sec=55)
    assert res.episodes_updated == 1
    assert store.episodes[1]["lifecycle_status"] == "CLOSED"
    assert store.episodes[1]["acknowledgement_version"] == 1
    assert store.revisions[(1, 2)]["end_reason"] == "RECOVERED"
    assert store.revisions[(1, 2)]["end_measurement_id"] == m_rec


def test_corrupt_checkpoint_never_reconstructs_and_in_tx_raises(monkeypatch):
    store = _FakeThresholdStore()
    _patch_store(monkeypatch, store)
    store.eval_states[store.node_id]["threshold_checkpoint_json"] = "{corrupt"

    with pytest.raises(ThresholdStateError) as exc:
        _run_replay(store, at_sec=100)
    assert exc.value.code == "THRESHOLD_CHECKPOINT_CORRUPT"
    assert len(store.episodes) == 0

    store.eval_states[store.node_id]["threshold_checkpoint_json"] = None
    store.episodes[99] = {
        "episode_id": 99,
        "node_id": store.node_id,
        "alert_kind": "THRESHOLD",
        "metric_code": "temperature",
        "lifecycle_status": "OPEN",
        "is_current": True,
        "current_revision_no": 1,
        "first_detected_at": _dt(50),
        "acknowledgement_version": 1,
    }
    store.revisions[(99, 1)] = {
        "episode_id": 99,
        "revision_no": 1,
        "config_id": 1,
        "period_id": 1,
        "direction": "HIGH",
        "start_event_time": _epoch_sec(_dt(50)),
        "end_event_time": None,
        "start_backend_at": _dt(50),
        "end_backend_at": None,
        "closed_at": None,
        "end_reason": None,
        "start_measurement_id": 1,
        "end_measurement_id": None,
        "lower_snapshot": Decimal("18.00"),
        "upper_snapshot": Decimal("30.00"),
        "evaluated_at": _dt(50),
        "evaluation_generation": 1,
        "acknowledgement_version": 1,
        "threshold_metadata_json": encode_threshold_metadata(
            is_current=True, transition="PROGRESS"
        ),
    }
    with pytest.raises(ThresholdStateError) as exc2:
        evaluate_threshold_in_tx(
            None,
            node_id=store.node_id,
            eval_state=store.eval_states[store.node_id],
            observed_now=_dt(100),
            evaluation_generation=1,
        )
    assert exc2.value.code == "THRESHOLD_CHECKPOINT_MISSING"
