from pydantic import Field, field_validator

from app.core.schemas import WebModel
from app.modules.devices.schemas import identity


class ReportCreateRequest(WebModel):
    node_id: str
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None

    _node_id = field_validator("node_id")(identity)

    @field_validator("from_", "to")
    @classmethod
    def reject_explicit_null(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("Timestamp field cannot be null")
        return value


class ReportCsvRequest(WebModel):
    snapshot_token: str
