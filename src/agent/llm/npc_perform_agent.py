from __future__ import annotations

from datetime import datetime
from typing import Optional

from src.agent.llm.service import LLMServiceBase
from src.agent.prompt.npc_performer_prompt import NPC_PERFORMER_SYSTEM_PROMPT
from src.data.model.agent_input import NpcPerformerAgentInput
from src.data.model.agent_output import NpcPerformerAgentLlmOutput, NpcPerformerAgentOutput, NpcPerformerAgentSystemOutput
from src.data.model.base import Goal, MemoryLogItem, ShortLogItem
from src.data.model.world_state import WorldState


class NpcPerformerAgent:
    """LLM-driven NPC performer with system-side memory and goal maintenance."""

    def __init__(
        self,
        llm_service: LLMServiceBase,
        world_state: WorldState,
        memory_turns: int = 15,
        shortlog_turns: int = 30,
    ) -> None:
        self.llm_service = llm_service
        self.world_state = world_state
        self.memory_turns = max(1, int(memory_turns))
        self.shortlog_turns = max(1, int(shortlog_turns))

    def run(self, *, agent_input: NpcPerformerAgentInput) -> NpcPerformerAgentOutput:
        execution = agent_input.system_input.execution
        npc_id = agent_input.system_input.execution.debug.get("npc_id", agent_input.llm_input.world_info.id)
        llm_output = self.llm_service.call_llm_json(
            agent_name="npc_performer",
            system_prompt=NPC_PERFORMER_SYSTEM_PROMPT,
            user_payload=agent_input.llm_input.model_dump(mode="json"),
            output_model=NpcPerformerAgentLlmOutput,
            retry_budget=0,
            validation_feedback=None,
        )
        output = NpcPerformerAgentOutput(
            llm_output=llm_output,
            system_output=NpcPerformerAgentSystemOutput(
                trace_id=execution.trace_id,
                turn_id=execution.turn_id,
                id=npc_id,
            ),
        )
        self._apply_side_effects(agent_input=agent_input, output=output)
        return output

    def _apply_side_effects(self, *, agent_input: NpcPerformerAgentInput, output: NpcPerformerAgentOutput) -> None:
        """将 performer 结果写回 NPC 的目标系统和短期记忆。"""
        npc_id = output.system_output.id
        store = self.world_state.get_store_copy()
        character = store.characters[npc_id]
        turn_id = output.system_output.turn_id
        event_text = self._build_event_text(agent_input=agent_input, output=output)
        timestamp = int(datetime.now().timestamp())

        character.memory.current_event = event_text
        if event_text:
            character.memory.short.append(event_text)
            character.memory.short = character.memory.short[-self.memory_turns :]
            character.memory.short_log.append(ShortLogItem(turn=turn_id, event=event_text))
            character.memory.short_log = character.memory.short_log[-self.shortlog_turns :]
            character.memory.log.append(MemoryLogItem(turn=turn_id, content=event_text, timestamp=timestamp))

        character.goal = self._apply_goal_updates(character.goal, output.llm_output)
        self.world_state.commit_store(store=store)

    def _build_event_text(self, *, agent_input: NpcPerformerAgentInput, output: NpcPerformerAgentOutput) -> str:
        extra_context = agent_input.llm_input.e4.extra_npc_context.get(output.system_output.id)
        action_text = (output.llm_output.action_text or "").strip()
        if extra_context and action_text:
            return f"{extra_context} | {action_text}"
        return extra_context or action_text

    @staticmethod
    def _apply_goal_updates(goal: Goal, llm_output: NpcPerformerAgentLlmOutput) -> Goal:
        updated_goal = goal.model_copy(deep=True)
        NpcPerformerAgent._update_goal_field(
            goal=updated_goal,
            field_name="base_goal",
            new_value=llm_output.change_basic_goal,
        )
        NpcPerformerAgent._update_goal_field(
            goal=updated_goal,
            field_name="active_goal",
            new_value=llm_output.change_active_goal,
        )
        return updated_goal

    @staticmethod
    def _update_goal_field(*, goal: Goal, field_name: str, new_value: Optional[str]) -> None:
        if new_value is None:
            return
        normalized_value = new_value.strip()
        if not normalized_value:
            return

        current_value = getattr(goal, field_name)
        if current_value and current_value != normalized_value:
            goal.goal_history.append(current_value)
        setattr(goal, field_name, normalized_value)
