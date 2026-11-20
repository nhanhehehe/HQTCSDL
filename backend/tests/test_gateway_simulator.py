import sys
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.gateway_simulator.ack_validator import validate_batch_response  # noqa: E402
from tools.gateway_simulator.cli import build_sample_record  # noqa: E402
from tools.gateway_simulator.client import GatewaySimulatorClient  # noqa: E402
from tools.gateway_simulator.queue_store import DurableQueueStore  # noqa: E402


def test_e06_02_full_response_ack2_validation():
    sent = [
        build_sample_record(device_id="NODE-01", boot_id=1, seq=1),
        build_sample_record(device_id="NODE-01", boot_id=1, seq=2),
        build_sample_record(device_id="NODE-01", boot_id=1, seq=3),
    ]

    # Duplicate index 0 later in response MUST prevent releasing index 0!
    dup_resp = {
        "results": [
            {
                "index": 0,
                "device_id": "NODE-01",
                "boot_id": 1,
                "seq": 1,
                "status": "STORED",
                "ack": True,
            },
            {
                "index": 1,
                "device_id": "NODE-01",
                "boot_id": 1,
                "seq": 2,
                "status": "STORED",
                "ack": True,
            },
            {
                "index": 0,
                "device_id": "NODE-01",
                "boot_id": 1,
                "seq": 1,
                "status": "REJECTED",
                "ack": False,
            },
        ]
    }
    out = validate_batch_response(sent, dup_resp)
    assert out[0]["action"] == "RETRY" and out[0]["reason"] == "DUPLICATE_INDEX"
    assert out[1]["action"] == "RELEASE"
    assert out[2]["action"] == "RETRY" and out[2]["reason"] == "MISSING_INDEX"

    # Identity mismatch or contradictory ack=false with STORED -> NEVER release
    bad_resp = {
        "results": [
            {
                "index": 0,
                "device_id": "NODE-01",
                "boot_id": 1,
                "seq": 999,
                "status": "STORED",
                "ack": True,
            },
            {
                "index": 1,
                "device_id": "NODE-01",
                "boot_id": 1,
                "seq": 2,
                "status": "STORED",
                "ack": False,
            },
            {
                "index": 2,
                "device_id": "NODE-01",
                "boot_id": 1,
                "seq": 3,
                "status": "REJECTED",
                "ack": False,
                "error": {"code": "RECORD_CONFLICT"},
            },
        ]
    }
    out2 = validate_batch_response(sent, bad_resp)
    assert out2[0]["action"] == "RETRY"
    assert out2[1]["action"] == "RETRY"
    assert out2[2]["action"] == "QUARANTINE"


def test_durable_queue_restart_quarantine_and_3_to_1_fairness(tmp_path):
    state_file = tmp_path / "queue.json"
    store = DurableQueueStore(state_file)
    for seq in range(1, 5):
        store.enqueue(
            build_sample_record(device_id="NODE-01", boot_id=1, seq=seq), priority="online"
        )
    store.enqueue(build_sample_record(device_id="NODE-02", boot_id=1, seq=1), priority="backlog")

    # Simulate Gateway restart by reloading from disk
    reloaded = DurableQueueStore(state_file)
    assert len(reloaded.pending) == 5

    # Fairness 3:1: 3 online turns, 4th turn serves backlog even when online items remain
    turns = [
        reloaded.select_batch(max_batch_size=1, online_ratio=3)[0]["priority"] for _ in range(4)
    ]
    assert turns == ["online", "online", "online", "backlog"]


def test_client_lost_response_413_split_and_429_retry_after(tmp_path):
    state_file = tmp_path / "sim.json"
    store = DurableQueueStore(state_file)
    r1 = build_sample_record(device_id="NODE-01", boot_id=2, seq=1)
    r2 = build_sample_record(device_id="NODE-01", boot_id=2, seq=2)
    store.enqueue(r1)
    store.enqueue(r2)

    seen_calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read()
        seen_calls.append({"len": len(body), "api_key": request.headers.get("x-api-key")})
        call_no = len(seen_calls)
        if call_no == 1:
            return httpx.Response(
                413, json={"error": {"code": "PAYLOAD_TOO_LARGE"}, "retryable": False}
            )
        if call_no == 2:
            return httpx.Response(
                429,
                headers={"Retry-After": "15"},
                json={"error": {"code": "RATE_LIMITED"}, "retryable": True},
            )
        if call_no == 3:
            # Server commits r1, but client simulates lost response
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "index": 0,
                            "device_id": "NODE-01",
                            "boot_id": 2,
                            "seq": 1,
                            "status": "STORED",
                            "ack": True,
                        }
                    ]
                },
            )
        # Retry receives ALREADY_EXISTS
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "index": 0,
                        "device_id": "NODE-01",
                        "boot_id": 2,
                        "seq": 1,
                        "status": "ALREADY_EXISTS",
                        "ack": True,
                    }
                ]
            },
        )

    client = GatewaySimulatorClient(
        base_url="http://127.0.0.1:8000",
        gateway_id="GW-01",
        api_key="hqgw_v1_dummy",
        store=store,
        max_batch_size=2,
        transport=httpx.MockTransport(handler),
    )

    # 1) 413 splits max_batch_size from 2 -> 1 without releasing anything
    res1 = client.sync_once()
    assert res1.http_status == 413 and res1.released == 0
    assert client.max_batch_size == 1
    assert len(store.pending) == 2

    # 2) 429 preserves pending and reports Retry-After
    res2 = client.sync_once()
    assert res2.http_status == 429 and res2.retry_after_seconds == 15
    assert len(store.pending) == 2

    # 3) Lost response keeps record pending
    res3 = client.sync_once(simulate_lost_response=True)
    assert res3.error_code == "LOST_RESPONSE" and res3.released == 0
    assert len(store.pending) == 2

    # 4) Idempotent retry receives ALREADY_EXISTS -> releases r1, preserves r2
    res4 = client.sync_once()
    assert res4.http_status == 200 and res4.released == 1
    assert len(store.pending) == 1
    assert store.pending[0]["record"]["seq"] == 2
