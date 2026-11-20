from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.errors import WebError
from app.core.web import parse_page_size, require_json_mutation
from app.modules.devices.schemas import (
    EmptyBody,
    GatewayCreate,
    NodeCreate,
    ProfilePatch,
    code,
    identity,
)

router = APIRouter(prefix="/api/v1", tags=["devices"])


def shape(request, allowed, body=False):
    keys = list(request.query_params.keys())
    if any(k not in allowed or len(request.query_params.getlist(k)) != 1 for k in keys):
        raise WebError(400, "INPUT_INVALID", "Tham số không hợp lệ")
    if not body and (
        request.headers.get("content-length", "0") != "0"
        or request.headers.get("transfer-encoding")
    ):
        raise WebError(400, "INPUT_INVALID", "Không nhận request body")


def checked(function, value):
    try:
        return function(value)
    except (ValueError, TypeError):
        raise WebError(400, "INPUT_INVALID", "Dữ liệu không hợp lệ") from None


@router.get("/context")
def context(request: Request):
    shape(request, {"farm_id"})
    raw_farm_id = request.query_params.get("farm_id")
    farm_id = checked(identity, raw_farm_id) if raw_farm_id is not None else None
    return request.app.state.devices.context(request.cookies.get("hq_session"), farm_id=farm_id)


def register(kind):
    create_schema = NodeCreate if kind == "nodes" else GatewayCreate

    def listing(request: Request):
        filter_key = "device_id" if kind == "nodes" else "gateway_id"
        shape(request, {"page_size", "cursor", filter_key})
        raw = request.query_params.get("page_size", "50")
        if not raw.isascii() or not raw.isdecimal() or not 1 <= int(raw) <= 200:
            raise WebError(400, "INPUT_INVALID", "page_size không hợp lệ")
        exact = request.query_params.get(filter_key)
        if exact is not None:
            checked(code, exact)
        return request.app.state.devices.listing(
            request.cookies.get("hq_session"),
            kind,
            int(raw),
            exact,
            request.query_params.get("cursor"),
        )

    def detail(id: str, request: Request):
        shape(request, set())
        return request.app.state.devices.detail(
            request.cookies.get("hq_session"), kind, checked(identity, id)
        )

    def create(body, request: Request):
        require_json_mutation(request)
        result = request.app.state.devices.create(
            request.cookies.get("hq_session"), request.headers.get("x-csrf-token"), kind, body
        )
        return JSONResponse(
            result, status_code=201, headers={"Location": f"/api/v1/{kind}/{result['id']}"}
        )

    create.__annotations__["body"] = create_schema

    def patch(id: str, body: ProfilePatch, request: Request):
        require_json_mutation(request)
        return request.app.state.devices.patch(
            request.cookies.get("hq_session"),
            request.headers.get("x-csrf-token"),
            kind,
            checked(identity, id),
            body,
        )

    router.add_api_route("/" + kind, listing, methods=["GET"])
    router.add_api_route("/" + kind, create, methods=["POST"])
    router.add_api_route("/" + kind + "/{id}", detail, methods=["GET"])
    router.add_api_route("/" + kind + "/{id}", patch, methods=["PATCH"])


register("nodes")
register("gateways")


@router.get("/gateways/{id}/credentials")
def list_credentials(id: str, request: Request):
    shape(request, {"page_size", "cursor"})
    gateway_id = checked(identity, id)
    page_size = parse_page_size(request, default=50, maximum=200)
    return request.app.state.devices.list_credentials(
        request.cookies.get("hq_session"),
        gateway_id=gateway_id,
        page_size=page_size,
        cursor=request.query_params.get("cursor"),
    )


@router.get("/gateways/{id}/credentials/{credential_id}")
def get_credential(id: str, credential_id: str, request: Request):
    shape(request, set())
    return request.app.state.devices.get_credential(
        request.cookies.get("hq_session"),
        gateway_id=checked(identity, id),
        credential_id=checked(identity, credential_id),
    )


@router.post("/gateways/{id}/credentials")
def issue_credential(id: str, body: EmptyBody, request: Request):
    require_json_mutation(request)
    gateway_id = checked(identity, id)
    result = request.app.state.devices.issue_credential(
        request.cookies.get("hq_session"),
        request.headers.get("x-csrf-token"),
        gateway_id=gateway_id,
    )
    return JSONResponse(
        result,
        status_code=201,
        headers={
            "Location": f"/api/v1/gateways/{gateway_id}/credentials/{result['credential']['id']}"
        },
    )


@router.post("/gateways/{id}/credentials/{credential_id}/revoke")
def revoke_credential(id: str, credential_id: str, body: EmptyBody, request: Request):
    require_json_mutation(request)
    return request.app.state.devices.revoke_credential(
        request.cookies.get("hq_session"),
        request.headers.get("x-csrf-token"),
        gateway_id=checked(identity, id),
        credential_id=checked(identity, credential_id),
    )


@router.get("/metrics")
def list_metrics(request: Request):
    shape(request, set())
    return request.app.state.devices.list_metrics(request.cookies.get("hq_session"))
