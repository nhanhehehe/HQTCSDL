from fastapi import APIRouter, Request

from app.core.errors import IngestionError
from app.modules.ingestion.validator import parse_ingestion_json

router = APIRouter(prefix="/api/v1/ingestion", tags=["ingestion"])


@router.post("/measurements")
async def ingest_measurements(request: Request):
    if request.query_params:
        raise IngestionError(
            400,
            "INVALID_REQUEST",
            "Query parameters are not allowed on ingestion endpoint",
            retryable=False,
        )
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type != "application/json":
        raise IngestionError(
            400,
            "INVALID_REQUEST",
            "Content-Type must be application/json",
            retryable=False,
        )

    raw_body = await request.body()
    max_bytes = request.app.state.settings.ingestion_max_body_bytes
    if len(raw_body) > max_bytes:
        raise IngestionError(
            413,
            "PAYLOAD_TOO_LARGE",
            f"Request body exceeds maximum allowed size of {max_bytes} bytes",
            retryable=False,
        )

    payload = parse_ingestion_json(raw_body)
    api_key = request.headers.get("x-api-key")
    return request.app.state.ingestion.ingest(api_key=api_key, payload=payload)
