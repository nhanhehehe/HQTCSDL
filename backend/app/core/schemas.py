from pydantic import BaseModel, ConfigDict


class WebModel(BaseModel):
    model_config = ConfigDict(
        strict=True,
        extra="forbid",
    )
