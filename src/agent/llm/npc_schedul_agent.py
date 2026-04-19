from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Set, Tuple

from src.config.constants import DEFAULT_DEXTERITY_ATTRIBUTE_KEYS
from src.agent.llm.service import LLMServiceBase
from src.agent.prompt.npc_scheduler_prompt import NPC_SCHEDULER_SYSTEM_PROMPT
from src.data.model.agent_input import NpcSchedulerAgentInput
from src.data.model.agent_output import NpcSchedulerAgentLlmOutput, NpcSchedulerAgentOutput, NpcSchedulerAgentSystemOutput
from src.data.model.world_state import WorldState


class NpcSchedulerAgent:
    """LLM-driven scheduler for NPC side branch."""

    def __init__(
        self,
        llm_service: LLMServiceBase,
        world_state: WorldState,
        max_actions_per_turn: int = 3,
        cooldown_turns: int = 1,
        dexterity_attribute_keys: Optional[Iterable[str]] = None,
    ) -> None:
        self.llm_service = llm_service
        self.world_state = world_state
        self.max_actions_per_turn = max(1, int(max_actions_per_turn))
        self.cooldown_turns = max(0, int(cooldown_turns))
        self.dexterity_attribute_keys = {
            str(value).strip().lower()
            for value in (dexterity_attribute_keys or DEFAULT_DEXTERITY_ATTRIBUTE_KEYS)
            if str(value).strip()
        } or set(DEFAULT_DEXTERITY_ATTRIBUTE_KEYS)
        self._npc_last_scheduled_turn: Dict[str, int] = {}

    def run(self, *, agent_input: NpcSchedulerAgentInput) -> NpcSchedulerAgentOutput:
        execution = agent_input.system_input.execution
        user_payload = agent_input.llm_input.model_dump(mode="json")
        user_payload.pop("narrative_info", None)

        llm_output = self.llm_service.call_llm_json(
            agent_name="npc_scheduler",
            system_prompt=NPC_SCHEDULER_SYSTEM_PROMPT,
            user_payload=user_payload,
            output_model=NpcSchedulerAgentLlmOutput,
            retry_budget=0,
            validation_feedback=None,
        )
        normalized_output = self._normalize_schedule(
            llm_output=llm_output,
            turn_id=execution.turn_id,
        )
        return NpcSchedulerAgentOutput(
            llm_output=normalized_output,
            system_output=NpcSchedulerAgentSystemOutput(trace_id=execution.trace_id, turn_id=execution.turn_id),
        )

    def _normalize_schedule(self, *, llm_output: NpcSchedulerAgentLlmOutput, turn_id: int) -> NpcSchedulerAgentLlmOutput:
        """系统侧执行预算、冷却和敏捷排序，避免只依赖 LLM 自觉遵守规则。"""
        step_result = llm_output.step_result
        candidate_ids = self._collect_candidate_ids(
            scheduled_npc_ids=step_result.scheduled_npc_ids,
            extra_npc_context=step_result.extra_npc_context,
        )
        available_ids = [
            npc_id
            for npc_id in candidate_ids
            if self._npc_exists(npc_id) and self._is_schedule_available(npc_id=npc_id, turn_id=turn_id)
        ]
        sorted_ids = self._sort_npcs_by_dexterity(candidate_ids=available_ids)
        scheduled_ids = sorted_ids[: self.max_actions_per_turn]

        for npc_id in scheduled_ids:
            self._npc_last_scheduled_turn[npc_id] = turn_id

        filtered_context = {
            npc_id: step_result.extra_npc_context.get(npc_id)
            for npc_id in scheduled_ids
        }
        if candidate_ids and not scheduled_ids:
            step_result.summary = (
                f"{step_result.summary}（系统过滤后本回合未调度NPC：可能处于冷却或关键状态不足）"
            )
        llm_output.step_result.scheduled_npc_ids = scheduled_ids
        llm_output.step_result.extra_npc_context = filtered_context
        return llm_output

    @staticmethod
    def _collect_candidate_ids(*, scheduled_npc_ids: List[str], extra_npc_context: Dict[str, Optional[str]]) -> List[str]:
        seen: Set[str] = set()
        merged_ids: List[str] = []
        for npc_id in list(scheduled_npc_ids) + list(extra_npc_context.keys()):
            if not npc_id or npc_id in seen:
                continue
            seen.add(npc_id)
            merged_ids.append(npc_id)
        return merged_ids

    def _npc_exists(self, npc_id: str) -> bool:
        snapshot = self.world_state.get_snapshot()
        return npc_id in snapshot.characters

    def _is_schedule_available(self, *, npc_id: str, turn_id: int) -> bool:
        """过滤任意状态归零或仍在冷却中的 NPC。"""
        character = self.world_state.get_character(npc_id)
        if self._has_zero_status(character.status.values()):
            return False

        last_turn = self._npc_last_scheduled_turn.get(npc_id)
        if last_turn is None:
            return True
        return (turn_id - last_turn) > self.cooldown_turns

    def _sort_npcs_by_dexterity(self, *, candidate_ids: List[str]) -> List[str]:
        indexed_candidates: List[Tuple[int, str, int]] = []
        for index, npc_id in enumerate(candidate_ids):
            character = self.world_state.get_character(npc_id)
            indexed_candidates.append((index, npc_id, self._get_dexterity_value(character.attributes.values())))

        indexed_candidates.sort(key=lambda item: (-item[2], item[0]))
        return [npc_id for _, npc_id, _ in indexed_candidates]

    def _get_dexterity_value(self, attributes: Iterable[object]) -> int:
        for attribute in attributes:
            attr_id = str(getattr(attribute, "id", "")).strip().lower()
            attr_name = str(getattr(attribute, "name", "")).strip().lower()
            if attr_id in self.dexterity_attribute_keys or attr_name in self.dexterity_attribute_keys:
                return int(getattr(attribute, "value", 0))
        return 0

    @staticmethod
    def _has_zero_status(statuses: Iterable[object]) -> bool:
        for status in statuses:
            if int(getattr(status, "value", 0)) <= 0:
                return True
        return False
