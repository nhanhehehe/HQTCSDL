import copy
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


class DurableQueueStore:
    """Atomic file-backed local queue simulating Gateway SD pending + quarantine storage."""

    def __init__(self, state_path: Path):
        self.state_path = Path(state_path)
        self.pending: list[dict[str, Any]] = []
        self.quarantine: list[dict[str, Any]] = []
        self.online_streak: int = 0
        self.load()

    def load(self) -> None:
        if not self.state_path.exists():
            self.pending = []
            self.quarantine = []
            self.online_streak = 0
            return
        data = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.pending = list(data.get("pending", []))
        self.quarantine = list(data.get("quarantine", []))
        self.online_streak = int(data.get("online_streak", 0))

    def save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        payload = {
            "pending": self.pending,
            "quarantine": self.quarantine,
            "online_streak": self.online_streak,
        }
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp_path, self.state_path)

    def enqueue(self, record: dict[str, Any], *, priority: str = "online") -> str:
        if priority not in {"online", "backlog"}:
            raise ValueError("priority must be 'online' or 'backlog'")
        entry_id = uuid4().hex
        self.pending.append(
            {
                "entry_id": entry_id,
                "priority": priority,
                "record": copy.deepcopy(record),
                "attempts": 0,
            }
        )
        self.save()
        return entry_id

    def select_batch(self, *, max_batch_size: int = 50, online_ratio: int = 3) -> list[dict]:
        """Select 1..50 records with fair online:backlog scheduling (default 3:1)."""
        if not self.pending:
            return []
        limit = max(1, min(50, max_batch_size))
        online_items = [e for e in self.pending if e["priority"] == "online"]
        backlog_items = [e for e in self.pending if e["priority"] == "backlog"]

        if online_items and backlog_items:
            if self.online_streak >= online_ratio:
                selected = backlog_items[:limit]
                self.online_streak = 0
            else:
                selected = online_items[:limit]
                self.online_streak += 1
        elif online_items:
            selected = online_items[:limit]
            self.online_streak = min(self.online_streak + 1, online_ratio)
        else:
            selected = backlog_items[:limit]
            self.online_streak = 0

        self.save()
        return [copy.deepcopy(e) for e in selected]

    def apply_outcomes(
        self,
        sent_entries: list[dict[str, Any]],
        outcomes: dict[int, dict[str, Any]],
    ) -> dict[str, int]:
        """Release ONLY verified ACK2 entries; quarantine REJECTED; keep all others pending."""
        release_ids: set[str] = set()
        quarantine_map: dict[str, dict[str, Any]] = {}
        retry_ids: set[str] = set()

        for idx, entry in enumerate(sent_entries):
            outcome = outcomes.get(idx, {"action": "RETRY", "reason": "MISSING"})
            action = outcome["action"]
            eid = entry["entry_id"]
            if action == "RELEASE":
                release_ids.add(eid)
            elif action == "QUARANTINE":
                quarantine_map[eid] = {
                    "entry_id": eid,
                    "record": copy.deepcopy(entry["record"]),
                    "reason": outcome.get("reason", "REJECTED"),
                    "quarantined_at": datetime.now(UTC)
                    .isoformat(timespec="milliseconds")
                    .replace("+00:00", "Z"),
                }
            else:
                retry_ids.add(eid)

        remaining: list[dict[str, Any]] = []
        for entry in self.pending:
            eid = entry["entry_id"]
            if eid in release_ids:
                continue
            if eid in quarantine_map:
                self.quarantine.append(quarantine_map[eid])
                continue
            if eid in retry_ids:
                entry["attempts"] = int(entry.get("attempts", 0)) + 1
            remaining.append(entry)

        self.pending = remaining
        self.save()
        return {
            "released": len(release_ids),
            "quarantined": len(quarantine_map),
            "kept_pending": len(retry_ids),
        }
