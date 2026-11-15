from fastapi import Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException


def is_web_api(request: Request) -> bool:
    path = request.url.path
    return (path == "/api/v1" or path.startswith("/api/v1/")) and not (
        path == "/api/v1/ingestion" or path.startswith("/api/v1/ingestion/")
    )


class WebError(Exception):
    """Only pass public messages/details, never driver errors or submitted secrets."""

    def __init__(self, status: int, code: str, message: str, *, details: dict | None = None):
        self.status = status
        self.code = code
        self.message = message
        self.details = details


def web_problem(
    request: Request, status: int, code: str, message: str, details: dict | None = None
) -> JSONResponse:
    error = {"code": code, "message": message, "retryable": status == 503}
    if details is not None:
        error["details"] = details
    return JSONResponse(
        status_code=status,
        content={"error": error, "request_id": getattr(request.state, "request_id", None)},
        headers={"Cache-Control": "no-store"},
    )


def problem(request: Request, status: int, title: str) -> JSONResponse:
    if is_web_api(request):
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
        return web_problem(request, status, codes.get(status, "REQUEST_FAILED"), title)
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


async def web_error(request: Request, exc: WebError) -> JSONResponse:
    if not is_web_api(request):
        return problem(request, 500, "Internal Server Error")
    return web_problem(request, exc.status, exc.code, exc.message, exc.details)


async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    if is_web_api(request):
        # Do not echo exc.body/errors(): validation inputs can contain credentials.
        return web_problem(request, 400, "INPUT_INVALID", "Dữ liệu đầu vào không hợp lệ")
    return await request_validation_exception_handler(request, exc)


async def storage_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    if is_web_api(request):
        return web_problem(request, 503, "STORAGE_UNAVAILABLE", "Kho dữ liệu tạm thời lỗi")
    return await unexpected_error(request, exc)
