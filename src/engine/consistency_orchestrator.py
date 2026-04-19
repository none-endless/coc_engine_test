from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, Optional


class ConsistencyOrchestrator:
    """Dedicated entry wrapper for consistency maintenance lifecycle."""

    def __init__(self, run_consistency_cycle_impl: Callable[..., Awaitable[Optional[Dict[str, Any]]]]) -> None:
        self._run_consistency_cycle_impl = run_consistency_cycle_impl

    async def run_consistency_cycle(self, *, turn_id: int, trace_id: int) -> Optional[Dict[str, Any]]:
        return await self._run_consistency_cycle_impl(turn_id=turn_id, trace_id=trace_id)
