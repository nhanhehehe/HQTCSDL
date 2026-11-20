from fastapi import APIRouter, Request

from app.core.errors import WebError
from app.core.web import check_request_shape, checked_value, parse_page_size
from app.modules.devices.schemas import identity

router = APIRouter(prefix="/api/v1", tags=["measurements"])

_VALID_TIME_MODES = {"known", "unknown"}


@router.get("/dashboard")
def get_dashboard(request: Request):
    check_request_shape(request, {"farm_id", "node_id"})
    raw_farm_id = request.query_params.get("farm_id")
    if raw_farm_id is None:
        raise WebError(400, "INPUT_INVALID", "Thiếu farm_id")
    farm_id = checked_value(identity, raw_farm_id)
    raw_node_id = request.query_params.get("node_id")
    node_id = checked_value(identity, raw_node_id) if raw_node_id is not None else None
    return request.app.state.dashboard.get_dashboard(
        request.cookies.get("hq_session"),
        farm_id=farm_id,
        node_id=node_id,
    )


@router.get("/nodes/{id}/measurements/current")
def get_current_measurement(id: str, request: Request):
    check_request_shape(request, set())
    node_id = checked_value(identity, id)
    return request.app.state.measurements.get_current(
        request.cookies.get("hq_session"), node_id=node_id
    )


@router.get("/nodes/{id}/measurements")
def list_node_measurements(id: str, request: Request):
    check_request_shape(request, {"time_mode", "from", "to", "page_size", "cursor", "view_token"})
    node_id = checked_value(identity, id)
    time_mode = request.query_params.get("time_mode", "known")
    if time_mode not in _VALID_TIME_MODES:
        raise WebError(400, "INPUT_INVALID", "time_mode không hợp lệ")
    page_size = parse_page_size(request, default=50, maximum=200)
    return request.app.state.measurements.list_measurements(
        request.cookies.get("hq_session"),
        node_id=node_id,
        time_mode=time_mode,
        from_raw=request.query_params.get("from"),
        to_raw=request.query_params.get("to"),
        page_size=page_size,
        cursor=request.query_params.get("cursor"),
        view_token=request.query_params.get("view_token"),
    )


@router.get("/nodes/{id}/measurements/chart")
def get_node_measurements_chart(id: str, request: Request):
    check_request_shape(request, {"from", "to", "page_size", "cursor", "view_token"})
    node_id = checked_value(identity, id)
    page_size = parse_page_size(request, default=50, maximum=200)
    return request.app.state.measurements.get_chart(
        request.cookies.get("hq_session"),
        node_id=node_id,
        from_raw=request.query_params.get("from"),
        to_raw=request.query_params.get("to"),
        page_size=page_size,
        cursor=request.query_params.get("cursor"),
        view_token=request.query_params.get("view_token"),
    )
