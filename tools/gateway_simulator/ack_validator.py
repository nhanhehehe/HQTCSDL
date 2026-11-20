from collections import Counter
from typing import Any

ACK_SUCCESS_STATUSES = frozenset({"STORED", "ALREADY_EXISTS"})
ACK_FAIL_STATUSES = frozenset({"RETRY_LATER", "REJECTED"})


def _identity_matches(sent: dict[str, Any], item: dict[str, Any]) -> bool:
    return (
        type(item.get("device_id")) is str
        and item.get("device_id") == sent.get("device_id")
        and type(item.get("boot_id")) is int
        and item.get("boot_id") == sent.get("boot_id")
        and type(item.get("seq")) is int
        and item.get("seq") == sent.get("seq")
    )


def validate_batch_response(
    sent_records: list[dict[str, Any]],
    response_payload: Any,
) -> dict[int, dict[str, Any]]:
    """Validate the ENTIRE results[] array before releasing any pending record (E06-02).

    Returns a mapping from 0-based request index to:
      {"action": "RELEASE" | "RETRY" | "QUARANTINE", "reason": str, "item": dict | None}
    Any missing index, duplicate index, mismatched identity, or contradictory status/ack
    is classified as RETRY so the record is never released speculatively.
    """
    n = len(sent_records)
    default_retry = {
        i: {"action": "RETRY", "reason": "UNTRUSTED_RESPONSE", "item": None} for i in range(n)
    }

    if not isinstance(response_payload, dict):
        return default_retry
    results = response_payload.get("results")
    if not isinstance(results, list):
        return default_retry

    # First pass: inspect ALL elements to detect duplicate/invalid indices before any release.
    index_counts: Counter[int] = Counter()
    by_index: dict[int, dict[str, Any]] = {}
    for entry in results:
        if not isinstance(entry, dict):
            continue
        idx = entry.get("index")
        if type(idx) is int and 0 <= idx < n:
            index_counts[idx] += 1
            by_index[idx] = entry

    outcomes: dict[int, dict[str, Any]] = {}
    for i in range(n):
        if index_counts[i] != 1:
            reason = "DUPLICATE_INDEX" if index_counts[i] > 1 else "MISSING_INDEX"
            outcomes[i] = {"action": "RETRY", "reason": reason, "item": None}
            continue

        item = by_index[i]
        status = item.get("status")
        ack = item.get("ack")
        if type(ack) is not bool or type(status) is not str:
            outcomes[i] = {"action": "RETRY", "reason": "INVALID_RESULT_TYPES", "item": item}
            continue

        if status in ACK_SUCCESS_STATUSES:
            if ack is True and _identity_matches(sent_records[i], item):
                outcomes[i] = {"action": "RELEASE", "reason": status, "item": item}
            else:
                outcomes[i] = {
                    "action": "RETRY",
                    "reason": "ACK_OR_IDENTITY_MISMATCH",
                    "item": item,
                }
        elif status == "REJECTED":
            if ack is False:
                error = item.get("error") if isinstance(item.get("error"), dict) else {}
                outcomes[i] = {
                    "action": "QUARANTINE",
                    "reason": str(error.get("code", "REJECTED")),
                    "item": item,
                }
            else:
                outcomes[i] = {"action": "RETRY", "reason": "CONTRADICTORY_ACK", "item": item}
        elif status == "RETRY_LATER":
            if ack is False:
                error = item.get("error") if isinstance(item.get("error"), dict) else {}
                outcomes[i] = {
                    "action": "RETRY",
                    "reason": str(error.get("code", "RETRY_LATER")),
                    "item": item,
                }
            else:
                outcomes[i] = {"action": "RETRY", "reason": "CONTRADICTORY_ACK", "item": item}
        else:
            outcomes[i] = {"action": "RETRY", "reason": "UNKNOWN_STATUS", "item": item}

    return outcomes
