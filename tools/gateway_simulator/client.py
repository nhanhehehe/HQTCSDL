from dataclasses import dataclass
from typing import Any

import httpx
from tools.gateway_simulator.ack_validator import validate_batch_response
from tools.gateway_simulator.queue_store import DurableQueueStore


@dataclass
class SyncStepResult:
    http_status: int | None
    sent_count: int
    released: int
    quarantined: int
    kept_pending: int
    retry_after_seconds: int | None = None
    error_code: str | None = None


class GatewaySimulatorClient:
    """Contract-compliant HTTP Gateway client for software integration testing."""

    def __init__(
        self,
        *,
        base_url: str,
        gateway_id: str,
        api_key: str,
        store: DurableQueueStore,
        max_batch_size: int = 50,
        online_ratio: int = 3,
        timeout_seconds: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.gateway_id = gateway_id
        self._api_key = api_key
        self.store = store
        self.max_batch_size = max(1, min(50, max_batch_size))
        self.online_ratio = max(1, online_ratio)
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    def sync_once(self, *, simulate_lost_response: bool = False) -> SyncStepResult:
        batch = self.store.select_batch(
            max_batch_size=self.max_batch_size,
            online_ratio=self.online_ratio,
        )
        if not batch:
            return SyncStepResult(
                http_status=None,
                sent_count=0,
                released=0,
                quarantined=0,
                kept_pending=0,
            )

        sent_records = [entry["record"] for entry in batch]
        payload = {"gateway_id": self.gateway_id, "records": sent_records}
        url = f"{self.base_url}/api/v1/ingestion/measurements"

        try:
            with httpx.Client(timeout=self.timeout_seconds, transport=self.transport) as client:
                response = client.post(
                    url,
                    json=payload,
                    headers={
                        "Content-Type": "application/json",
                        "X-API-Key": self._api_key,
                    },
                )
        except httpx.HTTPError:
            return SyncStepResult(
                http_status=None,
                sent_count=len(batch),
                released=0,
                quarantined=0,
                kept_pending=len(batch),
                error_code="NETWORK_ERROR",
            )

        if simulate_lost_response:
            # Server may have already committed; Gateway drops response and keeps all pending.
            return SyncStepResult(
                http_status=None,
                sent_count=len(batch),
                released=0,
                quarantined=0,
                kept_pending=len(batch),
                error_code="LOST_RESPONSE",
            )

        if response.status_code == 200:
            try:
                body: Any = response.json()
            except ValueError:
                return SyncStepResult(
                    http_status=200,
                    sent_count=len(batch),
                    released=0,
                    quarantined=0,
                    kept_pending=len(batch),
                    error_code="INVALID_RESPONSE_JSON",
                )
            outcomes = validate_batch_response(sent_records, body)
            counts = self.store.apply_outcomes(batch, outcomes)
            return SyncStepResult(
                http_status=200,
                sent_count=len(batch),
                released=counts["released"],
                quarantined=counts["quarantined"],
                kept_pending=counts["kept_pending"],
            )

        if response.status_code == 413 and self.max_batch_size > 1:
            self.max_batch_size = max(1, self.max_batch_size // 2)

        retry_after: int | None = None
        if response.status_code == 429:
            raw_ra = response.headers.get("retry-after", "")
            if raw_ra.isascii() and raw_ra.isdecimal():
                retry_after = int(raw_ra)

        error_code = None
        try:
            err_body = response.json()
            if isinstance(err_body, dict) and isinstance(err_body.get("error"), dict):
                error_code = err_body["error"].get("code")
        except ValueError:
            pass

        return SyncStepResult(
            http_status=response.status_code,
            sent_count=len(batch),
            released=0,
            quarantined=0,
            kept_pending=len(batch),
            retry_after_seconds=retry_after,
            error_code=error_code,
        )
