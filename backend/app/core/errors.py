from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException


def problem(request: Request, status: int, title: str) -> JSONResponse:
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
