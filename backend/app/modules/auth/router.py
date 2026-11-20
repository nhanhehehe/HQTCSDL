import time

from fastapi import APIRouter, Depends, Request, Response

from app.core.errors import WebError
from app.modules.auth.schemas import LoginRequest, SessionResponse

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def origin_guard(request: Request):
    if request.headers.get("origin") != request.app.state.settings.web_origin:
        raise WebError(403, "CSRF_INVALID", "Origin không hợp lệ")


def login_guard(request: Request):
    origin_guard(request)
    if request.headers.get("x-hq-client") != "web":
        raise WebError(403, "CSRF_INVALID", "Client không hợp lệ")
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        raise WebError(400, "INPUT_INVALID", "Yêu cầu phải là JSON")


@router.post("/login", response_model=SessionResponse, dependencies=[Depends(login_guard)])
def login(body: LoginRequest, request: Request, response: Response):
    started = time.monotonic()
    dto, cookie, remaining = request.app.state.auth.login(
        body.username, body.password, request.cookies.get("hq_session")
    )
    response.set_cookie(
        "hq_session",
        cookie,
        max_age=max(0, remaining - int(time.monotonic() - started) - 1),
        path="/api",
        httponly=True,
        samesite="strict",
        secure=request.app.state.settings.cookie_secure,
    )
    return dto


@router.get("/session", response_model=SessionResponse)
def session(request: Request):
    return request.app.state.auth.session(request.cookies.get("hq_session"))


@router.post("/logout", status_code=204, dependencies=[Depends(origin_guard)])
def logout(request: Request):
    request.app.state.auth.logout(
        request.cookies.get("hq_session"), request.headers.get("x-csrf-token")
    )
    response = Response(status_code=204)
    response.delete_cookie(
        "hq_session",
        path="/api",
        httponly=True,
        samesite="strict",
        secure=request.app.state.settings.cookie_secure,
    )
    return response
