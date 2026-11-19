import logging
import random
import time
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.errors import IngestionError
from app.modules.areas import repository as areas_repo
from app.modules.devices import repository as devices_repo
from app.modules.ingestion import auth as ingestion_auth
from app.modules.ingestion import repository as repo
from app.modules.ingestion.canonical import (
    CANONICAL_VERSION,
    build_canonical_v1,
    is_same_original_content,
)
from app.modules.ingestion.validator import (
    ValidatedRecord,
    extract_valid_identity,
    validate_d06_timestamps,
    validate_envelope,
    validate_record_static,
)

logger = logging.getLogger("uvicorn.error.hqtcsdl.ingestion")

MAX_DEADLOCK_RETRIES = 3


class _IntegrityRollbackError(Exception):
    """Internal sentinel to force per-record transaction rollback on invariant violations."""


def _is_uq_identity_error(exc: SQLAlchemyError) -> bool:
    message = str(getattr(exc, "orig", ""))
    return ("2601" in message or "2627" in message) and "UQ_Measurement_identity" in message


def _is_deadlock_error(exc: SQLAlchemyError) -> bool:
    message = str(getattr(exc, "orig", ""))
    return "1205" in message


def _ack_result(index: int, echo: dict[str, Any], status: str) -> dict[str, Any]:
    return {"index": index, **echo, "status": status, "ack": True}


def _failed_result(
    index: int, echo: dict[str, Any], *, status: str, retryable: bool, code: str, message: str
) -> dict[str, Any]:
    return {
        "index": index,
        **echo,
        "status": status,
        "ack": False,
        "retryable": retryable,
        "error": {"code": code, "message": message},
    }


def _rejected(index: int, echo: dict[str, Any], code: str, message: str) -> dict[str, Any]:
    return _failed_result(
        index, echo, status="REJECTED", retryable=False, code=code, message=message
    )


def _retry_later(index: int, echo: dict[str, Any], code: str, message: str) -> dict[str, Any]:
    return _failed_result(
        index, echo, status="RETRY_LATER", retryable=True, code=code, message=message
    )


class IngestionService:
    def __init__(
        self,
        database,
        settings,
        *,
        clock: Callable[[], datetime] | None = None,
        sleep_fn: Callable[[float], None] | None = None,
    ):
        self.database = database
        self.settings = settings
        self.clock = clock or (lambda: datetime.now(UTC))
        self.sleep_fn = sleep_fn or time.sleep

    @contextmanager
    def _record_transaction(self, *, write: bool = True):
        if self.database.engine is None:
            raise IngestionError(
                503,
                "SERVICE_UNAVAILABLE",
                "Storage service is temporarily unavailable",
                retryable=True,
            )
        with (
            self.database.engine.connect().execution_options(
                isolation_level="READ COMMITTED"
            ) as connection,
            connection.begin(),
            Session(bind=connection) as session,
        ):
            if write:
                session.execute(text("SET XACT_ABORT ON"))
            yield session

    def _authenticate_request(
        self, *, api_key: str | None, declared_gateway_code: str
    ) -> ingestion_auth.AuthenticatedGateway:
        # Validate API key format before opening a DB connection.
        ingestion_auth.extract_api_key_digest(api_key)
        try:
            with self._record_transaction(write=False) as session:
                return ingestion_auth.authenticate_gateway(
                    session,
                    api_key=api_key,
                    declared_gateway_code=declared_gateway_code,
                )
        except IngestionError:
            raise
        except SQLAlchemyError:
            raise IngestionError(
                503,
                "SERVICE_UNAVAILABLE",
                "Storage service is temporarily unavailable",
                retryable=True,
            ) from None

    def _recheck_credential_for_invalid_item(
        self, auth: ingestion_auth.AuthenticatedGateway
    ) -> None:
        try:
            with self._record_transaction(write=True) as session:
                areas_repo.get_farm(session, str(auth.farm_id), lock=True)
                devices_repo.lock_gateway(session, auth.gateway_id)
                ingestion_auth.recheck_credential_locked(session, auth)
        except IngestionError:
            raise
        except SQLAlchemyError:
            raise IngestionError(
                503,
                "SERVICE_UNAVAILABLE",
                "Storage service is temporarily unavailable",
                retryable=True,
            ) from None

    def _reread_after_uq(
        self,
        *,
        index: int,
        echo: dict[str, Any],
        auth: ingestion_auth.AuthenticatedGateway,
        record: ValidatedRecord,
    ) -> dict[str, Any]:
        with self._record_transaction(write=True) as session:
            node_info = repo.resolve_node_for_ingestion(session, record.device_id)
            if node_info is None or node_info["gateway_id"] != auth.gateway_id:
                areas_repo.get_farm(session, str(auth.farm_id), lock=True)
                devices_repo.lock_gateway(session, auth.gateway_id)
                ingestion_auth.recheck_credential_locked(session, auth)
                return _rejected(
                    index,
                    echo,
                    "INVALID_SOURCE",
                    "Node is not registered or not linked to authenticated Gateway",
                )
            farm_ids = sorted({auth.farm_id, int(node_info["zone_farm_id"])})
            for fid in farm_ids:
                areas_repo.get_farm(session, str(fid), lock=True)
            areas_repo.get_zone(session, str(node_info["zone_id"]), lock=True)
            gateway = devices_repo.lock_gateway(session, auth.gateway_id)
            node = devices_repo.lock_node(session, node_info["node_id"])
            ingestion_auth.recheck_credential_locked(session, auth)
            if gateway is None or node is None or node["gateway_id"] != auth.gateway_id:
                return _rejected(
                    index,
                    echo,
                    "INVALID_SOURCE",
                    "Node is not linked to authenticated Gateway",
                )
            existing = repo.seek_measurement_locked(
                session,
                node_id=int(node["node_id"]),
                boot_id=record.boot_id,
                seq=record.seq,
            )
            if existing is None:
                return _retry_later(
                    index,
                    echo,
                    "STORAGE_UNAVAILABLE",
                    "Measurement identity conflict could not be resolved",
                )
            stored_values = repo.get_measurement_values(session, int(existing["measurement_id"]))
            if is_same_original_content(
                record,
                stored_device_code=str(node["device_code"]),
                stored_header=existing,
                stored_values=stored_values,
            ):
                return _ack_result(index, echo, "ALREADY_EXISTS")
            return _rejected(
                index,
                echo,
                "RECORD_CONFLICT",
                "Record ID exists with different content",
            )

    def _process_record_once(
        self,
        *,
        index: int,
        echo: dict[str, Any],
        auth: ingestion_auth.AuthenticatedGateway,
        record: ValidatedRecord,
        backend_received_at_db: datetime,
        backend_received_epoch_sec: int,
    ) -> dict[str, Any]:
        with self._record_transaction(write=True) as session:
            node_info = repo.resolve_node_for_ingestion(session, record.device_id)
            if node_info is None or node_info["gateway_id"] != auth.gateway_id:
                areas_repo.get_farm(session, str(auth.farm_id), lock=True)
                devices_repo.lock_gateway(session, auth.gateway_id)
                ingestion_auth.recheck_credential_locked(session, auth)
                return _rejected(
                    index,
                    echo,
                    "INVALID_SOURCE",
                    "Node is not registered or not linked to authenticated Gateway",
                )

            # Canonical lock order: Farm (id ASC) -> Zone -> Gateway -> Node -> state.
            farm_ids = sorted({auth.farm_id, int(node_info["zone_farm_id"])})
            farms = {fid: areas_repo.get_farm(session, str(fid), lock=True) for fid in farm_ids}
            zone = areas_repo.get_zone(session, str(node_info["zone_id"]), lock=True)
            gateway = devices_repo.lock_gateway(session, auth.gateway_id)
            node = devices_repo.lock_node(session, node_info["node_id"])

            # Recheck credential under Gateway guard (E06-03).
            ingestion_auth.recheck_credential_locked(session, auth)

            if gateway is None or node is None or node["gateway_id"] != auth.gateway_id:
                return _rejected(
                    index,
                    echo,
                    "INVALID_SOURCE",
                    "Node is not linked to authenticated Gateway",
                )

            node_id = int(node["node_id"])
            existing = repo.seek_measurement_locked(
                session,
                node_id=node_id,
                boot_id=record.boot_id,
                seq=record.seq,
            )
            if existing is not None:
                stored_values = repo.get_measurement_values(
                    session, int(existing["measurement_id"])
                )
                if is_same_original_content(
                    record,
                    stored_device_code=str(node["device_code"]),
                    stored_header=existing,
                    stored_values=stored_values,
                ):
                    return _ack_result(index, echo, "ALREADY_EXISTS")
                return _rejected(
                    index,
                    echo,
                    "RECORD_CONFLICT",
                    "Record ID exists with different content",
                )

            # Step D (G2-D03): D06 -> Configuration -> Receiving permissions.
            d06_error = validate_d06_timestamps(record, backend_received_epoch_sec)
            if d06_error is not None:
                return _rejected(index, echo, "INVALID_RECORD", d06_error)

            gw_farm = farms.get(int(gateway["farm_id"]))
            zone_farm = farms.get(int(zone["farm_id"])) if zone is not None else None
            if (
                zone is None
                or gw_farm is None
                or zone_farm is None
                or int(zone["farm_id"]) != int(gateway["farm_id"])
                or gw_farm["archived_at"] is not None
                or zone["archived_at"] is not None
            ):
                return _retry_later(
                    index,
                    echo,
                    "CONFIGURATION_INVALID",
                    "Node zone/farm configuration is invalid or archived",
                )

            if not gateway["receiving_enabled"] or not node["receiving_enabled"]:
                return _retry_later(
                    index,
                    echo,
                    "RECEIVING_DISABLED",
                    "Gateway or Node receiving permission is disabled",
                )

            # Step E: atomic insert Measurement + 5 Values + NodeRuntime + EvaluationState.
            _, original_hash = build_canonical_v1(record)
            prev_max_t_node = (
                repo.get_max_event_time(session, node_id) if record.time_status == "VALID" else None
            )
            inserted = repo.insert_measurement(
                session,
                node_id=node_id,
                gateway_id=int(gateway["gateway_id"]),
                record=record,
                backend_received_at=backend_received_at_db,
                canonical_version=CANONICAL_VERSION,
                original_hash=original_hash,
            )
            measurement_id = int(inserted["measurement_id"])
            val_count = repo.insert_measurement_values(session, measurement_id, record)
            if val_count != 5:
                raise _IntegrityRollbackError("Expected 5 MeasurementValue rows inserted")
            rt_count = repo.update_node_runtime(
                session,
                node_id=node_id,
                measurement_id=measurement_id,
                persisted_at=inserted["persisted_at"],
            )
            if rt_count != 1:
                raise _IntegrityRollbackError("Expected exactly 1 NodeRuntime row updated")
            ev_count = repo.update_evaluation_state(
                session,
                node_id=node_id,
                time_status=record.time_status,
                t_node=record.t_node,
                prev_max_t_node=prev_max_t_node,
            )
            if ev_count != 1:
                raise _IntegrityRollbackError("Expected exactly 1 EvaluationState row updated")

        # Only after COMMIT succeeds can STORED be returned.
        return _ack_result(index, echo, "STORED")

    def _process_record_with_retry(
        self,
        *,
        index: int,
        echo: dict[str, Any],
        auth: ingestion_auth.AuthenticatedGateway,
        record: ValidatedRecord,
        backend_received_at_db: datetime,
        backend_received_epoch_sec: int,
    ) -> dict[str, Any]:
        for attempt in range(MAX_DEADLOCK_RETRIES + 1):
            try:
                return self._process_record_once(
                    index=index,
                    echo=echo,
                    auth=auth,
                    record=record,
                    backend_received_at_db=backend_received_at_db,
                    backend_received_epoch_sec=backend_received_epoch_sec,
                )
            except IngestionError:
                raise
            except _IntegrityRollbackError:
                logger.error("ingestion_integrity_rollback index=%s", index)
                return _retry_later(
                    index,
                    echo,
                    "STORAGE_UNAVAILABLE",
                    "Storage integrity check failed; transaction rolled back",
                )
            except SQLAlchemyError as exc:
                if _is_uq_identity_error(exc):
                    try:
                        return self._reread_after_uq(
                            index=index, echo=echo, auth=auth, record=record
                        )
                    except IngestionError:
                        raise
                    except SQLAlchemyError:
                        logger.error("ingestion_uq_reread_failed index=%s", index)
                        return _retry_later(
                            index,
                            echo,
                            "STORAGE_UNAVAILABLE",
                            "Storage service is temporarily unavailable",
                        )
                if _is_deadlock_error(exc) and attempt < MAX_DEADLOCK_RETRIES:
                    self.sleep_fn(random.uniform(0.05, 0.2))
                    continue
                logger.error("ingestion_sql_error index=%s", index)
                return _retry_later(
                    index,
                    echo,
                    "STORAGE_UNAVAILABLE",
                    "Storage service is temporarily unavailable",
                )
        return _retry_later(
            index,
            echo,
            "STORAGE_UNAVAILABLE",
            "Storage service is temporarily unavailable",
        )

    def ingest(self, *, api_key: str | None, payload: Any) -> dict[str, list[dict[str, Any]]]:
        now_utc = self.clock().astimezone(UTC)
        backend_received_epoch_sec = int(now_utc.timestamp())
        backend_received_at_db = now_utc.replace(
            tzinfo=None,
            microsecond=(now_utc.microsecond // 1000) * 1000,
        )

        declared_gateway_code, raw_records = validate_envelope(payload)
        auth = self._authenticate_request(
            api_key=api_key,
            declared_gateway_code=declared_gateway_code,
        )

        results: list[dict[str, Any]] = []
        for index, raw_item in enumerate(raw_records):
            echo = extract_valid_identity(raw_item)
            record, static_error = validate_record_static(raw_item)
            if static_error is not None or record is None:
                self._recheck_credential_for_invalid_item(auth)
                results.append(
                    _rejected(
                        index,
                        echo,
                        "INVALID_RECORD",
                        static_error or "Invalid measurement record",
                    )
                )
                continue
            results.append(
                self._process_record_with_retry(
                    index=index,
                    echo=echo,
                    auth=auth,
                    record=record,
                    backend_received_at_db=backend_received_at_db,
                    backend_received_epoch_sec=backend_received_epoch_sec,
                )
            )
        return {"results": results}
