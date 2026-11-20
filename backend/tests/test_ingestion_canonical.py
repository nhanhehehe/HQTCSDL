from decimal import Decimal

import pytest

from app.modules.ingestion.canonical import build_canonical_v1, is_same_original_content
from app.modules.ingestion.validator import (
    MAX_EPOCH_SEC,
    MAX_INT64,
    extract_valid_identity,
    parse_ingestion_json,
    validate_d06_timestamps,
    validate_record_static,
)

REFERENCE_SHA256 = "794467f9ca01b4fad6a65273b3128b2b1d0b96cde3ba9aa6c1bf4d00365a9537"
REFERENCE_BYTES = (
    b"HQTCSDL/measurement/v1\n"
    b'["NODE-01",15,42,"VALID",1794384000,'
    b'["OK",2850],["OK",7524],["OK",100842],["OK",2455],["READ_ERROR",null]]'
)


def _reference_raw_json(overrides: dict | None = None) -> bytes:
    base = (
        '{"device_id":"NODE-01","boot_id":15,"seq":42,"t_node":1794384000,'
        '"time_status":"VALID","recv_time":1794384002,"rssi":-85,'
        '"measurements":{'
        '"temperature":{"value":28.50,"status":"OK"},'
        '"airHumidity":{"value":75.24,"status":"OK"},'
        '"pressure":{"value":1008.42,"status":"OK"},'
        '"light":{"value":245.5,"status":"OK"},'
        '"soilMoisture":{"value":null,"status":"READ_ERROR"}'
        "}}"
    )
    if not overrides:
        return base.encode("utf-8")
    data = parse_ingestion_json(base.encode("utf-8"))
    data.update(overrides)
    return data


def test_c01_reference_canonical_bytes_and_sha256():
    parsed = parse_ingestion_json(_reference_raw_json())
    rec, err = validate_record_static(parsed)
    assert err is None and rec is not None
    raw, digest = build_canonical_v1(rec)
    assert raw == REFERENCE_BYTES
    assert digest.hex() == REFERENCE_SHA256


def test_c02_key_order_and_equivalent_decimals_produce_same_canonical():
    reordered = (
        b'{"measurements":{'
        b'"soilMoisture":{"status":"READ_ERROR","value":null},'
        b'"light":{"status":"OK","value":245.50},'
        b'"pressure":{"value":1008.420,"status":"OK"},'
        b'"airHumidity":{"value":75.24,"status":"OK"},'
        b'"temperature":{"status":"OK","value":28.5}'
        b'},"rssi":-70.25,"recv_time":1794384500,"time_status":"VALID",'
        b'"t_node":1794384000,"seq":42,"boot_id":15,"device_id":"NODE-01"}'
    )
    rec, err = validate_record_static(parse_ingestion_json(reordered))
    assert err is None and rec is not None
    raw, digest = build_canonical_v1(rec)
    assert raw == REFERENCE_BYTES
    assert digest.hex() == REFERENCE_SHA256


def test_c03_metadata_changes_do_not_change_canonical_or_equality():
    base = parse_ingestion_json(_reference_raw_json())
    rec1, _ = validate_record_static(base)
    assert rec1 is not None
    base["recv_time"] = 1794389999
    base["rssi"] = Decimal("-99.75")
    rec2, err = validate_record_static(base)
    assert err is None and rec2 is not None
    assert build_canonical_v1(rec1) == build_canonical_v1(rec2)


def test_c04_c05_c06_c10_typed_original_content_comparison():
    rec_orig, _ = validate_record_static(parse_ingestion_json(_reference_raw_json()))
    assert rec_orig is not None
    stored_header = {
        "boot_id": 15,
        "seq": 42,
        "time_status": "VALID",
        "t_node": 1794384000,
        "canonical_version": 0,  # C10: old version must still compare via typed fields
    }
    stored_values = [
        {"metric_code": "temperature", "read_status": "OK", "value": Decimal("28.50")},
        {"metric_code": "airHumidity", "read_status": "OK", "value": Decimal("75.24")},
        {"metric_code": "pressure", "read_status": "OK", "value": Decimal("1008.42")},
        {"metric_code": "light", "read_status": "OK", "value": Decimal("245.50")},
        {"metric_code": "soilMoisture", "read_status": "READ_ERROR", "value": None},
    ]
    assert is_same_original_content(
        rec_orig,
        stored_device_code="NODE-01",
        stored_header=stored_header,
        stored_values=stored_values,
    )

    # C04: 28.50 -> 28.51
    d4 = parse_ingestion_json(_reference_raw_json())
    d4["measurements"]["temperature"]["value"] = Decimal("28.51")
    rec4, _ = validate_record_static(d4)
    assert rec4 is not None
    assert not is_same_original_content(
        rec4, stored_device_code="NODE-01", stored_header=stored_header, stored_values=stored_values
    )

    # C05: READ_ERROR -> INVALID_VALUE (both null)
    d5 = parse_ingestion_json(_reference_raw_json())
    d5["measurements"]["soilMoisture"]["status"] = "INVALID_VALUE"
    rec5, _ = validate_record_static(d5)
    assert rec5 is not None
    assert not is_same_original_content(
        rec5, stored_device_code="NODE-01", stored_header=stored_header, stored_values=stored_values
    )

    # C06: VALID/timestamp -> UNKNOWN/null
    d6 = parse_ingestion_json(_reference_raw_json())
    d6["time_status"] = "UNKNOWN"
    d6["t_node"] = None
    rec6, _ = validate_record_static(d6)
    assert rec6 is not None
    assert not is_same_original_content(
        rec6, stored_device_code="NODE-01", stored_header=stored_header, stored_values=stored_values
    )


def test_c07_c08_c09_scale_negative_zero_and_bigint_boundaries():
    # C07: temperature 28.567 rejected before scale/hash (never rounded)
    d7 = parse_ingestion_json(_reference_raw_json())
    d7["measurements"]["temperature"]["value"] = Decimal("28.567")
    rec7, err7 = validate_record_static(d7)
    assert rec7 is None and err7 is not None

    # C08: light -0.0 and 0 produce identical canonical bytes/hash
    d8a = parse_ingestion_json(_reference_raw_json())
    d8a["measurements"]["light"]["value"] = Decimal("-0.0")
    d8b = parse_ingestion_json(_reference_raw_json())
    d8b["measurements"]["light"]["value"] = 0
    rec8a, err8a = validate_record_static(d8a)
    rec8b, err8b = validate_record_static(d8b)
    assert err8a is None and err8b is None and rec8a is not None and rec8b is not None
    assert build_canonical_v1(rec8a) == build_canonical_v1(rec8b)

    # C09: max int64 boot_id and seq + max epoch
    d9 = parse_ingestion_json(_reference_raw_json())
    d9["boot_id"] = MAX_INT64
    d9["seq"] = MAX_INT64
    d9["t_node"] = MAX_EPOCH_SEC
    d9["recv_time"] = MAX_EPOCH_SEC
    rec9, err9 = validate_record_static(d9)
    assert err9 is None and rec9 is not None
    raw9, _ = build_canonical_v1(rec9)
    assert str(MAX_INT64).encode("ascii") in raw9


def test_strict_unknown_fields_and_identity_echo():
    # G2-D02a: extra field on record or metric rejected
    d = parse_ingestion_json(_reference_raw_json())
    d["farm_id"] = "FARM-1"
    rec, err = validate_record_static(d)
    assert rec is None and "Unexpected" in (err or "")

    d2 = parse_ingestion_json(_reference_raw_json())
    d2["measurements"]["temperature"]["unit"] = "C"
    rec2, err2 = validate_record_static(d2)
    assert rec2 is None and err2 is not None

    # D04: echo only valid identity fields (reject bool/string/negative/zero seq)
    assert extract_valid_identity({"device_id": "NODE-01", "boot_id": True, "seq": "1"}) == {
        "device_id": "NODE-01"
    }
    assert extract_valid_identity({"device_id": "NODE-01", "boot_id": 0, "seq": 0}) == {
        "device_id": "NODE-01",
        "boot_id": 0,
    }
    assert extract_valid_identity(None) == {}


@pytest.mark.parametrize(
    ("t_node", "recv_time", "backend_sec", "expected_ok"),
    [
        (1000, 1000, 1000, True),
        (1120, 1000, 1000, True),  # +120 inclusive
        (1121, 1000, 1000, False),  # t_node > recv_time + 120
        (1121, 1120, 1000, False),  # t_node > backend + 120
        (1000, 1120, 1000, True),  # recv_time == backend + 120
        (1000, 1121, 1000, False),  # recv_time > backend + 120
        (100, 100, 200000, True),  # >24h backlog accepted
    ],
)
def test_d06_timestamp_boundaries(t_node, recv_time, backend_sec, expected_ok):
    d = parse_ingestion_json(_reference_raw_json())
    d["t_node"] = t_node
    d["recv_time"] = recv_time
    rec, err = validate_record_static(d)
    assert err is None and rec is not None
    d06_err = validate_d06_timestamps(rec, backend_sec)
    assert (d06_err is None) is expected_ok
