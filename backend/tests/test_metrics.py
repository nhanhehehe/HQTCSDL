from contextlib import contextmanager
from decimal import Decimal

from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import Settings
from app.main import create_app


def _settings():
    return Settings(
        _env_file=None,
        csrf_secret=SecretStr("test-csrf-secret"),
        cursor_secret=SecretStr("test-cursor-secret"),
    )


def test_metrics_readonly_and_auth(monkeypatch):
    app = create_app(_settings())
    with TestClient(app) as client:
        unauth = client.get("/api/v1/metrics")
        assert unauth.status_code == 401
        assert unauth.headers["cache-control"] == "no-store"

        @contextmanager
        def fake_tx(database, settings, cookie, csrf=None, write=False):
            yield "fake-session"

        class Result:
            def mappings(self):
                return self

            def all(self):
                return [
                    {
                        "metric_code": "temperature",
                        "unit": "°C",
                        "min_value": Decimal("-40.00"),
                        "max_value": Decimal("85.00"),
                        "decimal_places": 2,
                    },
                    {
                        "metric_code": "airHumidity",
                        "unit": "%RH",
                        "min_value": Decimal("0.00"),
                        "max_value": Decimal("100.00"),
                        "decimal_places": 2,
                    },
                    {
                        "metric_code": "pressure",
                        "unit": "hPa",
                        "min_value": Decimal("300.00"),
                        "max_value": Decimal("1100.00"),
                        "decimal_places": 2,
                    },
                    {
                        "metric_code": "light",
                        "unit": "lux",
                        "min_value": Decimal("0.00"),
                        "max_value": Decimal("120000.00"),
                        "decimal_places": 1,
                    },
                    {
                        "metric_code": "soilMoisture",
                        "unit": "%",
                        "min_value": Decimal("0.00"),
                        "max_value": Decimal("100.00"),
                        "decimal_places": 1,
                    },
                ]

        class FakeSession:
            def execute(self, query):
                return Result()

        @contextmanager
        def fake_tx_session(database, settings, cookie, csrf=None, write=False):
            yield FakeSession()

        monkeypatch.setattr("app.modules.devices.service.web_transaction", fake_tx_session)
        client.cookies.set("hq_session", "valid-cookie")
        res = client.get("/api/v1/metrics")
        assert res.status_code == 200
        items = res.json()["items"]
        assert [m["code"] for m in items] == [
            "temperature",
            "airHumidity",
            "pressure",
            "light",
            "soilMoisture",
        ]
        assert items[0]["min_value"] == "-40.00"
        assert isinstance(items[0]["max_value"], str)
        assert items[0]["decimal_places"] == 2
        assert client.post("/api/v1/metrics", json={}).status_code in {404, 405}
