from __future__ import annotations

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
