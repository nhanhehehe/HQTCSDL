from typing import Any

from fastapi import Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException


class WebError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ):
        self.status = status
        self.code = code
        self.message = message
        self.details = details


class IngestionError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        retryable: bool | None = None,
        details: list[dict[str, str]] | None = None,
        headers: dict[str, str] | None = None,
    ):
        self.status = status
        self.code = code
        self.message = message
        self.retryable = status in {429, 500, 503} if retryable is None else retryable
        self.details = details
        self.headers = headers


def ingestion_error_response(
    status: int,
    code: str,
    message: str,
    *,
    retryable: bool | None = None,
    details: list[dict[str, str]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    error_body: dict = {"code": code, "message": message}
    if details:
        error_body["details"] = details
    response_headers = {"Cache-Control": "no-store"}
    if headers:
        response_headers.update(headers)
    return JSONResponse(
        status_code=status,
        content={
            "error": error_body,
            "retryable": status in {429, 500, 503} if retryable is None else retryable,
        },
        headers=response_headers,
    )


async def web_error(request: Request, exc: WebError) -> JSONResponse:
    return problem(request, exc.status, exc.message, exc.code, details=exc.details)


async def ingestion_error(request: Request, exc: IngestionError) -> JSONResponse:
    return ingestion_error_response(
        exc.status,
        exc.code,
        exc.message,
        retryable=exc.retryable,
        details=exc.details,
        headers=exc.headers,
    )


def problem(
    request: Request,
    status: int,
    title: str,
    code: str | None = None,
    *,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    path = request.url.path

    is_web = path == "/api/v1" or path.startswith("/api/v1/")
    is_ingestion = path == "/api/v1/ingestion" or path.startswith("/api/v1/ingestion/")

    if is_ingestion:
        ingestion_codes = {
            400: "INVALID_REQUEST",
            401: "AUTHENTICATION_FAILED",
            403: "GATEWAY_FORBIDDEN",
            404: "INVALID_REQUEST",
            405: "INVALID_REQUEST",
            413: "PAYLOAD_TOO_LARGE",
            429: "RATE_LIMITED",
            500: "INTERNAL_ERROR",
            503: "SERVICE_UNAVAILABLE",
        }
        return ingestion_error_response(
            status,
            code or ingestion_codes.get(status, "INTERNAL_ERROR"),
            title,
        )

    if is_web and not is_ingestion:
        codes = {
            400: "INPUT_INVALID",
            401: "SESSION_INVALID",
            403: "CSRF_INVALID",
            404: "NOT_FOUND",
            405: "METHOD_NOT_ALLOWED",
            409: "VERSION_CONFLICT",
            500: "INTERNAL_ERROR",
            503: "STORAGE_UNAVAILABLE",
        }

        error_body: dict[str, Any] = {
            "code": code or codes.get(status, "REQUEST_FAILED"),
            "message": title,
            "retryable": status == 503,
        }
        if details is not None:
            error_body["details"] = details

        return JSONResponse(
            status_code=status,
            content={
                "error": error_body,
                "request_id": getattr(request.state, "request_id", None),
            },
            headers={"Cache-Control": "no-store"},
        )

    return JSONResponse(
        status_code=status,
        media_type="application/problem+json",
        content={
            "type": "about:blank",
            "title": title,
            "status": status,
            "request_id": getattr(request.state, "request_id", None),
        },
    )


async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
    titles = {404: "Not Found", 405: "Method Not Allowed", 503: "Service Unavailable"}
    response = problem(request, exc.status_code, titles.get(exc.status_code, "Request failed"))
    if exc.headers:
        response.headers.update(exc.headers)
    return response


async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    # Never return exception/connection strings, SQL parameters or credentials.
    return problem(request, 500, "Internal Server Error")


async def validation_error(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    path = request.url.path

    is_web = path == "/api/v1" or path.startswith("/api/v1/")
    is_ingestion = path == "/api/v1/ingestion" or path.startswith("/api/v1/ingestion/")

    if is_ingestion:
        return ingestion_error_response(400, "INVALID_REQUEST", "Invalid ingestion request")

    if is_web and not is_ingestion:
        fields = sorted(
            {str(e["loc"][1]) for e in exc.errors() if len(e["loc"]) > 1} & {"username", "password"}
        )
        return problem(
            request,
            400,
            "Dữ liệu đầu vào không hợp lệ" + (": " + ", ".join(fields) if fields else ""),
        )

    return await request_validation_exception_handler(request, exc)
