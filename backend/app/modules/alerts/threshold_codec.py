import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from app.core.web import json_loads_no_duplicates, utc_ms_from_datetime
from app.modules.monitoring_config.schemas import METRIC_ORDER

PHASE_PERIOD_END = 0
PHASE_CONFIG = 1
PHASE_PERIOD_START = 2
PHASE_SAMPLE = 3

VALID_DIRECTIONS = frozenset({"LOW", "HIGH"})
VALID_TRANSITIONS = frozenset({"PROGRESS", "REPLAY_REWRITE", "REPLAY_REMOVED", "REPLAY_REPLACED"})
_EPOCH_UTC = datetime(1970, 1, 1, tzinfo=UTC)


class ThresholdStateError(Exception):
    """Raised when threshold checkpoint, config, or alert projection violates invariants."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, order=True)
class EventKey:
    """Total ordering key across period, config, and sample events."""

    event_time_ms: int
    phase: int
    tie_a: int
    tie_b: int

    def to_list(self) -> list[int]:
        return [self.event_time_ms, self.phase, self.tie_a, self.tie_b]


def datetime_from_utc_ms(ms: int) -> datetime:
    """Convert UTC epoch milliseconds to naive UTC datetime with millisecond precision."""
    dt = _EPOCH_UTC + timedelta(milliseconds=int(ms))
    return dt.replace(tzinfo=None)


def format_snapshot_decimal(value: Decimal | str) -> str:
    d = value if isinstance(value, Decimal) else Decimal(value)
    return f"{d:.2f}"


def _require_strict_int(value: Any, *, field: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ThresholdStateError(
            "THRESHOLD_CHECKPOINT_CORRUPT",
            f"Checkpoint field {field} must be an integer >= {minimum}",
        )
    return value


def _require_strict_str(value: Any, *, field: str) -> str:
    if type(value) is not str or not value:
        raise ThresholdStateError(
            "THRESHOLD_CHECKPOINT_CORRUPT",
            f"Checkpoint field {field} must be a non-empty string",
        )
    return value


def _parse_event_key(raw: Any, *, field: str) -> EventKey:
    if not isinstance(raw, list) or len(raw) != 4:
        raise ThresholdStateError(
            "THRESHOLD_CHECKPOINT_CORRUPT",
            f"Checkpoint {field} must be a 4-element integer array",
        )
    ms = _require_strict_int(raw[0], field=f"{field}[0]", minimum=0)
    phase = _require_strict_int(raw[1], field=f"{field}[1]", minimum=0)
    if phase not in {PHASE_PERIOD_END, PHASE_CONFIG, PHASE_PERIOD_START, PHASE_SAMPLE}:
        raise ThresholdStateError(
            "THRESHOLD_CHECKPOINT_CORRUPT",
            f"Checkpoint {field} has unsupported EventKey phase",
        )
    tie_a = _require_strict_int(raw[2], field=f"{field}[2]", minimum=0)
    tie_b = _require_strict_int(raw[3], field=f"{field}[3]", minimum=0)
    return EventKey(event_time_ms=ms, phase=phase, tie_a=tie_a, tie_b=tie_b)


def _parse_bound_str(raw: Any, *, field: str) -> Decimal:
    text_val = _require_strict_str(raw, field=field)
    try:
        d = Decimal(text_val)
    except InvalidOperation as exc:
        raise ThresholdStateError(
            "THRESHOLD_CHECKPOINT_CORRUPT",
            f"Checkpoint {field} is not a valid decimal string",
        ) from exc
    if not d.is_finite():
        raise ThresholdStateError(
            "THRESHOLD_CHECKPOINT_CORRUPT",
            f"Checkpoint {field} must be finite",
        )
    exp = d.as_tuple().exponent
    if not isinstance(exp, int) or exp < -2:
        raise ThresholdStateError(
            "THRESHOLD_CHECKPOINT_CORRUPT",
            f"Checkpoint {field} exceeds DECIMAL(9,2) scale",
        )
    if abs(d) > Decimal("9999999.99"):
        raise ThresholdStateError(
            "THRESHOLD_CHECKPOINT_CORRUPT",
            f"Checkpoint {field} exceeds DECIMAL(9,2) range",
        )
    return Decimal(f"{d:.2f}")


@dataclass(frozen=True)
class CheckpointEpisode:
    episode_id: int
    revision_no: int
    acknowledgement_version: int
    metric_code: str
    direction: str
    config_id: int
    period_id: int
    start_key: EventKey
    end_key: EventKey | None
    end_reason: str | None
    start_measurement_id: int
    end_measurement_id: int | None
    lower_snapshot: Decimal
    upper_snapshot: Decimal
    bad_measurement_ids: tuple[int, ...]

    @property
    def is_open(self) -> bool:
        return self.end_key is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "revision_no": self.revision_no,
            "acknowledgement_version": self.acknowledgement_version,
            "metric_code": self.metric_code,
            "direction": self.direction,
            "config_id": self.config_id,
            "period_id": self.period_id,
            "start_key": self.start_key.to_list(),
            "end_key": self.end_key.to_list() if self.end_key is not None else None,
            "end_reason": self.end_reason,
            "start_measurement_id": self.start_measurement_id,
            "end_measurement_id": self.end_measurement_id,
            "lower_snapshot": format_snapshot_decimal(self.lower_snapshot),
            "upper_snapshot": format_snapshot_decimal(self.upper_snapshot),
            "bad_measurement_ids": list(self.bad_measurement_ids),
        }


@dataclass(frozen=True)
class ThresholdCheckpoint:
    processed_generation: int
    watermark_measurement_id: int | None
    frontier_key: EventKey | None
    projection: tuple[CheckpointEpisode, ...]
    codec: int = 1

    def to_json(self) -> str:
        payload = {
            "codec": self.codec,
            "processed_generation": self.processed_generation,
            "watermark_measurement_id": self.watermark_measurement_id,
            "frontier_key": (
                self.frontier_key.to_list() if self.frontier_key is not None else None
            ),
            "projection": [ep.to_dict() for ep in self.projection],
        }
        return json.dumps(payload, separators=(",", ":"))


@dataclass(frozen=True)
class ValidatedCheckpointContext:
    checkpoint: ThresholdCheckpoint | None
    live_by_episode_id: dict[int, dict[str, Any]]
    disabled_open_episode_ids: frozenset[int]


def _parse_checkpoint_episode(raw: Any, *, idx: int) -> CheckpointEpisode:
    if not isinstance(raw, dict):
        raise ThresholdStateError(
            "THRESHOLD_CHECKPOINT_CORRUPT",
            f"Checkpoint projection[{idx}] must be an object",
        )
    expected_keys = {
        "episode_id",
        "revision_no",
        "acknowledgement_version",
        "metric_code",
        "direction",
        "config_id",
        "period_id",
        "start_key",
        "end_key",
        "end_reason",
        "start_measurement_id",
        "end_measurement_id",
        "lower_snapshot",
        "upper_snapshot",
        "bad_measurement_ids",
    }
    if set(raw.keys()) != expected_keys:
        raise ThresholdStateError(
            "THRESHOLD_CHECKPOINT_CORRUPT",
            f"Checkpoint projection[{idx}] has unexpected or missing keys",
        )
    episode_id = _require_strict_int(
        raw["episode_id"], field=f"projection[{idx}].episode_id", minimum=1
    )
    revision_no = _require_strict_int(
        raw["revision_no"], field=f"projection[{idx}].revision_no", minimum=1
    )
    ack_ver = _require_strict_int(
        raw["acknowledgement_version"],
        field=f"projection[{idx}].acknowledgement_version",
        minimum=1,
    )
    metric_code = _require_strict_str(raw["metric_code"], field=f"projection[{idx}].metric_code")
    if metric_code not in METRIC_ORDER:
        raise ThresholdStateError(
            "THRESHOLD_CHECKPOINT_CORRUPT",
            f"Checkpoint projection[{idx}].metric_code is invalid",
        )
    direction = _require_strict_str(raw["direction"], field=f"projection[{idx}].direction")
    if direction not in VALID_DIRECTIONS:
        raise ThresholdStateError(
            "THRESHOLD_CHECKPOINT_CORRUPT",
            f"Checkpoint projection[{idx}].direction is invalid",
        )
    config_id = _require_strict_int(
        raw["config_id"], field=f"projection[{idx}].config_id", minimum=1
    )
    period_id = _require_strict_int(
        raw["period_id"], field=f"projection[{idx}].period_id", minimum=1
    )
    start_key = _parse_event_key(raw["start_key"], field=f"projection[{idx}].start_key")

    raw_end_key = raw["end_key"]
    raw_end_reason = raw["end_reason"]
    if raw_end_key is None:
        if raw_end_reason is not None:
            raise ThresholdStateError(
                "THRESHOLD_CHECKPOINT_CORRUPT",
                f"Checkpoint projection[{idx}] has end_reason without end_key",
            )
        end_key = None
        end_reason = None
    else:
        end_key = _parse_event_key(raw_end_key, field=f"projection[{idx}].end_key")
        end_reason = _require_strict_str(raw_end_reason, field=f"projection[{idx}].end_reason")
        if end_key <= start_key:
            raise ThresholdStateError(
                "THRESHOLD_CHECKPOINT_CORRUPT",
                f"Checkpoint projection[{idx}] end_key must be greater than start_key",
            )

    start_mid = _require_strict_int(
        raw["start_measurement_id"],
        field=f"projection[{idx}].start_measurement_id",
        minimum=1,
    )
    raw_end_mid = raw["end_measurement_id"]
    if raw_end_mid is None:
        end_mid = None
    else:
        if end_key is None:
            raise ThresholdStateError(
                "THRESHOLD_CHECKPOINT_CORRUPT",
                f"Checkpoint projection[{idx}] has end_measurement_id while OPEN",
            )
        end_mid = _require_strict_int(
            raw_end_mid, field=f"projection[{idx}].end_measurement_id", minimum=1
        )

    lower_snap = _parse_bound_str(raw["lower_snapshot"], field=f"projection[{idx}].lower_snapshot")
    upper_snap = _parse_bound_str(raw["upper_snapshot"], field=f"projection[{idx}].upper_snapshot")
    if lower_snap >= upper_snap:
        raise ThresholdStateError(
            "THRESHOLD_CHECKPOINT_CORRUPT",
            f"Checkpoint projection[{idx}] lower_snapshot must be < upper_snapshot",
        )

    raw_bad_ids = raw["bad_measurement_ids"]
    if not isinstance(raw_bad_ids, list) or not raw_bad_ids:
        raise ThresholdStateError(
            "THRESHOLD_CHECKPOINT_CORRUPT",
            f"Checkpoint projection[{idx}].bad_measurement_ids must be a non-empty list",
        )
    bad_ids_list = [
        _require_strict_int(
            item,
            field=f"projection[{idx}].bad_measurement_ids[{b_idx}]",
            minimum=1,
        )
        for b_idx, item in enumerate(raw_bad_ids)
    ]
    if len(set(bad_ids_list)) != len(bad_ids_list) or bad_ids_list != sorted(bad_ids_list):
        raise ThresholdStateError(
            "THRESHOLD_CHECKPOINT_CORRUPT",
            f"Checkpoint projection[{idx}].bad_measurement_ids must be strictly sorted and unique",
        )
    if start_mid not in bad_ids_list:
        raise ThresholdStateError(
            "THRESHOLD_CHECKPOINT_CORRUPT",
            f"Checkpoint projection[{idx}] start_measurement_id must be in bad_measurement_ids",
        )

    return CheckpointEpisode(
        episode_id=episode_id,
        revision_no=revision_no,
        acknowledgement_version=ack_ver,
        metric_code=metric_code,
        direction=direction,
        config_id=config_id,
        period_id=period_id,
        start_key=start_key,
        end_key=end_key,
        end_reason=end_reason,
        start_measurement_id=start_mid,
        end_measurement_id=end_mid,
        lower_snapshot=lower_snap,
        upper_snapshot=upper_snap,
        bad_measurement_ids=tuple(bad_ids_list),
    )


def parse_checkpoint_json(raw_json: str) -> ThresholdCheckpoint:
    if type(raw_json) is not str or not raw_json:
        raise ThresholdStateError(
            "THRESHOLD_CHECKPOINT_CORRUPT",
            "Threshold checkpoint JSON must be a non-empty string",
        )
    try:
        parsed = json_loads_no_duplicates(raw_json)
    except (ValueError, TypeError) as exc:
        raise ThresholdStateError(
            "THRESHOLD_CHECKPOINT_CORRUPT",
            "Threshold checkpoint JSON is malformed",
        ) from exc

    if not isinstance(parsed, dict):
        raise ThresholdStateError(
            "THRESHOLD_CHECKPOINT_CORRUPT",
            "Threshold checkpoint JSON must be an object",
        )
    expected_keys = {
        "codec",
        "processed_generation",
        "watermark_measurement_id",
        "frontier_key",
        "projection",
    }
    if set(parsed.keys()) != expected_keys:
        raise ThresholdStateError(
            "THRESHOLD_CHECKPOINT_CORRUPT",
            "Threshold checkpoint JSON has unexpected or missing keys",
        )
    if _require_strict_int(parsed["codec"], field="codec", minimum=1) != 1:
        raise ThresholdStateError(
            "THRESHOLD_CHECKPOINT_CORRUPT",
            "Unsupported threshold checkpoint codec version",
        )
    processed_generation = _require_strict_int(
        parsed["processed_generation"], field="processed_generation", minimum=0
    )
    raw_wm = parsed["watermark_measurement_id"]
    watermark_id = (
        None
        if raw_wm is None
        else _require_strict_int(raw_wm, field="watermark_measurement_id", minimum=1)
    )
    raw_fk = parsed["frontier_key"]
    frontier_key = None if raw_fk is None else _parse_event_key(raw_fk, field="frontier_key")
    raw_proj = parsed["projection"]
    if not isinstance(raw_proj, list):
        raise ThresholdStateError(
            "THRESHOLD_CHECKPOINT_CORRUPT",
            "Checkpoint projection must be a list",
        )
    episodes = tuple(_parse_checkpoint_episode(item, idx=idx) for idx, item in enumerate(raw_proj))
    if episodes and frontier_key is None:
        raise ThresholdStateError(
            "THRESHOLD_CHECKPOINT_CORRUPT",
            "Checkpoint projection is non-empty while frontier_key is null",
        )
    seen_ids: set[int] = set()
    open_metrics: set[str] = set()
    for ep in episodes:
        if ep.episode_id in seen_ids:
            raise ThresholdStateError(
                "THRESHOLD_CHECKPOINT_CORRUPT",
                "Duplicate episode_id in checkpoint projection",
            )
        seen_ids.add(ep.episode_id)
        if ep.is_open:
            if ep.metric_code in open_metrics:
                raise ThresholdStateError(
                    "THRESHOLD_CHECKPOINT_CORRUPT",
                    "Multiple OPEN episodes for same metric in checkpoint projection",
                )
            open_metrics.add(ep.metric_code)
    return ThresholdCheckpoint(
        processed_generation=processed_generation,
        watermark_measurement_id=watermark_id,
        frontier_key=frontier_key,
        projection=episodes,
    )


def _is_valid_step2_threshold_disable_closure(
    cp_ep: CheckpointEpisode, live_row: dict[str, Any]
) -> bool:
    if cp_ep.end_key is not None:
        return False
    if int(live_row["revision_no"]) != cp_ep.revision_no + 1:
        return False
    if (
        live_row["lifecycle_status"] != "CLOSED"
        or live_row.get("end_reason") != "THRESHOLD_DISABLED"
    ):
        return False
    if live_row.get("end_event_time") is not None or live_row.get("end_measurement_id") is not None:
        return False
    if live_row.get("end_backend_at") is None or live_row.get("closed_at") is None:
        return False
    if int(live_row["acknowledgement_version"]) != cp_ep.acknowledgement_version:
        return False
    if live_row["metric_code"] != cp_ep.metric_code or live_row.get("direction") != cp_ep.direction:
        return False
    if (
        live_row.get("period_id") != cp_ep.period_id
        or live_row.get("start_measurement_id") != cp_ep.start_measurement_id
        or live_row.get("start_event_time") != (cp_ep.start_key.event_time_ms // 1000)
    ):
        return False
    raw_meta = live_row.get("threshold_metadata_json")
    if type(raw_meta) is not str:
        return False
    try:
        meta = json_loads_no_duplicates(raw_meta)
    except (ValueError, TypeError):
        return False
    return (
        isinstance(meta, dict)
        and meta.get("codec") == 1
        and meta.get("is_current") is True
        and meta.get("transition") == "PROGRESS"
    )


def parse_and_validate_checkpoint(
    raw_json: str | None,
    *,
    current_episodes: list[dict[str, Any]],
    has_any_threshold_episode: bool,
) -> ValidatedCheckpointContext:
    """Parse codec-1 checkpoint and cross-check against live current THRESHOLD episodes."""
    live_by_id: dict[int, dict[str, Any]] = {}
    for row in current_episodes:
        eid = int(row["episode_id"])
        rev_no = row.get("current_revision_no")
        if (
            rev_no is None
            or row.get("revision_no") is None
            or int(rev_no) != int(row["revision_no"])
        ):
            raise ThresholdStateError(
                "THRESHOLD_STATE_INCONSISTENT",
                f"Current THRESHOLD episode {eid} is missing its current AlertRevision",
            )
        if row["lifecycle_status"] == "OPEN" and row.get("end_reason") is not None:
            raise ThresholdStateError(
                "THRESHOLD_STATE_INCONSISTENT",
                f"OPEN THRESHOLD episode {eid} points to a closed AlertRevision",
            )
        if row["lifecycle_status"] == "CLOSED" and row.get("end_reason") is None:
            raise ThresholdStateError(
                "THRESHOLD_STATE_INCONSISTENT",
                f"CLOSED THRESHOLD episode {eid} points to an open AlertRevision",
            )
        live_by_id[eid] = row

    if raw_json is None:
        if has_any_threshold_episode or live_by_id:
            raise ThresholdStateError(
                "THRESHOLD_CHECKPOINT_MISSING",
                "Threshold checkpoint is missing while AlertEpisode history already exists",
            )
        return ValidatedCheckpointContext(
            checkpoint=None,
            live_by_episode_id={},
            disabled_open_episode_ids=frozenset(),
        )

    checkpoint = parse_checkpoint_json(raw_json)
    cp_by_id = {ep.episode_id: ep for ep in checkpoint.projection}
    if set(cp_by_id.keys()) != set(live_by_id.keys()):
        raise ThresholdStateError(
            "THRESHOLD_STATE_INCONSISTENT",
            "Checkpoint projection episode IDs do not match current DB THRESHOLD episodes",
        )

    disabled_open_ids: set[int] = set()
    for eid, cp_ep in cp_by_id.items():
        live_row = live_by_id[eid]
        if (
            live_row["metric_code"] != cp_ep.metric_code
            or live_row.get("direction") != cp_ep.direction
            or int(live_row["acknowledgement_version"]) != cp_ep.acknowledgement_version
        ):
            raise ThresholdStateError(
                "THRESHOLD_STATE_INCONSISTENT",
                f"Episode {eid} metadata diverges between checkpoint and live DB",
            )

        if _is_valid_step2_threshold_disable_closure(cp_ep, live_row):
            disabled_open_ids.add(eid)
            continue

        expected_status = "OPEN" if cp_ep.is_open else "CLOSED"
        if (
            int(live_row["revision_no"]) != cp_ep.revision_no
            or live_row["lifecycle_status"] != expected_status
            or live_row.get("end_reason") != cp_ep.end_reason
        ):
            raise ThresholdStateError(
                "THRESHOLD_STATE_INCONSISTENT",
                f"Episode {eid} revision or lifecycle diverges between checkpoint and live DB",
            )
        if (
            int(live_row["config_id"]) != cp_ep.config_id
            or live_row.get("period_id") != cp_ep.period_id
        ):
            raise ThresholdStateError(
                "THRESHOLD_STATE_INCONSISTENT",
                f"Episode {eid} config/period diverges between checkpoint and live DB",
            )
        if (
            live_row.get("start_measurement_id") != cp_ep.start_measurement_id
            or live_row.get("end_measurement_id") != cp_ep.end_measurement_id
            or live_row.get("start_event_time") != (cp_ep.start_key.event_time_ms // 1000)
        ):
            raise ThresholdStateError(
                "THRESHOLD_STATE_INCONSISTENT",
                f"Episode {eid} sample pointers diverge between checkpoint and live DB",
            )
        if cp_ep.end_key is not None:
            if cp_ep.end_key.phase == PHASE_SAMPLE:
                if live_row.get("end_event_time") != (cp_ep.end_key.event_time_ms // 1000):
                    raise ThresholdStateError(
                        "THRESHOLD_STATE_INCONSISTENT",
                        f"Episode {eid} end_event_time diverges between checkpoint and live DB",
                    )
            else:
                if live_row.get("end_event_time") is not None:
                    raise ThresholdStateError(
                        "THRESHOLD_STATE_INCONSISTENT",
                        f"Episode {eid} non-sample closure unexpectedly has end_event_time",
                    )
                closed_dt = live_row.get("closed_at")
                if (
                    closed_dt is None
                    or utc_ms_from_datetime(closed_dt) != cp_ep.end_key.event_time_ms
                ):
                    raise ThresholdStateError(
                        "THRESHOLD_STATE_INCONSISTENT",
                        f"Episode {eid} closure timestamp diverges between checkpoint and live DB",
                    )

    return ValidatedCheckpointContext(
        checkpoint=checkpoint,
        live_by_episode_id=live_by_id,
        disabled_open_episode_ids=frozenset(disabled_open_ids),
    )


def encode_threshold_metadata(*, is_current: bool, transition: str) -> str:
    if transition not in VALID_TRANSITIONS:
        raise ValueError(f"Unsupported threshold metadata transition: {transition}")
    return json.dumps(
        {
            "codec": 1,
            "is_current": is_current,
            "transition": transition,
        },
        separators=(",", ":"),
    )
