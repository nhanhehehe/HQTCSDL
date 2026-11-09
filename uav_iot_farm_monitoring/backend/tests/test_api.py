import os
import sys
from datetime import datetime
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite:///./test_farm_iot.db"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from app.database import Base, engine
from app.main import app
from app.seed import seed


Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)
seed()
client = TestClient(app)


def login():
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": "Admin@123"})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_health_and_dashboard():
    assert client.get("/api/v1/health").json() == {"status": "ok"}
    response = client.get("/api/v1/dashboard", headers=login())
    assert response.status_code == 200
    assert response.json()["counts"]["sensors"] == 1


def test_sync_is_idempotent():
    payload = {"gateway_id": "GATEWAY_001", "records": [{"sensor_id": "NODE_001", "sequence_no": 999, "temperature": 31.2, "air_humidity": 70.0, "pressure": 1007.0, "light": 300.0, "soil_moisture": 40.0, "source": "physical", "quality": "valid", "measured_at": datetime.utcnow().isoformat(), "collected_at": datetime.utcnow().isoformat()}]}
    headers = {"X-Gateway-Key": "gateway-local-key"}
    first = client.post("/api/v1/sync/batches", json=payload, headers=headers)
    second = client.post("/api/v1/sync/batches", json=payload, headers=headers)
    assert first.status_code == 200
    assert first.json()["acknowledgements"][0]["status"] == "accepted"
    assert second.json()["acknowledgements"][0]["status"] == "duplicate"


def test_gateway_mission_lifecycle_and_report():
    auth = login()
    mission = client.post(
        "/api/v1/missions",
        headers=auth,
        json={"farm_id": 1, "gateway_id": 1, "name": "Gateway test", "sensor_node_ids": [1]},
    )
    assert mission.status_code == 201

    gateway_headers = {"X-Gateway-Key": "gateway-local-key"}
    active = client.get("/api/v1/gateways/GATEWAY_001/active-mission", headers=gateway_headers)
    assert active.status_code == 200
    assert active.json()["mission"]["status"] == "running"

    payload = {"gateway_id": "GATEWAY_001", "records": [{"sensor_id": "NODE_001", "sequence_no": 1000, "temperature": 36.0, "air_humidity": 70.0, "pressure": 1007.0, "light": 300.0, "soil_moisture": 40.0, "measured_at": datetime.utcnow().isoformat()}]}
    synced = client.post("/api/v1/sync/batches", json=payload, headers=gateway_headers)
    assert synced.status_code == 200
    assert synced.json()["acknowledgements"][0]["status"] == "accepted"

    missions = client.get("/api/v1/missions", headers=auth)
    assert missions.status_code == 200
    assert missions.json()[0]["status"] == "completed"
    report = client.get("/api/v1/reports/summary", headers=auth)
    assert report.status_code == 200
    assert report.json()["open_alerts"] >= 1
