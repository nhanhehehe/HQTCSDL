from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import NodeTimeProfile, Settings
from app.core.errors import WebError
from app.core.web import (
    format_decimal_clean,
    format_utc_iso_to_vi,
    json_loads_no_duplicates,
    parse_rfc3339_utc,
    resolve_event_range,
    utc_ms_from_datetime,
)


def test_g3_settings_validation():
    default = Settings(_env_file=None)
    assert default.report_secret is None
    assert default.report_snapshot_ttl_seconds == 900
    assert default.node_time_profiles == {}

    valid_secret = "R" * 32
    configured = Settings(
        _env_file=None,
        csrf_secret=SecretStr("C" * 32),
        cursor_secret=SecretStr("K" * 32),
        report_secret=SecretStr(valid_secret),
        report_snapshot_ttl_seconds=600,
        node_time_profiles='{"NODE-01":{"sampling_interval_seconds":60,"normal_delivery_budget_seconds":30}}',
    )
    assert configured.report_snapshot_ttl_seconds == 600
    profile = configured.node_time_profiles["NODE-01"]
    assert isinstance(profile, NodeTimeProfile)
    assert profile.stale_after_seconds == 90

    # Multibyte UTF-8 secret meeting >= 32 bytes
    Settings(_env_file=None, report_secret=SecretStr("á" * 16))

    # Too short (< 32 UTF-8 bytes)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, report_secret=SecretStr("short-secret"))

    # Duplicate secret rejected
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            csrf_secret=SecretStr(valid_secret),
            report_secret=SecretStr(valid_secret),
        )
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            cursor_secret=SecretStr(valid_secret),
            report_secret=SecretStr(valid_secret),
        )

    # Invalid TTL
    for bad_ttl in (0, -1, True, "0", "abc", "12.5"):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, report_snapshot_ttl_seconds=bad_ttl)

    # Invalid node_time_profiles
    for bad_profiles in (
        '{"NODE-01":{"sampling_interval_seconds":0,"normal_delivery_budget_seconds":0}}',
        '{"NODE-01":{"sampling_interval_seconds":60,"normal_delivery_budget_seconds":-1}}',
        '{"NODE-01":{"sampling_interval_seconds":true,"normal_delivery_budget_seconds":0}}',
        '{"NODE 01":{"sampling_interval_seconds":60,"normal_delivery_budget_seconds":0}}',
        '{"NODE-01":{"sampling_interval_seconds":60,"normal_delivery_budget_seconds":0},"NODE-01":{"sampling_interval_seconds":60,"normal_delivery_budget_seconds":1}}',
        "[]",
        "{not-json}",
    ):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, node_time_profiles=bad_profiles)

    # Budget == 0 is allowed when explicitly configured
    zero_budget = Settings(
        _env_file=None,
        node_time_profiles='{"NODE-02":{"sampling_interval_seconds":60,"normal_delivery_budget_seconds":0}}',
    )
    assert zero_budget.node_time_profiles["NODE-02"].stale_after_seconds == 60


def test_parse_rfc3339_and_fractional_range_boundaries():
    ts = parse_rfc3339_utc("2026-11-15T17:00:00.250+07:00")
    assert ts.utc_iso == "2026-11-15T10:00:00.250Z"

    # Naive timestamp rejected
    with pytest.raises(ValueError):
        parse_rfc3339_utc("2026-11-15T10:00:00")

    # E07-02: fractional second boundaries
    r1 = resolve_event_range(
        "1970-01-01T00:01:40.1Z",
        "1970-01-01T00:01:41.9Z",
        as_of_dt=datetime(2026, 11, 15, 0, 0, 0),
    )
    assert (r1.lo_epoch, r1.hi_epoch) == (101, 101)

    # Valid range with no integer second inside -> lo > hi (EMPTY_RANGE, not RANGE_INVALID)
    r2 = resolve_event_range(
        "1970-01-01T00:01:40.1Z",
        "1970-01-01T00:01:40.9Z",
        as_of_dt=datetime(2026, 11, 15, 0, 0, 0),
    )
    assert (r2.lo_epoch, r2.hi_epoch) == (101, 100)

    # Exact integer fractional zeros preserve inclusive lower bound
    r3 = resolve_event_range(
        "1970-01-01T00:01:40.000Z",
        "1970-01-01T00:01:40.000Z",
        as_of_dt=datetime(2026, 11, 15, 0, 0, 0),
    )
    assert (r3.lo_epoch, r3.hi_epoch) == (100, 100)

    # Default [as_of - 24h, as_of]
    as_of = datetime(2026, 11, 15, 10, 0, 0, 125000)
    r_def = resolve_event_range(None, None, as_of_dt=as_of)
    assert r_def.from_ts.utc_iso == "2026-11-14T10:00:00.125Z"
    assert r_def.to_ts.utc_iso == "2026-11-15T10:00:00.125Z"

    # from > to -> 400 RANGE_INVALID
    with pytest.raises(WebError) as exc:
        resolve_event_range(
            "2026-11-15T10:00:01Z",
            "2026-11-15T10:00:00Z",
            as_of_dt=as_of,
        )
    assert exc.value.code == "RANGE_INVALID"

    # Missing timezone -> 400 RANGE_INVALID
    with pytest.raises(WebError) as exc2:
        resolve_event_range("2026-11-15T10:00:00", None, as_of_dt=as_of)
    assert exc2.value.code == "RANGE_INVALID"


def test_decimal_and_time_formatting_helpers():
    assert format_decimal_clean(Decimal("-0.00")) == "0"
    assert format_decimal_clean(Decimal("28.50")) == "28.5"
    assert format_decimal_clean(Decimal("12400.0")) == "12400"
    assert format_decimal_clean(Decimal("1E+3")) == "1000"
    assert utc_ms_from_datetime(datetime(1970, 1, 1, 0, 0, 1, 250000, tzinfo=UTC)) == 1250
    assert format_utc_iso_to_vi("2026-11-15T02:00:00.000Z") == "2026-11-15T09:00:00.000+07:00"
    with pytest.raises(ValueError):
        json_loads_no_duplicates('{"a":1,"a":2}')
