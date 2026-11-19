import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any

from tools.gateway_simulator.client import GatewaySimulatorClient
from tools.gateway_simulator.queue_store import DurableQueueStore


def build_sample_record(
    *,
    device_id: str,
    boot_id: int,
    seq: int,
    t_node: int | None = None,
    time_status: str = "VALID",
    recv_time: int | None = None,
    rssi: float | None = -85.0,
) -> dict[str, Any]:
    now = int(time.time())
    actual_recv = now if recv_time is None else recv_time
    actual_t_node = (
        None if time_status == "UNKNOWN" else (actual_recv - 2 if t_node is None else t_node)
    )
    return {
        "device_id": device_id,
        "boot_id": boot_id,
        "seq": seq,
        "t_node": actual_t_node,
        "time_status": time_status,
        "recv_time": actual_recv,
        "rssi": rssi,
        "measurements": {
            "temperature": {"value": 28.5, "status": "OK"},
            "airHumidity": {"value": 75.24, "status": "OK"},
            "pressure": {"value": 1008.42, "status": "OK"},
            "light": {"value": 245.5, "status": "OK"},
            "soilMoisture": {"value": 48.0, "status": "OK"},
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gateway-simulator",
        description="HQTCSDL Gateway HTTP ingestion simulator (contract 03.1 / FL-06).",
    )
    parser.add_argument("--state", required=True, help="Path to local durable queue JSON file")
    sub = parser.add_subparsers(dest="command", required=True)

    enq = sub.add_parser("enqueue", help="Enqueue a sample measurement record")
    enq.add_argument("--device-id", required=True)
    enq.add_argument("--boot-id", type=int, default=1)
    enq.add_argument("--seq", type=int, required=True)
    enq.add_argument("--priority", choices=["online", "backlog"], default="online")
    enq.add_argument("--time-status", choices=["VALID", "UNKNOWN"], default="VALID")

    sync_cmd = sub.add_parser("sync", help="Send one batch to Backend ingestion API")
    sync_cmd.add_argument("--base-url", default="http://127.0.0.1:8000")
    sync_cmd.add_argument("--gateway-id", required=True)
    sync_cmd.add_argument("--max-batch-size", type=int, default=50)
    sync_cmd.add_argument("--simulate-lost-response", action="store_true")

    sub.add_parser("status", help="Show pending and quarantine queue counts")

    args = parser.parse_args(argv)
    store = DurableQueueStore(Path(args.state))

    if args.command == "enqueue":
        record = build_sample_record(
            device_id=args.device_id,
            boot_id=args.boot_id,
            seq=args.seq,
            time_status=args.time_status,
        )
        entry_id = store.enqueue(record, priority=args.priority)
        print(f"enqueued entry_id={entry_id} pending={len(store.pending)}")
        return 0

    if args.command == "status":
        print(
            f"pending={len(store.pending)} quarantine={len(store.quarantine)} "
            f"online_streak={store.online_streak}"
        )
        return 0

    if args.command == "sync":
        api_key = os.environ.get("HQ_SIM_API_KEY", "")
        if not api_key:
            print("Set HQ_SIM_API_KEY in environment before syncing.", file=sys.stderr)
            return 2
        client = GatewaySimulatorClient(
            base_url=args.base_url,
            gateway_id=args.gateway_id,
            api_key=api_key,
            store=store,
            max_batch_size=args.max_batch_size,
        )
        result = client.sync_once(simulate_lost_response=args.simulate_lost_response)
        print(
            f"http_status={result.http_status} sent={result.sent_count} "
            f"released={result.released} quarantined={result.quarantined} "
            f"kept_pending={result.kept_pending} error_code={result.error_code}"
        )
        return 0 if result.http_status == 200 else 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
