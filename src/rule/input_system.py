from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict

from src.data.model.infra import TurnEnvelope

from .rule_system import RuleSystem


@dataclass
class InputRouteResult:
    route: str
    payload: Dict[str, Any]
    envelope: TurnEnvelope

class InputSystem:
    def __init__(self, rule_system: RuleSystem, dm_handler: Callable[[TurnEnvelope], Dict[str, Any]]) -> None:
        self.rule_system = rule_system
        self.dm_handler = dm_handler

    def dispatch(
        self,
        raw_input: str,
        actor_id: str,
        turn: int,
        trace_id: int,
        world_version: int,
    ) -> InputRouteResult:
        envelope = TurnEnvelope(
            raw_input=raw_input,
            turn=turn,
            trace_id=trace_id,
            world_version=world_version,
            event_id=f"turn-{turn}-trace-{trace_id}",
            debug={"actor_id": actor_id},
        )

        normalized = raw_input.strip()
        if normalized.startswith("\\"):
            if normalized.lower() not in {"\\look", "\\inventory"}:
                raise ValueError(f"unsupported meta command: {normalized}")
            payload = self.rule_system.run_meta_command(actor_id=actor_id, command=normalized)
            return InputRouteResult(route="rule_system_meta", payload=payload, envelope=envelope)

        payload = self.dm_handler(envelope)
        return InputRouteResult(route="dm_agent", payload=payload, envelope=envelope)
