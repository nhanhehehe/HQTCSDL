import base64
import csv
import hmac
import io
import json
from contextlib import contextmanager
from datetime import datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import Settings
from app.main import create_app
from app.modules.measurements.repository import METRIC_ORDER, METRIC_UNITS
from app.modules.reports import repository as report_repo
from app.modules.reports.rounding import round_avg_half_up, round_stat_half_up

NOW = datetime(2026, 11, 15, 4, 5, 0)
WEB_HEADERS = {
    "Origin": "http://127.0.0.1:5173",
    "X-HQ-Client": "web",
    "X-CSRF-Token": "valid-csrf",
}


def _epoch(dt: datetime) -> int:
    return int((dt - datetime(1970, 1, 1)).total_seconds())


def _settings(**overrides):
    defaults = {
        "_env_file": None,
        "env": "test",
        "csrf_secret": SecretStr("C" * 32),
        "cursor_secret": SecretStr("K" * 32),
        "report_secret": SecretStr("R" * 32),
        "report_snapshot_ttl_seconds": 900,
        "dataset_generation": "1",
        "web_origin": "http://127.0.0.1:5173",
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _client_with_fake_reports(monkeypatch, settings=None):
    cfg = settings or _settings()
    app = create_app(cfg)
    state = {
        "now": NOW,
        "session_id": 1,
        "session_expires_at": NOW + timedelta(hours=2),
        "session_revoked": False,
        "nodes": {
            101: {
                "node_id": 101,
                "device_code": "NODE-01",
                "node_name": "Node demo",
                "zone_id": 11,
                "zone_code": "INDOOR",
                "zone_name": "Trong nhà",
                "farm_id": 1,
                "farm_code": "FARM-01",
                "farm_name": "Farm demo",
            },
            102: {
                "node_id": 102,
                "device_code": "=SUM(1,2)",
                "node_name": "Dangerous Node",
                "zone_id": 12,
                "zone_code": " \t+ZONE",
                "zone_name": 'Zone Quote, "Comma"\nNewline',
                "farm_id": 2,
                "farm_code": "\uff1dFARM",
                "farm_name": "Archived Farm",
            },
        },
        "measurements": [],
        "values": {},
        "next_mid": 1,
        "corrupt_missing_metric": False,
    }

    def add_record(
        *,
        node_id: int = 101,
        t_node: int | None = None,
        values: dict[str, tuple[str, Decimal | None]] | None = None,
    ):
        mid = state["next_mid"]
        state["next_mid"] += 1
        state["measurements"].append({"measurement_id": mid, "node_id": node_id, "t_node": t_node})
        default_vals = {
            "temperature": ("OK", Decimal("25.00")),
            "airHumidity": ("OK", Decimal("70.00")),
            "pressure": ("OK", Decimal("1010.00")),
            "light": ("OK", Decimal("0.0")),
            "soilMoisture": ("READ_ERROR", None),
        }
        if values:
            default_vals.update(values)
        state["values"][mid] = default_vals
        return mid

    state["add_record"] = add_record

    @contextmanager
    def fake_tx(
        database,
        settings_arg,
        cookie,
        csrf=None,
        *,
        write=False,
        require_csrf=None,
        lock_session=None,
        isolation=None,
    ):
        if (
            cookie != "valid-cookie"
            or state["session_revoked"]
            or (state["now"] >= state["session_expires_at"])
        ):
            from app.modules.auth.service import invalid_session

            raise invalid_session()
        if require_csrf and csrf != "valid-csrf":
            from app.core.errors import WebError

            raise WebError(403, "CSRF_INVALID", "CSRF_INVALID")
        yield (
            state,
            {
                "session_id": state["session_id"],
                "admin_id": 1,
                "username": "Admin",
                "expires_at": state["session_expires_at"],
                "db_now": state["now"],
                "token_digest": b"x" * 32,
            },
        )

    def fake_guard(session, digest, *, lock=False):
        if session["session_revoked"] or session["now"] >= session["session_expires_at"]:
            return None
        return {
            "session_id": session["session_id"],
            "admin_id": 1,
            "username": "Admin",
            "expires_at": session["session_expires_at"],
            "db_now": session["now"],
        }

    monkeypatch.setattr("app.modules.reports.service.web_session_transaction", fake_tx)
    monkeypatch.setattr("app.modules.reports.service.auth_repo.session_guard", fake_guard)
    monkeypatch.setattr(
        report_repo, "get_node_report_context", lambda s, nid: s["nodes"].get(int(nid))
    )

    def fake_agg(session, *, node_id, lo, hi):
        if lo > hi:
            return 0, [
                {
                    "metric_code": code,
                    "unit": METRIC_UNITS[code],
                    "seen_count": 0,
                    "n_valid": 0,
                    "bad_rows": 0,
                    "min_value": None,
                    "max_value": None,
                    "sum_value": None,
                }
                for code in METRIC_ORDER
            ]
        headers = [
            m
            for m in session["measurements"]
            if m["node_id"] == node_id and m["t_node"] is not None and lo <= m["t_node"] <= hi
        ]
        n_total = len(headers)
        rows = []
        for idx, code in enumerate(METRIC_ORDER):
            if session["corrupt_missing_metric"] and idx == 4:
                continue
            vals = []
            for h in headers:
                status, val = session["values"][h["measurement_id"]][code]
                if status == "OK" and val is not None:
                    vals.append(val)
            rows.append(
                {
                    "metric_code": code,
                    "unit": METRIC_UNITS[code],
                    "seen_count": max(0, n_total - 1)
                    if session["corrupt_missing_metric"] and n_total > 0
                    else n_total,
                    "n_valid": len(vals),
                    "bad_rows": 0,
                    "min_value": min(vals) if vals else None,
                    "max_value": max(vals) if vals else None,
                    "sum_value": sum(vals, Decimal(0)) if vals else None,
                }
            )
        return n_total, rows

    monkeypatch.setattr(report_repo, "aggregate_node_measurements", fake_agg)
    return app, state


def test_rounding_half_up_exact():
    assert round_avg_half_up(Decimal("3.375"), 3) == "1.13"
    assert round_avg_half_up(Decimal("-3.375"), 3) == "-1.13"
    assert round_avg_half_up(Decimal("-0.004"), 1) == "0"
    assert round_avg_half_up(Decimal("-0.005"), 1) == "-0.01"
    assert round_stat_half_up(Decimal("28.50")) == "28.5"
    assert round_stat_half_up(Decimal("-0.00")) == "0"


def test_create_report_strict_input_half_up_and_503_guards(monkeypatch):
    app, state = _client_with_fake_reports(monkeypatch)
    add_record = state["add_record"]
    # Build dataset in [02:00, 04:00] UTC (plus records outside & UNKNOWN)
    t_in1 = _epoch(datetime(2026, 11, 15, 2, 0, 0))
    t_in2 = _epoch(datetime(2026, 11, 15, 3, 0, 0))
    t_in3 = _epoch(datetime(2026, 11, 15, 4, 0, 0))
    t_in4 = _epoch(datetime(2026, 11, 15, 3, 30, 0))
    t_out = _epoch(datetime(2026, 11, 15, 1, 59, 59))

    # 3 valid values for temperature whose average is 1.125 -> 1.13, plus 1 duplicate 0 and 1 error
    # Total in range = 4: 0, 0, 3.375? Keep within [-40..85]: 1.12, 1.13 -> (1.12+1.13)/2 = 1.125!
    add_record(
        node_id=101,
        t_node=t_in1,
        values={"temperature": ("OK", Decimal("1.12")), "light": ("OK", Decimal("0"))},
    )
    add_record(
        node_id=101,
        t_node=t_in2,
        values={"temperature": ("OK", Decimal("1.13")), "light": ("OK", Decimal("0"))},
    )
    add_record(
        node_id=101,
        t_node=t_in3,
        values={"temperature": ("READ_ERROR", None), "light": ("OK", Decimal("200.0"))},
    )
    add_record(
        node_id=101,
        t_node=t_in4,
        values={"temperature": ("INVALID_VALUE", None), "light": ("READ_ERROR", None)},
    )
    # Out-of-range and UNKNOWN must be ignored
    add_record(node_id=101, t_node=t_out, values={"temperature": ("OK", Decimal("80.0"))})
    add_record(node_id=101, t_node=None, values={"temperature": ("OK", Decimal("80.0"))})

    with TestClient(app) as client:
        # 401 unauthenticated
        assert (
            client.post(
                "/api/v1/reports/measurements", headers=WEB_HEADERS, json={"node_id": "101"}
            ).status_code
            == 401
        )
        client.cookies.set("hq_session", "valid-cookie")
        # 403 missing CSRF
        assert (
            client.post(
                "/api/v1/reports/measurements",
                headers={"Origin": "http://127.0.0.1:5173", "X-HQ-Client": "web"},
                json={"node_id": "101"},
            ).status_code
            == 403
        )

        # G3-D03: strict request rejects null, extra farm_id, non-str node_id
        for bad_body in (
            {"node_id": 101},
            {"node_id": "101", "from": None},
            {"node_id": "101", "to": None},
            {"node_id": "101", "farm_id": "1"},
        ):
            r_bad = client.post("/api/v1/reports/measurements", headers=WEB_HEADERS, json=bad_body)
            assert r_bad.status_code == 400
            assert r_bad.json()["error"]["code"] == "INPUT_INVALID"

        # Bad range -> 400 RANGE_INVALID
        r_rng = client.post(
            "/api/v1/reports/measurements",
            headers=WEB_HEADERS,
            json={"node_id": "101", "from": "2026-11-15T04:00:01Z", "to": "2026-11-15T04:00:00Z"},
        )
        assert r_rng.status_code == 400
        assert r_rng.json()["error"]["code"] == "RANGE_INVALID"

        # Missing Node -> 404
        assert (
            client.post(
                "/api/v1/reports/measurements", headers=WEB_HEADERS, json={"node_id": "999"}
            ).status_code
            == 404
        )

        # Valid create in [02:00, 04:00] UTC
        res = client.post(
            "/api/v1/reports/measurements",
            headers=WEB_HEADERS,
            json={
                "node_id": "101",
                "from": "2026-11-15T02:00:00Z",
                "to": "2026-11-15T04:00:00Z",
            },
        )
        assert res.status_code == 200
        body = res.json()
        assert body["N_total"] == "4"
        assert body["context"]["timezone"] == "UTC+07:00"
        assert body["expires_at"] == "2026-11-15T04:20:00.000Z"
        assert [r["metric_code"] for r in body["rows"]] == list(METRIC_ORDER)

        temp_row = body["rows"][0]
        assert temp_row == {
            "metric_code": "temperature",
            "unit": "°C",
            "n_valid": "2",
            "n_invalid": "2",
            "min": "1.12",
            "max": "1.13",
            "avg": "1.13",
        }
        # 2 zeros + 200 -> sum=200, avg=66.67, min=0 (duplicates NOT deduplicated)
        light_row = body["rows"][3]
        assert light_row == {
            "metric_code": "light",
            "unit": "lux",
            "n_valid": "3",
            "n_invalid": "1",
            "min": "0",
            "max": "200",
            "avg": "66.67",
        }
        # All invalid soilMoisture -> null stats, n_invalid == N_total
        assert body["rows"][4] == {
            "metric_code": "soilMoisture",
            "unit": "%",
            "n_valid": "0",
            "n_invalid": "4",
            "min": None,
            "max": None,
            "avg": None,
        }

        # Empty fractional range -> N_total="0" with 5 rows
        empty_res = client.post(
            "/api/v1/reports/measurements",
            headers=WEB_HEADERS,
            json={
                "node_id": "101",
                "from": "1970-01-01T00:01:40.1Z",
                "to": "1970-01-01T00:01:40.9Z",
            },
        )
        assert empty_res.status_code == 200
        assert empty_res.json()["N_total"] == "0"
        assert len(empty_res.json()["rows"]) == 5

        # Corrupt children -> 503 STORAGE_UNAVAILABLE
        state["corrupt_missing_metric"] = True
        assert (
            client.post(
                "/api/v1/reports/measurements", headers=WEB_HEADERS, json={"node_id": "101"}
            ).status_code
            == 503
        )

    # Missing HQ_REPORT_SECRET -> 503 STORAGE_UNAVAILABLE after auth/CSRF
    app_no_secret, _ = _client_with_fake_reports(
        monkeypatch, settings=_settings(report_secret=None)
    )
    with TestClient(app_no_secret) as client2:
        client2.cookies.set("hq_session", "valid-cookie")
        assert (
            client2.post(
                "/api/v1/reports/measurements", headers=WEB_HEADERS, json={"node_id": "101"}
            ).status_code
            == 503
        )


def test_csv_export_bytes_security_and_formula_protection(monkeypatch):
    app, state = _client_with_fake_reports(monkeypatch)
    # Create record with negative temperature to ensure negative numeric is NOT prefixed with text:
    state["add_record"](
        node_id=102,
        t_node=_epoch(datetime(2026, 11, 15, 3, 0, 0)),
        values={"temperature": ("OK", Decimal("-5.50"))},
    )

    with TestClient(app) as client:
        client.cookies.set("hq_session", "valid-cookie")
        create_res = client.post(
            "/api/v1/reports/measurements",
            headers=WEB_HEADERS,
            json={
                "node_id": "102",
                "from": "2026-11-15T02:00:00Z",
                "to": "2026-11-15T04:00:00Z",
            },
        )
        assert create_res.status_code == 200
        snap = create_res.json()
        tok = snap["snapshot_token"]

        # New record added AND node renamed AFTER snapshot must NOT alter CSV from old token
        state["add_record"](
            node_id=102,
            t_node=_epoch(datetime(2026, 11, 15, 3, 30, 0)),
            values={"temperature": ("OK", Decimal("30.00"))},
        )
        state["nodes"][102]["device_code"] = "RENAMED-NODE"

        csv_res = client.post(
            "/api/v1/reports/measurements/csv",
            headers=WEB_HEADERS,
            json={"snapshot_token": tok},
        )
        assert csv_res.status_code == 200
        assert csv_res.headers["content-type"].startswith("text/csv")
        assert (
            csv_res.headers["content-disposition"]
            == 'attachment; filename="measurements_node_102_20261115T040500000Z.csv"'
        )
        assert csv_res.headers["x-content-type-options"] == "nosniff"
        assert csv_res.headers["cache-control"] == "no-store"

        raw_bytes = csv_res.content
        assert raw_bytes.startswith(b"\xef\xbb\xbf")
        text_body = raw_bytes[3:].decode("utf-8")
        assert "\r\n" in text_body

        parsed_rows = list(csv.reader(io.StringIO(text_body, newline="")))
        assert len(parsed_rows) == 6
        assert parsed_rows[0] == [
            "report_as_of",
            "from_measured_at",
            "to_measured_at",
            "timezone",
            "farm_code",
            "zone_code",
            "node_code",
            "data_source",
            "total_records",
            "metric",
            "unit",
            "valid_count",
            "invalid_count",
            "min",
            "max",
            "avg",
        ]
        temp_csv = parsed_rows[1]
        assert temp_csv[0] == "2026-11-15T11:05:00.000+07:00"
        assert temp_csv[1] == "2026-11-15T09:00:00.000+07:00"
        assert temp_csv[2] == "2026-11-15T11:00:00.000+07:00"
        assert temp_csv[3] == "UTC+07:00"
        # Fullwidth '=' farm_code, leading whitespace '+' zone_code, '=' node_code prefixed!
        assert temp_csv[4] == "text:\uff1dFARM"
        assert temp_csv[5] == "text: \t+ZONE"
        assert temp_csv[6] == "text:=SUM(1,2)"
        assert temp_csv[8] == "1"
        assert temp_csv[9] == "temperature"
        # Negative numeric MUST NOT be prefixed with text:
        assert temp_csv[13] == "-5.5"
        assert temp_csv[14] == "-5.5"
        assert temp_csv[15] == "-5.5"
        # Null stats -> empty string
        assert parsed_rows[5][13:16] == ["", "", ""]


def test_csv_token_tamper_expiry_session_and_context_errors(monkeypatch):
    app, state = _client_with_fake_reports(monkeypatch)
    with TestClient(app) as client:
        client.cookies.set("hq_session", "valid-cookie")
        res = client.post(
            "/api/v1/reports/measurements",
            headers=WEB_HEADERS,
            json={"node_id": "101"},
        )
        tok = res.json()["snapshot_token"]

        # Tampered token -> 400 TOKEN_INVALID
        bad = tok[:-1] + ("A" if tok[-1] != "A" else "B")
        assert (
            client.post(
                "/api/v1/reports/measurements/csv",
                headers=WEB_HEADERS,
                json={"snapshot_token": bad},
            ).json()["error"]["code"]
            == "TOKEN_INVALID"
        )

        # Oversized token (> 32768 chars) -> 400 TOKEN_INVALID
        assert (
            client.post(
                "/api/v1/reports/measurements/csv",
                headers=WEB_HEADERS,
                json={"snapshot_token": "v1." + ("a" * 32768) + ".sig"},
            ).status_code
            == 400
        )

        # Re-signed token with duplicate JSON key -> 400 TOKEN_INVALID
        dup_json = '{"codec":1,"codec":1}'
        dup_b64 = base64.urlsafe_b64encode(dup_json.encode()).decode().rstrip("=")
        dup_sig = (
            base64.urlsafe_b64encode(hmac.digest(b"R" * 32, f"v1.{dup_b64}".encode(), "sha256"))
            .decode()
            .rstrip("=")
        )
        assert (
            client.post(
                "/api/v1/reports/measurements/csv",
                headers=WEB_HEADERS,
                json={"snapshot_token": f"v1.{dup_b64}.{dup_sig}"},
            ).json()["error"]["code"]
            == "TOKEN_INVALID"
        )

        # Different session_id -> 400 TOKEN_INVALID
        state["session_id"] = 2
        assert (
            client.post(
                "/api/v1/reports/measurements/csv",
                headers=WEB_HEADERS,
                json={"snapshot_token": tok},
            ).json()["error"]["code"]
            == "TOKEN_INVALID"
        )
        state["session_id"] = 1

        # Token bound to different dataset_generation -> 409 REPORT_CONTEXT_CHANGED
        v, b, _ = tok.split(".")
        decoded = json.loads(base64.urlsafe_b64decode(b + "=" * (-len(b) % 4)))
        decoded["instance_generation"] = "2"
        new_b = (
            base64.urlsafe_b64encode(
                json.dumps(decoded, sort_keys=True, separators=(",", ":")).encode()
            )
            .decode()
            .rstrip("=")
        )
        new_s = (
            base64.urlsafe_b64encode(hmac.digest(b"R" * 32, f"v1.{new_b}".encode(), "sha256"))
            .decode()
            .rstrip("=")
        )
        ctx_res = client.post(
            "/api/v1/reports/measurements/csv",
            headers=WEB_HEADERS,
            json={"snapshot_token": f"v1.{new_b}.{new_s}"},
        )
        assert ctx_res.status_code == 409
        assert ctx_res.json()["error"]["code"] == "REPORT_CONTEXT_CHANGED"

        # Token expired inside valid session -> 410 SNAPSHOT_EXPIRED
        state["now"] = NOW + timedelta(minutes=16)
        exp_res = client.post(
            "/api/v1/reports/measurements/csv",
            headers=WEB_HEADERS,
            json={"snapshot_token": tok},
        )
        assert exp_res.status_code == 410
        assert exp_res.json()["error"]["code"] == "SNAPSHOT_EXPIRED"

        # Both session and token expired -> 401 SESSION_INVALID takes priority!
        state["now"] = NOW + timedelta(hours=3)
        both_exp = client.post(
            "/api/v1/reports/measurements/csv",
            headers=WEB_HEADERS,
            json={"snapshot_token": tok},
        )
        assert both_exp.status_code == 401
        assert both_exp.json()["error"]["code"] == "SESSION_INVALID"

        # Reset clock & check missing node -> 404
        state["now"] = NOW + timedelta(minutes=5)
        del state["nodes"][101]
        assert (
            client.post(
                "/api/v1/reports/measurements/csv",
                headers=WEB_HEADERS,
                json={"snapshot_token": tok},
            ).status_code
            == 404
        )
