from typing import Any

from app.core.web import fail, serialize_utc, utc_ms_from_datetime, web_session_transaction
from app.modules.measurements import repository as repo
from app.modules.measurements.freshness import compute_freshness


class DashboardService:
    def __init__(self, database, settings):
        self.database = database
        self.settings = settings

    def get_dashboard(
        self, cookie: str | None, *, farm_id: str, node_id: str | None = None
    ) -> dict[str, Any]:
        with web_session_transaction(self.database, self.settings, cookie) as (session, auth_ctx):
            as_of_dt = auth_ctx["db_now"]
            as_of_iso = serialize_utc(as_of_dt)
            assert as_of_iso is not None
            as_of_utc_ms = utc_ms_from_datetime(as_of_dt)
            profiles = dict(self.settings.node_time_profiles)

            farm = repo.get_farm_summary(session, farm_id)
            if farm is None:
                fail(404, "NOT_FOUND")

            if node_id is not None:
                node_row = repo.get_dashboard_node(session, node_id)
                if node_row is None:
                    fail(404, "NOT_FOUND")
                if int(node_row["farm_id"]) != int(farm_id):
                    fail(400, "INPUT_INVALID", "Node không thuộc Farm đã chọn")
                target_nodes = [node_row]
                scope_kind = "NODE"
            else:
                target_nodes = repo.list_farm_dashboard_nodes(session, farm_id)
                scope_kind = "FARM"

            target_nids = [int(r["node_id"]) for r in target_nodes]
            headers_by_node = repo.load_latest_known_headers_for_nodes(session, target_nids)
            missing_known_nids = [nid for nid in target_nids if headers_by_node.get(nid) is None]
            nodes_with_any = repo.find_nodes_with_any_measurement(session, missing_known_nids)
            header_mids = [
                int(h["measurement_id"]) for h in headers_by_node.values() if h is not None
            ]
            values_map = repo.load_measurement_values_map(session, header_mids)

            node_views: list[dict[str, Any]] = []
            for row in target_nodes:
                nid = int(row["node_id"])
                device_code = row["device_code"]
                header = headers_by_node.get(nid)
                if header is not None:
                    mid = int(header["measurement_id"])
                    t_node_int = int(header["t_node"])
                    current_state = "AVAILABLE"
                    measurement_view = repo.build_measurement_view(
                        header,
                        values_map[mid],
                        device_code=device_code,
                    )
                else:
                    t_node_int = None
                    current_state = "NO_EVENT_TIME" if nid in nodes_with_any else "NO_DATA"
                    measurement_view = None

                current_envelope = {
                    "state": current_state,
                    "measurement": measurement_view,
                    "as_of": as_of_iso,
                }
                freshness_view = compute_freshness(
                    current_state=current_state,
                    t_node=t_node_int,
                    as_of_utc_ms=as_of_utc_ms,
                    profile=profiles.get(device_code),
                )
                node_views.append(
                    {
                        "profile": {
                            "id": str(nid),
                            "device_id": device_code,
                            "name": row["name"],
                            "zone": {
                                "id": str(row["zone_id"]),
                                "code": row["zone_code"],
                                "name": row["zone_name"],
                                "archived_at": serialize_utc(row["zone_archived_at"]),
                            },
                            "gateway": (
                                {
                                    "id": str(row["gateway_id"]),
                                    "gateway_id": row["gateway_code"],
                                    "name": row["gateway_name"],
                                    "receiving_enabled": bool(row["gateway_receiving_enabled"]),
                                }
                                if row["gateway_id"] is not None
                                else None
                            ),
                            "receiving_enabled": bool(row["receiving_enabled"]),
                        },
                        "current": current_envelope,
                        "freshness": freshness_view,
                        "monitoring": None,
                        "evaluation": None,
                        "active_alerts": None,
                        "active_count": None,
                    }
                )

            return {
                "as_of": as_of_iso,
                "capabilities": {"monitoring": False, "alerts": False},
                "scope": {
                    "kind": scope_kind,
                    "farm_id": str(farm["farm_id"]),
                    "farm": {
                        "id": str(farm["farm_id"]),
                        "code": farm["farm_code"],
                        "name": farm["name"],
                        "archived_at": serialize_utc(farm["archived_at"]),
                    },
                    "node_ids": [str(nid) for nid in target_nids],
                },
                "active_count": None,
                "nodes": node_views,
            }
