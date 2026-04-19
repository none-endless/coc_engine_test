from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, Optional

from src.data.model.input.agent_chain_input import E7CausalityChain


class TurnOrchestrator:
    """Thin orchestrator wrapper to isolate phase3 turn execution contract from Engine."""

    def __init__(self, run_phase3_turn_async_impl: Callable[..., Awaitable[Dict[str, Any]]]) -> None:
        self._run_phase3_turn_async_impl = run_phase3_turn_async_impl

    async def run_phase3_turn_async(
        self,
        *,
        raw_input: str,
        actor_id: str,
        turn_id: int,
        trace_id: int,
        causality_chain: Optional[E7CausalityChain],
    ) -> Dict[str, Any]:
        return await self._run_phase3_turn_async_impl(
            raw_input=raw_input,
            actor_id=actor_id,
            turn_id=turn_id,
            trace_id=trace_id,
            causality_chain=causality_chain,
        )
