import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import BACKEND_DIR, Settings
from app.core.db import Database
from app.core.errors import (
    WebError,
    http_error,
    problem,
    unexpected_error,
    validation_error,
    web_error,
)
from app.modules.auth.router import router as auth_router
from app.modules.auth.service import AuthService
from app.modules.devices.router import router as devices_router
from app.modules.devices.service import DeviceService

logger = logging.getLogger("uvicorn.error.hqtcsdl")


def create_app(settings: Settings | None = None, frontend_dir: Path | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings()
    database = Database(settings)
    static_dir = (frontend_dir or BACKEND_DIR.parent / "frontend" / "dist").resolve()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        database.dispose()

    app = FastAPI(title="HQTCSDL", version="0.1.0", lifespan=lifespan)
    app.state.database = database
    app.state.settings = settings
    app.state.auth = AuthService(database, settings)
    app.state.devices = DeviceService(database, settings)
    app.add_exception_handler(WebError, web_error)
    app.add_exception_handler(StarletteHTTPException, http_error)
    app.add_exception_handler(RequestValidationError, validation_error)
    app.add_exception_handler(Exception, unexpected_error)

    @app.middleware("http")
    async def request_log(request: Request, call_next):
        request.state.request_id = uuid4().hex
        started = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            # Do not log exception strings: drivers may include credentials.
            logger.error("request_failed request_id=%s", request.state.request_id)
            response = problem(request, 500, "Internal Server Error")
        response.headers["X-Request-ID"] = request.state.request_id
        if request.url.path.startswith("/api/v1/"):
            response.headers["Cache-Control"] = "no-store"
        logger.info(
            "request_id=%s method=%s status=%s duration_ms=%.1f",
            request.state.request_id,
            request.method,
            response.status_code,
            (time.monotonic() - started) * 1000,
        )
        return response

    @app.get("/health/live")
    def live():
        return {"status": "alive", "scope": "foundation", "monitoring": "not_implemented"}

    @app.get("/health/ready")
    def ready():
        try:
            database.probe()
        except Exception:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "not_ready",
                    "checks": {"database": "unavailable"},
                    "scope": "foundation",
                    "monitoring": "not_implemented",
                },
            )
        return {
            "status": "ready",
            "checks": {"database": "ok"},
            "scope": "foundation",
            "monitoring": "not_implemented",
        }

    app.include_router(auth_router)
    app.include_router(devices_router)

    @app.api_route(
        "/{path:path}",
        methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        include_in_schema=False,
    )
    def frontend(path: str, request: Request):
        # API and health paths never fall through to index.html.
        if path.split("/", 1)[0] in {"api", "health", "docs", "redoc", "openapi.json"}:
            raise HTTPException(404)
        if request.method not in {"GET", "HEAD"}:
            raise HTTPException(405, headers={"Allow": "GET, HEAD"})
        target = (static_dir / path).resolve()
        if not target.is_relative_to(static_dir) or any(
            p.startswith(".") for p in Path(path).parts
        ):
            raise HTTPException(404)
        if path and target.is_file():
            return FileResponse(target)
        if path.startswith("assets/") or Path(path).suffix:
            raise HTTPException(404)
        index = static_dir / "index.html"
        if not index.is_file():
            raise HTTPException(503)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    return app
