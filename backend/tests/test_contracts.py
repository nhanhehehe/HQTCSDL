import json
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi import APIRouter, HTTPException, Response
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError
from sqlalchemy.exc import OperationalError

from app.core.clock import sql_utc, utc_now
from app.core.config import Settings
from app.core.contracts import (
    BackendTime,
    BigIntText,
    DecimalText,
    InternalId,
    PageSize,
    RangeTime,
    RowVersion,
    WebModel,
    page_size_query,
    web_integer,
)
from app.core.errors import WebError
from app.main import create_app


class ExampleRequest(WebModel):
    id: InternalId
    enabled: bool
    count: int
    expected_row_version: RowVersion
    notes: str | None = None


class ExampleResponse(WebModel):
    id: InternalId
    value: DecimalText
    row_version: RowVersion
    at: BackendTime


VALID = {
    "id": "9223372036854775807",
    "enabled": True,
    "count": 1,
    "expected_row_version": "AAAAAAAAAAA=",
}


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", 1),
        ("id", True),
        ("id", "01"),
        ("id", "0"),
        ("id", "9223372036854775808"),
        ("id", "١"),
        ("id", "1e2"),
        ("count", True),
        ("count", "1"),
        ("count", 1.0),
        ("enabled", 1),
        ("enabled", "true"),
        ("unexpected", "SECRET"),
        ("notes", 1),
        ("expected_row_version", None),
    ],
)
def test_request_rejects_coercion_and_unknown_fields(field, value):
    with pytest.raises(ValidationError):
        ExampleRequest.model_validate_json(json.dumps(VALID | {field: value}))


def test_patch_presence_is_distinct_from_explicit_null():
    missing = ExampleRequest.model_validate(VALID)
    cleared = ExampleRequest.model_validate(VALID | {"notes": None})
    assert "notes" not in missing.model_fields_set
    assert cleared.model_dump(exclude_unset=True)["notes"] is None


def test_lossless_json_response_and_openapi_types():
    response = ExampleResponse(
        id=web_integer(2**63 - 1),
        value=Decimal("9999999999999999.99"),
        row_version=b"\xff" * 8,
        at=datetime(2026, 11, 15, 6, 0, 0, 123000, UTC),
    )
    assert json.loads(response.model_dump_json()) == {
        "id": "9223372036854775807",
        "value": "9999999999999999.99",
        "row_version": "//////////8=",
        "at": "2026-11-15T06:00:00.123Z",
    }
    schema = ExampleResponse.model_json_schema(mode="serialization")["properties"]
    assert all(schema[key]["type"] == "string" for key in schema)
    assert TypeAdapter(BigIntText).validate_python("0") == "0"


@pytest.mark.parametrize("value", [True, 1.0, -1, 2**63])
def test_sql_bigint_conversion_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        web_integer(value)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "AAAAAAAAAAA",
        "AAAAAAAAAAAA",
        "AAAAAAAAAAB=",
        "AAAAAAAAAAA=\n",
        "//////////8_",
        123,
        b"1234567",
        b"123456789",
    ],
)
def test_rowversion_rejects_wrong_size_encoding_and_noncanonical_pad_bits(value):
    with pytest.raises(ValidationError):
        TypeAdapter(RowVersion).validate_python(value)


@pytest.mark.parametrize("value", [True, 1, 0.1, "NaN", "Infinity", "1e3", Decimal("NaN")])
def test_decimal_does_not_accept_float_or_nonfinite_values(value):
    with pytest.raises(ValidationError):
        TypeAdapter(DecimalText).validate_python(value)


def test_decimal_preserves_precision_without_rounding():
    adapter = TypeAdapter(DecimalText)
    assert adapter.dump_json(adapter.validate_python("28.500")) == b'"28.500"'
    assert adapter.dump_json(adapter.validate_python(Decimal("0.0001"))) == b'"0.0001"'


def test_time_normalization_and_clock_precision():
    value = TypeAdapter(RangeTime).validate_python("2026-11-15T13:00:00.123456+07:00")
    assert value == datetime(2026, 11, 15, 6, 0, 0, 123456, UTC)
    assert sql_utc(datetime(2026, 11, 15)) == datetime(2026, 11, 15, tzinfo=UTC)
    assert sql_utc(datetime(2026, 11, 15, 7, tzinfo=timezone(timedelta(hours=7)))).hour == 0
    now = utc_now()
    assert now.utcoffset() == timedelta(0)
    assert now.microsecond % 1000 == 0


@pytest.mark.parametrize(
    "value",
    [
        "2026-11-15T13:00:00",
        "2026-11-15",
        1794722400,
        True,
        "2026-11-15 13:00:00Z",
        "2026-02-30T00:00:00Z",
        datetime(2026, 11, 15),
        "0001-01-01T00:00:00+07:00",
        "9999-12-31T23:59:59-07:00",
    ],
)
def test_time_requires_rfc3339_and_explicit_timezone(value):
    with pytest.raises(ValidationError):
        TypeAdapter(RangeTime).validate_python(value)


@pytest.mark.parametrize("value", [0, 201, True, "50", 1.0])
def test_json_page_size_is_strict(value):
    with pytest.raises(ValidationError):
        TypeAdapter(PageSize).validate_python(value)


@pytest.mark.parametrize("value", ["0", "201", "true", "1.5", " 1", "١", "1e2"])
def test_query_page_size_rejects_invalid_text(value):
    with pytest.raises(ValueError):
        page_size_query(value)


def test_page_size_defaults_and_boundaries():
    assert page_size_query() == 50
    assert page_size_query("1") == 1
    assert page_size_query("200") == 200


@pytest.fixture
def client(tmp_path):
    app = create_app(
        Settings(_env_file=None, env="test", database_url=None, migration_database_url=None),
        tmp_path,
    )
    router = APIRouter()

    @router.post("/api/v1/contract")
    def echo(body: ExampleRequest):
        return body

    @router.get("/api/v1/fault/{kind}")
    def fault(kind: str):
        if kind == "storage":
            raise OperationalError("SQL_SECRET", {}, Exception("PASSWORD_SECRET"))
        if kind == "version":
            raise WebError(409, "VERSION_CONFLICT", "Đọc lại dữ liệu")
        if kind == "http":
            raise HTTPException(405, detail="PASSWORD_SECRET", headers={"Allow": "POST"})
        raise RuntimeError("PASSWORD_SECRET")

    @router.post("/api/v1/ingestion/measurements")
    def ingestion_contract_boundary():
        # Test-only stand-in: actual ingestion owns parsing/validation and ACK2.
        return Response('{"results":[]}', media_type="application/json")

    @router.post("/api/v1/logout", status_code=204)
    def logout_body_boundary():
        return Response(status_code=204)

    app.router.routes[0:0] = router.routes
    with TestClient(app, raise_server_exceptions=False) as result:
        yield result


def test_validation_returns_safe_400_and_backend_request_id(client):
    response = client.post(
        "/api/v1/contract",
        json=VALID | {"password": "PASSWORD_SECRET"},
        headers={"X-Request-ID": "untrusted"},
    )
    assert response.status_code == 400
    assert response.json()["error"] == {
        "code": "INPUT_INVALID",
        "message": "Dữ liệu đầu vào không hợp lệ",
        "retryable": False,
    }
    assert response.json()["request_id"] == response.headers["X-Request-ID"]
    assert response.json()["request_id"] != "untrusted"
    assert "PASSWORD_SECRET" not in response.text
    assert response.headers["Cache-Control"] == "no-store"


def test_malformed_json_is_web_400(client):
    response = client.post(
        "/api/v1/contract", content='{"id":', headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INPUT_INVALID"


@pytest.mark.parametrize(
    "kind,status,code,retryable",
    [
        ("storage", 503, "STORAGE_UNAVAILABLE", True),
        ("version", 409, "VERSION_CONFLICT", False),
        ("http", 405, "METHOD_NOT_ALLOWED", False),
        ("unexpected", 500, "INTERNAL_ERROR", False),
    ],
)
def test_errors_are_safe_and_consistent(client, kind, status, code, retryable):
    response = client.get(f"/api/v1/fault/{kind}")
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert response.json()["error"]["retryable"] is retryable
    assert response.json()["request_id"] == response.headers["X-Request-ID"]
    assert response.headers["Cache-Control"] == "no-store"
    assert "SECRET" not in response.text
    if kind == "http":
        assert response.headers["Allow"] == "POST"


def test_success_no_store_and_ingestion_boundary(client):
    response = client.post("/api/v1/contract", json=VALID)
    assert response.status_code == 200
    assert response.json()["id"] == VALID["id"]
    assert response.headers["Cache-Control"] == "no-store"
    ingestion = client.post("/api/v1/ingestion/measurements")
    assert ingestion.json() == {"results": []}
    assert "request_id" not in ingestion.json()
    logout = client.post("/api/v1/logout")
    assert logout.status_code == 204
    assert logout.content == b""
