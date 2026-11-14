import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def application(tmp_path):
    (tmp_path / "index.html").write_text("<html>foundation</html>")
    return create_app(
        Settings(_env_file=None, env="test", database_url=None, migration_database_url=None),
        tmp_path,
    )


def test_liveness_is_separate_from_database_readiness(application):
    with TestClient(application) as client:
        assert client.get("/health/live").json()["status"] == "alive"
        result = client.get("/health/ready")
        assert result.status_code == 503
        assert result.json()["checks"]["database"] == "unavailable"
        assert result.json()["monitoring"] == "not_implemented"


def test_probe_failure_does_not_leak_credentials(application, monkeypatch):
    def fail():
        raise RuntimeError("password=SECRET_VALUE")

    monkeypatch.setattr(application.state.database, "probe", fail)
    with TestClient(application) as client:
        result = client.get("/health/ready")
        assert result.status_code == 503
        assert "SECRET_VALUE" not in result.text


def test_database_success_reports_foundation_scope(application, monkeypatch):
    monkeypatch.setattr(application.state.database, "probe", lambda: None)
    with TestClient(application) as client:
        assert client.get("/health/ready").json()["scope"] == "foundation"
        assert client.get("/health/ready").status_code == 200


@pytest.mark.parametrize("path", ["/api", "/api/missing", "/health/missing", "/assets/missing.js"])
def test_reserved_paths_and_missing_assets_never_return_spa(application, path):
    with TestClient(application) as client:
        response = client.get(path)
        assert response.status_code == 404
        assert "text/html" not in response.headers["content-type"]


def test_spa_and_request_id(application):
    with TestClient(application) as client:
        response = client.get("/measurements")
        assert response.status_code == 200
        assert "foundation" in response.text
        assert len(response.headers["x-request-id"]) == 32
        assert client.post("/api/missing").status_code == 404
        assert client.get("/.env").status_code == 404


def test_missing_build_is_unavailable(tmp_path):
    app = create_app(
        Settings(_env_file=None, env="test", database_url=None, migration_database_url=None),
        tmp_path,
    )
    with TestClient(app) as client:
        assert client.get("/").status_code == 503
        assert client.get("/health/live").status_code == 200


def test_only_sql_server_is_accepted():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url=SecretStr("sqlite:///test.db"))
