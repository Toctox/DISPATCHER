from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SENSITIVE_FRAGMENTS = ("token", "prompt", "secret", "authorization", "clipboard")


def _sanitize(value: Any, key: str = "") -> Any:
    lowered = key.casefold()
    if any(fragment in lowered for fragment in SENSITIVE_FRAGMENTS):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {
            str(item_key): _sanitize(item_value, str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


class JsonlAuditLogger:
    def __init__(self, directory: Path, *, console: bool = True) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y-%m-%d")
        self.path = directory / f"dispatcher-{stamp}.jsonl"
        self.console = console

    def record(self, event: str, **fields: Any) -> None:
        payload = {
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "event": event,
            **fields,
        }
        sanitized = _sanitize(payload)
        line = json.dumps(sanitized, ensure_ascii=False, separators=(",", ":"))
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        if self.console:
            print(line, file=sys.stderr)


class MemoryAuditLogger:
    """Test logger with the same contract as JsonlAuditLogger."""

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def record(self, event: str, **fields: Any) -> None:
        self.records.append(_sanitize({"event": event, **fields}))
