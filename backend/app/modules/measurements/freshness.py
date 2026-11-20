from typing import Any

from app.core.config import NodeTimeProfile


def format_age_seconds(age_ms: int) -> str:
    if age_ms == 0:
        return "0"
    sign = "-" if age_ms < 0 else ""
    whole, ms = divmod(abs(age_ms), 1000)
    if ms == 0:
        return f"{sign}{whole}"
    frac = f"{ms:03d}".rstrip("0")
    return f"{sign}{whole}.{frac}"


def compute_freshness(
    *,
    current_state: str,
    t_node: int | None,
    as_of_utc_ms: int,
    profile: NodeTimeProfile | None,
) -> dict[str, Any]:
    stale_after = profile.stale_after_seconds if profile is not None else None
    if current_state in {"NO_DATA", "NO_EVENT_TIME"} or t_node is None:
        return {
            "state": current_state,
            "age_seconds": None,
            "stale_after_seconds": stale_after,
        }

    age_ms = as_of_utc_ms - int(t_node) * 1000
    age_str = format_age_seconds(age_ms)
    if age_ms < 0:
        state = "CLOCK_AHEAD"
    elif profile is None:
        state = "PROFILE_MISSING"
    elif age_ms > stale_after * 1000:
        state = "STALE"
    else:
        state = "FRESH"

    return {
        "state": state,
        "age_seconds": age_str,
        "stale_after_seconds": stale_after,
    }
