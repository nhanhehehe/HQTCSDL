from fastapi import Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException


def problem(request: Request, status: int, title: str) -> JSONResponse:
    path = request.url.path

    is_web = path == "/api/v1" or path.startswith("/api/v1/")
    is_ingestion = path == "/api/v1/ingestion" or path.startswith("/api/v1/ingestion/")

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

        return JSONResponse(
            status_code=status,
            content={
                "error": {
                    "code": codes.get(status, "REQUEST_FAILED"),
                    "message": title,
                    "retryable": status == 503,
                },
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

    if is_web and not is_ingestion:
        return problem(
            request,
            400,
            "Dữ liệu đầu vào không hợp lệ",
        )

    return await request_validation_exception_handler(request, exc)
