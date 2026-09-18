from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


GENESIS_HASH = "0" * 64
SENSITIVE_FIELDS = {"api_key", "authorization", "x-api-key", "token", "secret"}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if key.lower() in SENSITIVE_FIELDS else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


class AuditLog:
    def __init__(self, session_id: str, directory: Path | None = None) -> None:
        root = directory or Path.home() / ".local" / "share" / "x-sidechain" / "sessions"
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / f"{session_id}.jsonl"
        self._sequence = 0
        self._previous_hash = GENESIS_HASH
        self._lock = threading.Lock()

    def append(self, event: str, payload: dict[str, Any]) -> str:
        with self._lock:
            record = {
                "sequence": self._sequence,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": event,
                "payload": _redact(payload),
                "previous_hash": self._previous_hash,
            }
            event_hash = hashlib.sha256(_canonical(record)).hexdigest()
            record["event_hash"] = event_hash
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                handle.flush()
            self._sequence += 1
            self._previous_hash = event_hash
            return event_hash

    @staticmethod
    def verify(path: str | Path) -> tuple[bool, str]:
        previous = GENESIS_HASH
        expected_sequence = 0
        try:
            with Path(path).open("r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    record = json.loads(line)
                    actual_hash = record.pop("event_hash")
                    if record.get("sequence") != expected_sequence:
                        return False, f"sequence mismatch at line {line_number}"
                    if record.get("previous_hash") != previous:
                        return False, f"chain mismatch at line {line_number}"
                    calculated = hashlib.sha256(_canonical(record)).hexdigest()
                    if calculated != actual_hash:
                        return False, f"content hash mismatch at line {line_number}"
                    previous = actual_hash
                    expected_sequence += 1
        except (OSError, json.JSONDecodeError, KeyError) as exc:
            return False, f"invalid audit log: {exc}"
        if expected_sequence == 0:
            return False, "audit log is empty"
        return True, f"verified {expected_sequence} events; head={previous}"

