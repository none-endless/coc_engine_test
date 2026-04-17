from __future__ import annotations

from datetime import datetime
from threading import RLock
from typing import List

from pydantic import BaseModel, Field


class TurnEnvelope(BaseModel):
    """Foundation-level turn transaction metadata."""

    raw_input: str = Field(default="")
    turn: int = Field(default=0)
    trace_id: int = Field(default=0)
    debug: dict = Field(default_factory=dict)
    world_version: int | None = Field(default=None)
    event_id: str | None = Field(default=None)

    @property
    def turn_id(self) -> int:
        return self.turn


class MemoryLogEvent(BaseModel):
    turn_id: int = Field(default=0)
    trace_id: int = Field(default=0)
    content: str = Field(default="")
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class ShortLogEvent(BaseModel):
    turn_id: int = Field(default=0)
    trace_id: int = Field(default=0)
    event: str = Field(default="")
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class EventLogger:
    """In-memory event logger for memory.log and shortLog baselines."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._memory_log: List[MemoryLogEvent] = []
        self._short_log: List[ShortLogEvent] = []

    def log_memory(self, event: MemoryLogEvent) -> None:
        with self._lock:
            self._memory_log.append(event)

    def log_short(self, event: ShortLogEvent) -> None:
        with self._lock:
            self._short_log.append(event)

    def get_memory_log(self) -> List[MemoryLogEvent]:
        with self._lock:
            return [entry.model_copy(deep=True) for entry in self._memory_log]

    def get_short_log(self) -> List[ShortLogEvent]:
        with self._lock:
            return [entry.model_copy(deep=True) for entry in self._short_log]
