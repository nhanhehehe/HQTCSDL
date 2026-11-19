from fastapi import APIRouter, Request, Response

from app.core.web import require_json_mutation
from app.modules.reports.schemas import ReportCreateRequest, ReportCsvRequest

router = APIRouter(prefix="/api/v1/reports", tags=["reports"])


@router.post("/measurements")
def create_measurement_report(body: ReportCreateRequest, request: Request):
    require_json_mutation(request)
    return request.app.state.reports.create_report(
        request.cookies.get("hq_session"),
        request.headers.get("x-csrf-token"),
        body,
    )


@router.post("/measurements/csv")
def export_measurement_report_csv(body: ReportCsvRequest, request: Request):
    require_json_mutation(request)
    csv_bytes, filename = request.app.state.reports.export_csv(
        request.cookies.get("hq_session"),
        request.headers.get("x-csrf-token"),
        body,
    )
    return Response(
        content=csv_bytes,
        status_code=200,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
