from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Set

from src.agent.llm.service import LLMServiceBase, LLMServiceError, LLMValidationError
from src.agent.prompt.npc_performer_prompt import NPC_PERFORMER_SYSTEM_PROMPT
from src.data.model.agent_input import NpcPerformerAgentInput
from src.data.model.agent_output import (
    NpcPerformerAgentLlmOutput,
    NpcPerformerAgentOutput,
    NpcPerformerAgentSystemOutput,
    NpcPerformerPendingSideEffects,
)
from src.data.model.base import Goal, MemoryLogItem, ShortLogItem
from src.data.model.world_state import WorldState


class NpcPerformerAgent:
    """NPC 行为执行代理，负责生成行为结果，并在系统允许时回写目标与记忆。"""

    def __init__(
        self,
        llm_service: LLMServiceBase,
        world_state: WorldState,
        memory_turns: int = 15,
        shortlog_turns: int = 30,
        max_retries: int = 2,
    ) -> None:
        self.llm_service = llm_service
        self.world_state = world_state
        self.memory_turns = max(1, int(memory_turns))
        self.shortlog_turns = max(1, int(shortlog_turns))
        self.max_retries = max(0, int(max_retries))

    def run(self, *, agent_input: NpcPerformerAgentInput) -> NpcPerformerAgentOutput:
        """仅生成 NPC 行为结果，不在此处直接提交世界副作用。"""
        execution = agent_input.system_input.execution
        npc_id = agent_input.system_input.execution.debug.get("npc_id", agent_input.llm_input.world_info.id)
        actor = self.world_state.get_character(npc_id)
        user_payload = agent_input.llm_input.model_dump(mode="json")
        agent_memory_payload = user_payload.get("agent_memory")
        if isinstance(agent_memory_payload, dict):
            agent_memory_payload.pop("log", None)
            agent_memory_payload.pop("short_log", None)
            agent_memory_payload.pop("long_term_memory", None)

        available_attributes = [item.id for item in agent_input.llm_input.available_attributes if item.id]
        if not available_attributes:
            available_attributes = sorted(actor.attributes.keys())
        valid_character_ids = {item.id for item in agent_input.llm_input.valid_characters if item.id}
        if not valid_character_ids:
            valid_character_ids = set(self.world_state.get_snapshot().characters.keys())

        feedback: Optional[str] = None
        errors: List[str] = []
        llm_output: Optional[NpcPerformerAgentLlmOutput] = None

        for _ in range(self.max_retries + 1):
            try:
                llm_output = self.llm_service.call_llm_json(
                    agent_name="npc_performer",
                    system_prompt=NPC_PERFORMER_SYSTEM_PROMPT,
                    user_payload=user_payload,
                    output_model=NpcPerformerAgentLlmOutput,
                    retry_budget=0,
                    validation_feedback=feedback,
                )
            except LLMValidationError as exc:
                errors = [f"schema_error: {item.get('loc')} {item.get('msg')}" for item in exc.errors]
                feedback = self._build_validation_feedback(
                    errors=errors,
                    available_attributes=available_attributes,
                    valid_character_ids=valid_character_ids,
                    actor_id=npc_id,
                )
                continue
            except LLMServiceError as exc:
                errors = [f"llm_unavailable: {str(exc)}"]
                feedback = self._build_validation_feedback(
                    errors=errors,
                    available_attributes=available_attributes,
                    valid_character_ids=valid_character_ids,
                    actor_id=npc_id,
                )
                continue

            errors = self._validate_semantics(
                llm_output=llm_output,
                actor_id=npc_id,
                available_attributes=available_attributes,
                valid_character_ids=valid_character_ids,
            )
            if not errors:
                break
            feedback = self._build_validation_feedback(
                errors=errors,
                available_attributes=available_attributes,
                valid_character_ids=valid_character_ids,
                actor_id=npc_id,
            )

        if llm_output is None or errors:
            llm_output = NpcPerformerAgentLlmOutput.model_validate(
                {
                    "intent": "idle",
                    "action_text": "",
                    "routing_hint": None,
                    "attributes": [],
                    "against_char_id": [],
                    "difficulty": None,
                    "change_basic_goal": None,
                    "change_active_goal": None,
                }
            )

        output = NpcPerformerAgentOutput(
            llm_output=llm_output,
            system_output=NpcPerformerAgentSystemOutput(
                trace_id=execution.trace_id,
                turn_id=execution.turn_id,
                id=npc_id,
                pending_side_effects=self._build_pending_side_effects(
                    agent_input=agent_input,
                    llm_output=llm_output,
                    npc_id=npc_id,
                ),
            ),
        )
        return output

    def apply_side_effects(self, *, output: NpcPerformerAgentOutput) -> None:
        """在系统确认允许提交后，再把 performer 结果写回 NPC 目标与记忆。"""
        npc_id = output.system_output.id
        store = self.world_state.get_store_copy()
        character = store.characters[npc_id]
        turn_id = output.system_output.turn_id
        pending = output.system_output.pending_side_effects
        event_text = (pending.current_event or "").strip()
        timestamp = int(datetime.now().timestamp())

        character.memory.current_event = event_text or None
        if event_text and pending.append_short:
            character.memory.short.append(event_text)
            character.memory.short = character.memory.short[-self.memory_turns :]
        if event_text and pending.append_short_log:
            character.memory.short_log.append(ShortLogItem(turn=turn_id, event=event_text))
            character.memory.short_log = character.memory.short_log[-self.shortlog_turns :]
        if event_text and pending.append_log:
            character.memory.log.append(MemoryLogItem(turn=turn_id, content=event_text, timestamp=timestamp))

        character.goal = self._apply_goal_updates(
            character.goal,
            pending.next_base_goal,
            pending.next_active_goal,
        )
        self.world_state.commit_store(store=store)

    def _build_event_text(self, *, agent_input: NpcPerformerAgentInput, llm_output: NpcPerformerAgentLlmOutput, npc_id: str) -> str:
        extra_context = agent_input.llm_input.e4.extra_npc_context.get(npc_id)
        action_text = (llm_output.action_text or "").strip()
        if extra_context and action_text:
            return f"{extra_context} | {action_text}"
        return extra_context or action_text

    def _build_pending_side_effects(
        self,
        *,
        agent_input: NpcPerformerAgentInput,
        llm_output: NpcPerformerAgentLlmOutput,
        npc_id: str,
    ) -> NpcPerformerPendingSideEffects:
        event_text = self._build_event_text(agent_input=agent_input, llm_output=llm_output, npc_id=npc_id).strip()
        has_event = bool(event_text)
        return NpcPerformerPendingSideEffects(
            current_event=event_text,
            append_short=has_event,
            append_short_log=has_event,
            append_log=has_event,
            next_base_goal=llm_output.change_basic_goal,
            next_active_goal=llm_output.change_active_goal,
        )

    @staticmethod
    def _apply_goal_updates(goal: Goal, next_base_goal: Optional[str], next_active_goal: Optional[str]) -> Goal:
        updated_goal = goal.model_copy(deep=True)
        NpcPerformerAgent._update_goal_field(
            goal=updated_goal,
            field_name="base_goal",
            new_value=next_base_goal,
        )
        NpcPerformerAgent._update_goal_field(
            goal=updated_goal,
            field_name="active_goal",
            new_value=next_active_goal,
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

    @staticmethod
    def _validate_semantics(
        *,
        llm_output: NpcPerformerAgentLlmOutput,
        actor_id: str,
        available_attributes: List[str],
        valid_character_ids: Set[str],
    ) -> List[str]:
        errors: List[str] = []
        if llm_output.routing_hint not in {None, "num", "against"}:
            errors.append("routing_hint must be null/num/against")

        attrs = llm_output.attributes or []
        ids = llm_output.against_char_id or []

        if llm_output.routing_hint is None:
            if attrs:
                errors.append("routing_hint is null but attributes is not empty")
            if ids:
                errors.append("routing_hint is null but against_char_id is not empty")
            return errors

        if not attrs:
            errors.append("check routing requires attributes")
        for attr in attrs:
            if attr not in available_attributes:
                errors.append(f"invalid attribute: {attr}")

        for char_id in ids:
            if char_id not in valid_character_ids:
                errors.append(f"invalid char id: {char_id}")

        if llm_output.routing_hint == "num":
            if len(ids) > 1:
                errors.append("num routing should not include multiple against_char_id values")
            return errors

        if not ids:
            errors.append("against routing requires against_char_id")
            return errors

        if len(ids) < 2:
            errors.append("against routing requires at least 2 character ids")
        if len(set(ids)) != len(ids):
            errors.append("against routing contains duplicate character ids")
        if actor_id not in ids:
            errors.append("against routing must include actor id")
        if ids and ids[0] != actor_id:
            errors.append("against routing requires actor id as first against_char_id")
        return errors

    @staticmethod
    def _build_validation_feedback(
        *,
        errors: List[str],
        available_attributes: List[str],
        valid_character_ids: Set[str],
        actor_id: str,
    ) -> str:
        parts = list(errors)
        if available_attributes:
            parts.append(f"allowed attribute ids: {', '.join(sorted(available_attributes))}")
        if valid_character_ids:
            parts.append(f"valid character ids: {', '.join(sorted(valid_character_ids))}")
        parts.append(f"for against routing, first against_char_id must be actor id: {actor_id}")
        return " ; ".join(parts)
