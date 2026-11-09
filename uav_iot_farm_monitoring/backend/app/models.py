from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(30), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Farm(Base):
    __tablename__ = "farms"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(150), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    zones: Mapped[list["Zone"]] = relationship(back_populates="farm")


class Zone(Base):
    __tablename__ = "zones"
    __table_args__ = (UniqueConstraint("farm_id", "name", name="uq_zones_farm_name"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    latitude: Mapped[float | None] = mapped_column(Numeric(10, 7))
    longitude: Mapped[float | None] = mapped_column(Numeric(10, 7))
    farm: Mapped[Farm] = relationship(back_populates="zones")


class SensorNode(Base):
    __tablename__ = "sensor_nodes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sensor_id: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    zone_id: Mapped[int] = mapped_column(ForeignKey("zones.id"), nullable=False)
    api_key: Mapped[str] = mapped_column(String(150), unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Gateway(Base):
    __tablename__ = "gateways"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    gateway_id: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    api_key: Mapped[str] = mapped_column(String(150), unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime)


class CollectionMission(Base):
    __tablename__ = "collection_missions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), nullable=False)
    gateway_id: Mapped[int] = mapped_column(ForeignKey("gateways.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="ready")
    operator_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    points: Mapped[list["MissionPoint"]] = relationship(back_populates="mission", cascade="all, delete-orphan")


class MissionPoint(Base):
    __tablename__ = "mission_points"
    __table_args__ = (UniqueConstraint("mission_id", "sort_order", name="uq_mission_point_order"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mission_id: Mapped[int] = mapped_column(ForeignKey("collection_missions.id"), nullable=False)
    sensor_node_id: Mapped[int] = mapped_column(ForeignKey("sensor_nodes.id"), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="pending")
    error_reason: Mapped[str | None] = mapped_column(String(500))
    mission: Mapped[CollectionMission] = relationship(back_populates="points")


class Measurement(Base):
    __tablename__ = "measurements"
    __table_args__ = (UniqueConstraint("sensor_node_id", "sequence_no", name="uq_measurements_node_sequence"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sensor_node_id: Mapped[int] = mapped_column(ForeignKey("sensor_nodes.id"), nullable=False)
    gateway_id: Mapped[int | None] = mapped_column(ForeignKey("gateways.id"))
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    temperature: Mapped[float | None] = mapped_column(Numeric(6, 2))
    air_humidity: Mapped[float | None] = mapped_column(Numeric(6, 2))
    pressure: Mapped[float | None] = mapped_column(Numeric(8, 2))
    light: Mapped[float | None] = mapped_column(Numeric(10, 2))
    soil_moisture: Mapped[float | None] = mapped_column(Numeric(6, 2))
    source: Mapped[str] = mapped_column(String(30), default="physical")
    quality: Mapped[str] = mapped_column(String(30), default="valid")
    measured_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    collected_at: Mapped[datetime | None] = mapped_column(DateTime)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sensor_node_id: Mapped[int | None] = mapped_column(ForeignKey("sensor_nodes.id"))
    alert_type: Mapped[str] = mapped_column(String(80), nullable=False)
    severity: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="open")
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    handled_at: Mapped[datetime | None] = mapped_column(DateTime)
    handled_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    note: Mapped[str | None] = mapped_column(Text)
