from datetime import datetime, timedelta

from .database import Base, SessionLocal, engine
from .models import Farm, Gateway, Measurement, SensorNode, User, Zone
from .security import hash_password


def seed() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if not db.query(User).filter(User.username == "admin").first():
            db.add(User(username="admin", password_hash=hash_password("Admin@123"), role="admin"))
            db.add(User(username="operator", password_hash=hash_password("Operator@123"), role="operator"))
        db.flush()
        farm = db.query(Farm).filter(Farm.name == "Farm Demo").first()
        if not farm:
            farm = Farm(name="Farm Demo", description="Sample farm for integration testing")
            db.add(farm); db.flush()
        zone = db.query(Zone).filter(Zone.farm_id == farm.id, Zone.name == "Zone A").first()
        if not zone:
            zone = Zone(farm_id=farm.id, name="Zone A", latitude=10.7769, longitude=106.7009)
            db.add(zone); db.flush()
        node = db.query(SensorNode).filter(SensorNode.sensor_id == "NODE_001").first()
        if not node:
            node = SensorNode(sensor_id="NODE_001", name="ESP32 Node 001", zone_id=zone.id, api_key="node-local-key")
            db.add(node); db.flush()
        gateway = db.query(Gateway).filter(Gateway.gateway_id == "GATEWAY_001").first()
        if not gateway:
            gateway = Gateway(gateway_id="GATEWAY_001", name="ESP32 Gateway 001", api_key="gateway-local-key")
            db.add(gateway); db.flush()
        if not db.query(Measurement).first():
            now = datetime.utcnow()
            for index in range(12):
                db.add(Measurement(sensor_node_id=node.id, gateway_id=gateway.id, sequence_no=index + 1, temperature=29 + index * 0.2, air_humidity=65 + index * 0.3, pressure=1005 + index, light=200 + index * 10, soil_moisture=45 - index * 0.2, source="sample", quality="valid", measured_at=now - timedelta(minutes=(12-index)*5), collected_at=now - timedelta(minutes=(12-index)*5)))
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    seed()
