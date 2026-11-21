from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from app.core.web import (
    check_request_shape,
    checked_value,
    fail,
    parse_page_size,
    require_json_mutation,
)
from app.modules.devices.schemas import identity

router = APIRouter(prefix="/api/v1", tags=["alerts"])

_LIST_QUERY_KEYS = {
    "farm_id",
    "node_id",
    "view",
    "kind",
    "lifecycle",
    "acknowledged",
    "recovery_proven",
    "from",
    "to",
    "page_size",
    "cursor",
}
_DETAIL_QUERY_KEYS = {
    "audit_kind",
    "audit_cursor",
    "audit_page_size",
    "acknowledgement_version",
}


def _parse_audit_page_size(request: Request) -> int:
    raw = request.query_params.get("audit_page_size", "50")
    if not raw.isascii() or not raw.isdecimal() or not 1 <= int(raw) <= 200:
        fail(400, "INPUT_INVALID", "audit_page_size không hợp lệ")
    return int(raw)


@router.get("/alerts")
def list_alerts(request: Request):
    check_request_shape(request, _LIST_QUERY_KEYS)
    raw_farm = request.query_params.get("farm_id")
    if raw_farm is None:
        fail(400, "INPUT_INVALID", "Thiếu farm_id bắt buộc")
    farm_id = checked_value(identity, raw_farm)
    raw_node = request.query_params.get("node_id")
    node_id = checked_value(identity, raw_node) if raw_node is not None else None
    page_size = parse_page_size(request, default=50, maximum=200)
    return request.app.state.alerts.list_alerts(
        request.cookies.get("hq_session"),
        farm_id=farm_id,
        node_id=node_id,
        view=request.query_params.get("view", "active"),
        kind=request.query_params.get("kind"),
        lifecycle=request.query_params.get("lifecycle"),
        acknowledged_raw=request.query_params.get("acknowledged"),
        recovery_proven_raw=request.query_params.get("recovery_proven"),
        from_raw=request.query_params.get("from"),
        to_raw=request.query_params.get("to"),
        page_size=page_size,
        cursor=request.query_params.get("cursor"),
    )


@router.get("/alerts/{id}")
def get_alert_detail(id: str, request: Request):
    check_request_shape(request, _DETAIL_QUERY_KEYS)
    episode_id = checked_value(identity, id)
    audit_page_size = _parse_audit_page_size(request)
    return request.app.state.alerts.get_alert_detail(
        request.cookies.get("hq_session"),
        episode_id=episode_id,
        audit_kind=request.query_params.get("audit_kind"),
        audit_cursor=request.query_params.get("audit_cursor"),
        audit_page_size=audit_page_size,
        acknowledgement_version_raw=request.query_params.get("acknowledgement_version"),
    )


@router.post("/alerts/{id}/acknowledgements")
async def create_alert_acknowledgement(id: str, request: Request):
    require_json_mutation(request)
    episode_id = checked_value(identity, id)
    raw_body = await request.body()
    status_code, payload = await run_in_threadpool(
        request.app.state.alerts.create_acknowledgement,
        request.cookies.get("hq_session"),
        request.headers.get("x-csrf-token"),
        episode_id=episode_id,
        raw_body=raw_body,
    )
    return JSONResponse(status_code=status_code, content=payload)
