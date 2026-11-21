from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool

from app.core.web import check_request_shape, checked_value, require_json_mutation
from app.modules.devices.schemas import identity

router = APIRouter(prefix="/api/v1", tags=["monitoring_config"])


@router.get("/nodes/{id}/monitoring")
def get_node_monitoring(id: str, request: Request):
    check_request_shape(request, set())
    node_id = checked_value(identity, id)
    return request.app.state.monitoring_config.get_monitoring(
        request.cookies.get("hq_session"),
        node_id=node_id,
    )


@router.patch("/nodes/{id}/monitoring")
async def patch_node_monitoring(id: str, request: Request):
    require_json_mutation(request)
    node_id = checked_value(identity, id)
    raw_body = await request.body()
    return await run_in_threadpool(
        request.app.state.monitoring_config.patch_monitoring,
        request.cookies.get("hq_session"),
        request.headers.get("x-csrf-token"),
        node_id=node_id,
        raw_body=raw_body,
    )


@router.patch("/nodes/{id}/receiving")
async def patch_node_receiving(id: str, request: Request):
    require_json_mutation(request)
    node_id = checked_value(identity, id)
    raw_body = await request.body()
    return await run_in_threadpool(
        request.app.state.monitoring_config.patch_node_receiving,
        request.cookies.get("hq_session"),
        request.headers.get("x-csrf-token"),
        node_id=node_id,
        raw_body=raw_body,
    )


@router.patch("/gateways/{id}/receiving")
async def patch_gateway_receiving(id: str, request: Request):
    require_json_mutation(request)
    gateway_id = checked_value(identity, id)
    raw_body = await request.body()
    return await run_in_threadpool(
        request.app.state.monitoring_config.patch_gateway_receiving,
        request.cookies.get("hq_session"),
        request.headers.get("x-csrf-token"),
        gateway_id=gateway_id,
        raw_body=raw_body,
    )
