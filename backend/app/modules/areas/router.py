from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.errors import WebError
from app.core.web import (
    check_request_shape,
    checked_value,
    parse_page_size,
    require_json_mutation,
)
from app.modules.areas.schemas import AreaCreate, AreaLifecycleAction, AreaPatch, area_code
from app.modules.devices.schemas import identity

router = APIRouter(prefix="/api/v1", tags=["areas"])

_VALID_STATUSES = {"ACTIVE", "ARCHIVED", "ALL"}


def _parse_status(request: Request) -> str:
    status = request.query_params.get("status", "ACTIVE")
    if status not in _VALID_STATUSES:
        raise WebError(400, "INPUT_INVALID", "status không hợp lệ")
    return status


@router.get("/farms")
def list_farms(request: Request):
    check_request_shape(request, {"status", "code", "page_size", "cursor"})
    status = _parse_status(request)
    page_size = parse_page_size(request, default=20, maximum=100)
    code = request.query_params.get("code")
    if code is not None:
        checked_value(area_code, code)
    return request.app.state.areas.list_farms(
        request.cookies.get("hq_session"),
        status=status,
        code=code,
        page_size=page_size,
        cursor=request.query_params.get("cursor"),
    )


@router.post("/farms")
def create_farm(body: AreaCreate, request: Request):
    require_json_mutation(request)
    result = request.app.state.areas.create_farm(
        request.cookies.get("hq_session"), request.headers.get("x-csrf-token"), body
    )
    return JSONResponse(
        result, status_code=201, headers={"Location": f"/api/v1/farms/{result['id']}"}
    )


@router.get("/farms/{id}")
def get_farm(id: str, request: Request):
    check_request_shape(request, set())
    return request.app.state.areas.get_farm(
        request.cookies.get("hq_session"), checked_value(identity, id)
    )


@router.patch("/farms/{id}")
def patch_farm(id: str, body: AreaPatch, request: Request):
    require_json_mutation(request)
    return request.app.state.areas.patch_farm(
        request.cookies.get("hq_session"),
        request.headers.get("x-csrf-token"),
        checked_value(identity, id),
        body,
    )


@router.post("/farms/{id}/archive")
def archive_farm(id: str, body: AreaLifecycleAction, request: Request):
    require_json_mutation(request)
    return request.app.state.areas.archive_farm(
        request.cookies.get("hq_session"),
        request.headers.get("x-csrf-token"),
        checked_value(identity, id),
        body,
    )


@router.post("/farms/{id}/restore")
def restore_farm(id: str, body: AreaLifecycleAction, request: Request):
    require_json_mutation(request)
    return request.app.state.areas.restore_farm(
        request.cookies.get("hq_session"),
        request.headers.get("x-csrf-token"),
        checked_value(identity, id),
        body,
    )


@router.get("/farms/{id}/zones")
def list_zones(id: str, request: Request):
    check_request_shape(request, {"status", "code", "page_size", "cursor"})
    farm_id = checked_value(identity, id)
    status = _parse_status(request)
    page_size = parse_page_size(request, default=20, maximum=100)
    code = request.query_params.get("code")
    if code is not None:
        checked_value(area_code, code)
    return request.app.state.areas.list_zones(
        request.cookies.get("hq_session"),
        farm_id=farm_id,
        status=status,
        code=code,
        page_size=page_size,
        cursor=request.query_params.get("cursor"),
    )


@router.post("/farms/{id}/zones")
def create_zone(id: str, body: AreaCreate, request: Request):
    require_json_mutation(request)
    farm_id = checked_value(identity, id)
    result = request.app.state.areas.create_zone(
        request.cookies.get("hq_session"), request.headers.get("x-csrf-token"), farm_id, body
    )
    return JSONResponse(
        result, status_code=201, headers={"Location": f"/api/v1/zones/{result['id']}"}
    )


@router.get("/zones/{id}")
def get_zone(id: str, request: Request):
    check_request_shape(request, set())
    return request.app.state.areas.get_zone(
        request.cookies.get("hq_session"), checked_value(identity, id)
    )


@router.patch("/zones/{id}")
def patch_zone(id: str, body: AreaPatch, request: Request):
    require_json_mutation(request)
    return request.app.state.areas.patch_zone(
        request.cookies.get("hq_session"),
        request.headers.get("x-csrf-token"),
        checked_value(identity, id),
        body,
    )


@router.post("/zones/{id}/archive")
def archive_zone(id: str, body: AreaLifecycleAction, request: Request):
    require_json_mutation(request)
    return request.app.state.areas.archive_zone(
        request.cookies.get("hq_session"),
        request.headers.get("x-csrf-token"),
        checked_value(identity, id),
        body,
    )


@router.post("/zones/{id}/restore")
def restore_zone(id: str, body: AreaLifecycleAction, request: Request):
    require_json_mutation(request)
    return request.app.state.areas.restore_zone(
        request.cookies.get("hq_session"),
        request.headers.get("x-csrf-token"),
        checked_value(identity, id),
        body,
    )
