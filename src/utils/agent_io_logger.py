from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import BaseModel


def _json_default(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "value") and not isinstance(value, (str, bytes, bytearray)):
        return getattr(value, "value")
    return str(value)


class AgentIoLogger:
    """Append structured agent I/O records to `world/log`."""

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.base_dir / "agent_io.jsonl"
        self._lock = threading.Lock()

    def __call__(self, record: Dict[str, Any]) -> None:
        self.record(record)

    def record(self, record: Dict[str, Any]) -> None:
        payload = dict(record)
        payload.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        line = json.dumps(payload, ensure_ascii=False, default=_json_default)
        with self._lock:
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")


def make_io_record(
    *,
    kind: str,
    agent_name: str,
    input_data: Any,
    output_data: Any = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    record: Dict[str, Any] = {
        "kind": kind,
        "agent_name": agent_name,
        "input": input_data,
        "output": output_data,
    }
    if extra:
        record.update(extra)
    return record