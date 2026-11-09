from datetime import datetime

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str
    password: str


class FarmCreate(BaseModel):
    name: str
    description: str | None = None


class ZoneCreate(BaseModel):
    farm_id: int
    name: str
    latitude: float | None = None
    longitude: float | None = None


class SensorCreate(BaseModel):
    sensor_id: str
    name: str
    zone_id: int
    api_key: str


class GatewayCreate(BaseModel):
    gateway_id: str
    name: str
    api_key: str


class MissionCreate(BaseModel):
    farm_id: int
    gateway_id: int
    name: str
    sensor_node_ids: list[int] = Field(min_length=1)
    operator_note: str | None = None


class MeasurementRecord(BaseModel):
    sensor_id: str
    sequence_no: int = Field(ge=0)
    temperature: float = Field(ge=-40, le=85)
    air_humidity: float = Field(ge=0, le=100)
    pressure: float = Field(ge=300, le=1100)
    light: float = Field(ge=0, le=65535)
    soil_moisture: float = Field(ge=0, le=100)
    source: str = "physical"
    quality: str = "valid"
    measured_at: datetime
    collected_at: datetime | None = None


class SyncBatch(BaseModel):
    gateway_id: str
    records: list[MeasurementRecord] = Field(min_length=1, max_length=100)
