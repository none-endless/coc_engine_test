from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional, TYPE_CHECKING

from src.data.model.agent_input import (
    AgentIdentity,
    ConsistencyAgentInput,
    ConsistencyAgentLlmInput,
    ConsistencyAgentSystemInput,
    ConsistencyDescriptionCandidate,
    ConsistencyKeyFactsCandidate,
    ConsistencyNarrationCandidate,
    SystemExecutionMeta,
)
from src.data.model.agent_output import ConsistencyAgentOutput, ConsistencySummaryKind
from src.data.model.base import MemoryLogItem, ShortLogItem
from src.data.model.input.agent_narrative_input import NarrativeEntry

if TYPE_CHECKING:
    from src.engine.engine import Engine


class ConsistencyOrchestrator:
    """Owns consistency lifecycle: candidate building, retry, apply, and block decision."""

    def __init__(self, engine: "Engine") -> None:
        self._engine = engine

    def build_consistency_input(self, *, turn_id: int, trace_id: int) -> Optional[ConsistencyAgentInput]:
        e = self._engine
        snapshot = e.world_state.get_snapshot()
        description_threshold = e._consistency_description_threshold
        shortlog_threshold = e._consistency_shortlog_threshold

        description_candidates: list[ConsistencyDescriptionCandidate] = []
        for bucket_values in (snapshot.maps, snapshot.characters, snapshot.items):
            for entity_id, payload in bucket_values.items():
                add_items = payload.description.add
                if len(add_items) > description_threshold:
                    add_entries = e._extract_snapshot_description_entries(add_items)
                    if not add_entries:
                        continue
                    public_entries = [
                        e._normalize_consistency_text(text)
                        for text in payload.description.public
                    ]
                    description_candidates.append(
                        ConsistencyDescriptionCandidate(
                            entity_id=entity_id,
                            public=[text for text in public_entries if text],
                            add=add_entries,
                        )
                    )

        key_facts_candidates: list[ConsistencyKeyFactsCandidate] = []
        for char_id, payload in snapshot.characters.items():
            short_log = payload.memory.short_log
            if len(short_log) > shortlog_threshold:
                short_log_entries = e._extract_snapshot_shortlog_events(short_log)
                if not short_log_entries:
                    continue
                key_facts_candidates.append(
                    ConsistencyKeyFactsCandidate(
                        character_id=char_id,
                        key_facts=[
                            e._normalize_consistency_text(item)
                            for item in payload.memory.key_facts
                            if e._normalize_consistency_text(item)
                        ],
                        short_log=short_log_entries,
                    )
                )

        narration_candidates: list[ConsistencyNarrationCandidate] = []
        for entry in e._narrative_info.recent:
            content = e._normalize_consistency_text(entry.content)
            if content:
                narration_candidates.append(ConsistencyNarrationCandidate(turn=entry.turn, content=content))

        if not narration_candidates:
            recent_changes_window = max(1, int(e.config.consistency.narration_fallback_recent_changes))
            fallback_narration = "；".join(
                e._normalize_consistency_text(item.summary)
                for item in e._recent_change_logs[-recent_changes_window:]
                if e._normalize_consistency_text(item.summary)
            )
            if fallback_narration:
                narration_candidates.append(ConsistencyNarrationCandidate(turn=turn_id, content=fallback_narration))

        consistency_config_json = e.config.model_dump(mode="json") if e.config.consistency.include_full_config_json else {}

        has_description_candidate = bool(description_candidates)
        has_key_facts_candidate = bool(key_facts_candidates)
        has_narrative_candidate = len(narration_candidates) >= e._consistency_min_narration_candidates
        if not has_description_candidate and not has_key_facts_candidate and not has_narrative_candidate:
            return None

        return ConsistencyAgentInput(
            identity=AgentIdentity(id="consistency", skill="maintain description public, key facts and narrative recent"),
            llm_input=ConsistencyAgentLlmInput(
                narration_candidates=narration_candidates,
                description_candidates=description_candidates,
                key_facts_candidates=key_facts_candidates,
                recent_change_logs=[item.model_copy(deep=True) for item in e._recent_change_logs],
                config_json=consistency_config_json,
            ),
            system_input=ConsistencyAgentSystemInput(
                execution=SystemExecutionMeta(
                    turn_id=turn_id,
                    trace_id=trace_id,
                    world_version=e.world_state.get_version(),
                    debug={
                        "branch": "consistency",
                        "description_merge_threshold": str(description_threshold),
                        "shortlog_merge_threshold": str(shortlog_threshold),
                        "description_candidates": str(len(description_candidates)),
                        "key_facts_candidates": str(len(key_facts_candidates)),
                        "narration_candidates": str(len(narration_candidates)),
                    },
                )
            ),
        )

    async def run_consistency_cycle(self, *, turn_id: int, trace_id: int) -> Optional[Dict[str, Any]]:
        e = self._engine
        if not e._consistency_enabled:
            return None
        if turn_id % e._consistency_trigger_interval != 0:
            return None

        agent_input = self.build_consistency_input(turn_id=turn_id, trace_id=trace_id)
        if agent_input is None:
            return {
                "triggered": True,
                "ok": True,
                "blocked": False,
                "retry_count": 0,
                "patch": {"llm_output": {"summary_items": [], "can_proceed": True, "system_message": ""}},
                "maintenance": {"skipped": True},
                "system_message": "",
                "error_history": [],
            }

        max_retry = int(e.config.system.max_retry_count)
        maintenance: Optional[Dict[str, Any]] = None

        def _validate_and_apply(output: ConsistencyAgentOutput) -> None:
            nonlocal maintenance
            maintenance = self.apply_consistency_changes(output, agent_input, turn_id)
            e._persist_world_snapshot()
            e._persist_narrative_info()

        retry_result = await asyncio.to_thread(
            e.consistency_agent.run_with_retry,
            agent_input=agent_input,
            max_retry=max_retry,
            patch_id_prefix=f"consistency-{turn_id}-{trace_id}",
            validate_output=_validate_and_apply,
        )
        final_output = retry_result.output

        if final_output is None:
            return {
                "triggered": True,
                "ok": False,
                "blocked": True,
                "retry_count": retry_result.retry_count,
                "patch": None,
                "maintenance": None,
                "system_message": "一致性维护重试耗尽，请稍后重试。",
                "error_history": retry_result.error_history,
            }

        if not final_output.llm_output.can_proceed:
            message = final_output.llm_output.system_message.strip() or e.config.system.fallback_error
            e._consistency_blocking_message = message
            return {
                "triggered": True,
                "ok": False,
                "blocked": True,
                "retry_count": retry_result.retry_count,
                "patch": final_output.model_dump(mode="json"),
                "maintenance": None,
                "system_message": message,
                "error_history": retry_result.error_history,
            }

        if retry_result.exhausted:
            return {
                "triggered": True,
                "ok": False,
                "blocked": True,
                "retry_count": retry_result.retry_count,
                "patch": final_output.model_dump(mode="json"),
                "maintenance": None,
                "system_message": "一致性维护重试耗尽，请稍后重试。",
                "error_history": retry_result.error_history,
            }

        return {
            "triggered": True,
            "ok": True,
            "blocked": False,
            "retry_count": retry_result.retry_count,
            "patch": final_output.model_dump(mode="json"),
            "maintenance": maintenance,
            "system_message": final_output.llm_output.system_message,
            "error_history": retry_result.error_history,
        }

    def apply_consistency_changes(
        self,
        output: ConsistencyAgentOutput,
        agent_input: ConsistencyAgentInput,
        turn_id: int,
    ) -> Dict[str, Any]:
        e = self._engine
        summary_items = list(output.llm_output.summary_items)
        if not summary_items:
            raise ValueError("consistency summary_items 不能为空")
        if summary_items[0].kind != ConsistencySummaryKind.NARRATION:
            raise ValueError("consistency summary_items 第一项必须是 narration")
        if any(item.kind == ConsistencySummaryKind.NARRATION for item in summary_items[1:]):
            raise ValueError("consistency narration 只能出现在第一项")

        narration_value = e._normalize_consistency_text(summary_items[0].value)
        if not narration_value:
            raise ValueError("consistency narration 不能为空")

        description_values = [
            e._normalize_consistency_text(item.value)
            for item in summary_items[1:]
            if item.kind == ConsistencySummaryKind.DESCRIPTION
        ]
        key_facts_values = [
            e._normalize_consistency_text(item.value)
            for item in summary_items[1:]
            if item.kind == ConsistencySummaryKind.KEY_FACTS
        ]

        expected_description_count = len(agent_input.llm_input.description_candidates)
        expected_key_facts_count = len(agent_input.llm_input.key_facts_candidates)
        if len(description_values) != expected_description_count:
            raise ValueError(
                f"consistency description 数量不匹配: expected={expected_description_count}, got={len(description_values)}"
            )
        if len(key_facts_values) != expected_key_facts_count:
            raise ValueError(
                f"consistency key_facts 数量不匹配: expected={expected_key_facts_count}, got={len(key_facts_values)}"
            )

        store = e.world_state.get_store_copy()

        for index, candidate in enumerate(agent_input.llm_input.description_candidates):
            compressed_description = description_values[index]
            if not compressed_description:
                raise ValueError(f"description 候选 {candidate.entity_id} 为空")
            entity = self.get_consistency_entity(store=store, entity_id=candidate.entity_id)
            entity.description.public = [compressed_description]
            entity.description.add = []

        for index, candidate in enumerate(agent_input.llm_input.key_facts_candidates):
            compressed_key_fact = key_facts_values[index]
            if not compressed_key_fact:
                raise ValueError(f"key_facts 候选 {candidate.character_id} 为空")
            character = self.get_consistency_entity(store=store, entity_id=candidate.character_id)
            character.memory.key_facts = [compressed_key_fact]
            character.memory.short_log = []

        self.normalize_npc_memory_windows(store=store)
        e.world_state.commit_store(store=store)
        e._narrative_info.recent = [NarrativeEntry(turn=turn_id, content=narration_value)]

        return {
            "world_changes": expected_description_count + expected_key_facts_count,
            "narrative_changes": 1,
            "description_entities": expected_description_count,
            "key_facts_entities": expected_key_facts_count,
            "cleared_description_add": expected_description_count,
            "cleared_short_log": expected_key_facts_count,
        }

    @staticmethod
    def get_consistency_entity(*, store, entity_id: str):
        if entity_id.startswith("map-"):
            entity = store.maps.get(entity_id)
            if entity is None:
                raise ValueError(f"consistency map not found: {entity_id}")
            return entity
        if entity_id.startswith("char-"):
            entity = store.characters.get(entity_id)
            if entity is None:
                raise ValueError(f"consistency character not found: {entity_id}")
            return entity
        if entity_id.startswith("item-"):
            entity = store.items.get(entity_id)
            if entity is None:
                raise ValueError(f"consistency item not found: {entity_id}")
            return entity
        raise ValueError(f"unsupported consistency entity id: {entity_id}")

    def normalize_npc_memory_windows(self, *, store) -> None:
        e = self._engine
        for character in store.characters.values():
            memory = character.memory
            memory.short = [item for item in memory.short if (item or "").strip()][-e._npc_memory_turn_limit :]
            memory.short_log = [
                ShortLogItem.model_validate(item.model_dump(mode="json") if hasattr(item, "model_dump") else item)
                for item in memory.short_log
                if (getattr(item, "event", "") or "").strip()
            ][-e._npc_shortlog_turn_limit :]
            memory.log = [
                MemoryLogItem.model_validate(item.model_dump(mode="json") if hasattr(item, "model_dump") else item)
                for item in memory.log
                if (getattr(item, "content", "") or "").strip()
            ]
            if memory.current_event:
                memory.current_event = memory.current_event.strip()
