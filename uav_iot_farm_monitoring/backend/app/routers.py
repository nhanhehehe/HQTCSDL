from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from .database import get_db
from .models import Alert, CollectionMission, Farm, Gateway, Measurement, MissionPoint, SensorNode, User, Zone
from .schemas import FarmCreate, GatewayCreate, LoginRequest, MeasurementRecord, MissionCreate, SensorCreate, SyncBatch, ZoneCreate
from .security import create_token, get_current_user, require_role, verify_password

router = APIRouter()


def require_gateway(gateway_id: str, key: str | None, db: Session) -> Gateway:
    gateway = db.query(Gateway).filter(Gateway.gateway_id == gateway_id, Gateway.is_active == True).first()  # noqa: E712
    if not gateway or key != gateway.api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Gateway authentication failed")
    gateway.last_seen_at = datetime.utcnow()
    return gateway


def serialize_measurement(row: Measurement, sensor_id: str) -> dict:
    return {
        "sensor_id": sensor_id,
        "sequence_no": row.sequence_no,
        "temperature": float(row.temperature),
        "air_humidity": float(row.air_humidity),
        "pressure": float(row.pressure),
        "light": float(row.light),
        "soil_moisture": float(row.soil_moisture),
        "source": row.source,
        "quality": row.quality,
        "measured_at": row.measured_at,
        "received_at": row.received_at,
    }


def serialize_mission(item: CollectionMission, db: Session) -> dict:
    return {
        "id": item.id,
        "name": item.name,
        "status": item.status,
        "farm_id": item.farm_id,
        "gateway_id": item.gateway_id,
        "operator_note": item.operator_note,
        "created_at": item.created_at,
        "points": [
            {
                "sensor_node_id": point.sensor_node_id,
                "sensor_id": db.get(SensorNode, point.sensor_node_id).sensor_id,
                "sort_order": point.sort_order,
                "status": point.status,
                "error_reason": point.error_reason,
            }
            for point in item.points
        ],
    }


@router.get("/health")
def health():
    return {"status": "ok"}


@router.post("/auth/login")
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == payload.username, User.is_active == True).first()  # noqa: E712
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")
    return {"access_token": create_token(user), "role": user.role, "username": user.username}


@router.get("/dashboard")
def dashboard(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    latest = db.query(Measurement, SensorNode.sensor_id).join(SensorNode).order_by(Measurement.received_at.desc()).limit(20).all()
    return {
        "counts": {
            "farms": db.query(Farm).count(),
            "zones": db.query(Zone).count(),
            "sensors": db.query(SensorNode).count(),
            "gateways": db.query(Gateway).count(),
            "open_alerts": db.query(Alert).filter(Alert.status == "open").count(),
            "missions": db.query(CollectionMission).count(),
        },
        "latest_measurements": [serialize_measurement(row, sensor_id) for row, sensor_id in latest],
    }


@router.get("/farms")
def farms(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [{"id": item.id, "name": item.name, "description": item.description} for item in db.query(Farm).all()]


@router.post("/farms", status_code=201)
def create_farm(payload: FarmCreate, db: Session = Depends(get_db), user: User = Depends(require_role("admin"))):
    item = Farm(**payload.model_dump())
    db.add(item); db.commit(); db.refresh(item)
    return {"id": item.id, "name": item.name}


@router.get("/zones")
def zones(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [{"id": item.id, "farm_id": item.farm_id, "name": item.name, "latitude": item.latitude, "longitude": item.longitude} for item in db.query(Zone).all()]


@router.post("/zones", status_code=201)
def create_zone(payload: ZoneCreate, db: Session = Depends(get_db), user: User = Depends(require_role("admin"))):
    if not db.get(Farm, payload.farm_id):
        raise HTTPException(404, "Farm not found")
    item = Zone(**payload.model_dump())
    db.add(item); db.commit(); db.refresh(item)
    return {"id": item.id, "name": item.name}


@router.get("/devices/sensors")
def sensors(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [{"id": item.id, "sensor_id": item.sensor_id, "name": item.name, "zone_id": item.zone_id, "active": item.is_active} for item in db.query(SensorNode).all()]


@router.post("/devices/sensors", status_code=201)
def create_sensor(payload: SensorCreate, db: Session = Depends(get_db), user: User = Depends(require_role("operator", "admin"))):
    if not db.get(Zone, payload.zone_id):
        raise HTTPException(404, "Zone not found")
    item = SensorNode(**payload.model_dump())
    db.add(item); db.commit(); db.refresh(item)
    return {"id": item.id, "sensor_id": item.sensor_id}


@router.get("/devices/gateways")
def gateways(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [{"id": item.id, "gateway_id": item.gateway_id, "name": item.name, "active": item.is_active, "last_seen_at": item.last_seen_at} for item in db.query(Gateway).all()]


@router.post("/devices/gateways", status_code=201)
def create_gateway(payload: GatewayCreate, db: Session = Depends(get_db), user: User = Depends(require_role("operator", "admin"))):
    item = Gateway(**payload.model_dump())
    db.add(item); db.commit(); db.refresh(item)
    return {"id": item.id, "gateway_id": item.gateway_id}


@router.post("/missions", status_code=201)
def create_mission(payload: MissionCreate, db: Session = Depends(get_db), user: User = Depends(require_role("operator", "admin"))):
    gateway = db.get(Gateway, payload.gateway_id)
    if not db.get(Farm, payload.farm_id) or not gateway:
        raise HTTPException(404, "Farm or gateway not found")
    mission = CollectionMission(farm_id=payload.farm_id, gateway_id=payload.gateway_id, name=payload.name, operator_note=payload.operator_note)
    db.add(mission); db.flush()
    for order, node_id in enumerate(payload.sensor_node_ids, start=1):
        node = db.get(SensorNode, node_id)
        if not node:
            raise HTTPException(404, f"Sensor node {node_id} not found")
        db.add(MissionPoint(mission_id=mission.id, sensor_node_id=node_id, sort_order=order))
    db.commit(); db.refresh(mission)
    return {"id": mission.id, "status": mission.status}


@router.get("/missions")
def missions(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    items = db.query(CollectionMission).order_by(CollectionMission.created_at.desc()).all()
    return [serialize_mission(item, db) for item in items]


@router.get("/gateways/{gateway_id}/active-mission")
def active_mission(gateway_id: str, x_gateway_key: str | None = Header(default=None), db: Session = Depends(get_db)):
    gateway = require_gateway(gateway_id, x_gateway_key, db)
    mission = db.query(CollectionMission).filter(CollectionMission.gateway_id == gateway.id, CollectionMission.status.in_(["ready", "running"])).order_by(CollectionMission.created_at).first()
    if not mission:
        db.commit()
        return {"mission": None}
    if mission.status == "ready":
        mission.status = "running"
    db.commit()
    return {"mission": serialize_mission(mission, db)}


def save_record(record: MeasurementRecord, gateway: Gateway, db: Session) -> dict:
    node = db.query(SensorNode).filter(SensorNode.sensor_id == record.sensor_id, SensorNode.is_active == True).first()  # noqa: E712
    if not node:
        return {"sensor_id": record.sensor_id, "sequence_no": record.sequence_no, "status": "invalid", "reason": "unknown_sensor"}
    existing = db.query(Measurement).filter(Measurement.sensor_node_id == node.id, Measurement.sequence_no == record.sequence_no).first()
    if existing:
        return {"sensor_id": record.sensor_id, "sequence_no": record.sequence_no, "status": "duplicate"}
    row = Measurement(
        sensor_node_id=node.id,
        gateway_id=gateway.id,
        **record.model_dump(exclude={"sensor_id"}),
    )
    db.add(row)
    mission = db.query(CollectionMission).filter(
        CollectionMission.gateway_id == gateway.id,
        CollectionMission.status == "running",
    ).order_by(CollectionMission.created_at).first()
    if mission:
        point = db.query(MissionPoint).filter(
            MissionPoint.mission_id == mission.id,
            MissionPoint.sensor_node_id == node.id,
        ).first()
        if point:
            point.status = "completed"
            if all(current.status == "completed" for current in mission.points):
                mission.status = "completed"
    if record.temperature > 35:
        db.add(Alert(sensor_node_id=node.id, alert_type="high_temperature", severity="warning", message=f"Temperature {record.temperature} C exceeds 35 C"))
    return {"sensor_id": record.sensor_id, "sequence_no": record.sequence_no, "status": "accepted"}


@router.post("/sync/batches")
def sync_batch(payload: SyncBatch, x_gateway_key: str | None = Header(default=None), db: Session = Depends(get_db)):
    gateway = require_gateway(payload.gateway_id, x_gateway_key, db)
    acknowledgements = [save_record(record, gateway, db) for record in payload.records]
    db.commit()
    return {"gateway_id": gateway.gateway_id, "acknowledgements": acknowledgements}


@router.get("/measurements")
def measurements(limit: int = 100, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(Measurement, SensorNode.sensor_id).join(SensorNode).order_by(Measurement.measured_at.desc()).limit(min(limit, 500)).all()
    return [serialize_measurement(row, sensor_id) for row, sensor_id in rows]


@router.get("/reports/summary")
def report_summary(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    total, avg_temp, avg_humidity, avg_pressure, avg_light, avg_soil = db.query(
        func.count(Measurement.id),
        func.avg(Measurement.temperature),
        func.avg(Measurement.air_humidity),
        func.avg(Measurement.pressure),
        func.avg(Measurement.light),
        func.avg(Measurement.soil_moisture),
    ).one()
    return {
        "total_measurements": total,
        "open_alerts": db.query(Alert).filter(Alert.status == "open").count(),
        "averages": {
            "temperature": float(avg_temp) if avg_temp is not None else None,
            "air_humidity": float(avg_humidity) if avg_humidity is not None else None,
            "pressure": float(avg_pressure) if avg_pressure is not None else None,
            "light": float(avg_light) if avg_light is not None else None,
            "soil_moisture": float(avg_soil) if avg_soil is not None else None,
        },
    }


@router.get("/alerts")
def alerts(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [{"id": item.id, "type": item.alert_type, "severity": item.severity, "status": item.status, "message": item.message, "created_at": item.created_at} for item in db.query(Alert).order_by(Alert.created_at.desc()).all()]


@router.post("/alerts/{alert_id}/close")
def close_alert(alert_id: int, note: str = "", db: Session = Depends(get_db), user: User = Depends(require_role("admin"))):
    item = db.get(Alert, alert_id)
    if not item:
        raise HTTPException(404, "Alert not found")
    item.status = "closed"; item.handled_at = datetime.utcnow(); item.handled_by = user.id; item.note = note
    db.commit()
    return {"id": item.id, "status": item.status}
