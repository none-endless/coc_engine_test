from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple

from src.agent.llm.evolution_agent import EvolutionAgent, EvolutionResult
from src.agent.llm.input_agent import DMAgent, DmAnalyzeResult
from src.agent.llm.consistency_agent import ConsistencyAgent
from src.agent.llm.merger_agent import MergerAgent
from src.agent.llm.narrative_agent import NarrativeAgent
from src.agent.llm.npc_perform_agent import NpcPerformerAgent
from src.agent.llm.npc_schedul_agent import NpcSchedulerAgent
from src.agent.llm.service import LLMServiceBase, LLMServiceError
from src.agent.llm.statechange_agent import StateChangeAgent
from src.config.loader import ConfigLoader
from src.data.model.agent_input import (
    AgentIdentity,
    AvailableAttributeRef,
    AvailableCharacterRef,
    ConsistencyDescriptionCandidate,
    ConsistencyAgentInput,
    ConsistencyAgentLlmInput,
    ConsistencyAgentSystemInput,
    ConsistencyKeyFactsCandidate,
    ConsistencyNarrationCandidate,
    ConsistencyRecentChangeLog,
    DmAgentInput,
    DmAgentLlmInput,
    DmAgentSystemInput,
    E1LlmView,
    E3LlmView,
    E4EvolutionLlmView,
    E7LlmView,
    EvolutionAgentInput,
    EvolutionAgentLlmInput,
    EvolutionAgentSystemInput,
    MergerAgentInput,
    MergerAgentLlmInput,
    MergerAgentSystemInput,
    NarrativeAgentInput,
    NarrativeAgentLlmInput,
    NarrativeAgentSystemInput,
    NpcPerformerAgentInput,
    NpcPerformerAgentLlmInput,
    NpcPerformerAgentSystemInput,
    NpcSchedulerAgentInput,
    NpcSchedulerAgentLlmInput,
    NpcSchedulerAgentSystemInput,
    StateAgentInput,
    StateAgentLlmInput,
    StateAgentSystemInput,
    StateErrorFeedback,
    SystemExecutionMeta,
    SystemRetryControl,
)
from src.data.model.agent_output import (
    CocCheckResult,
    ConsistencyAgentOutput,
    ConsistencySummaryKind,
    MergerAgentOutput,
    NarrativeAgentOutput,
    NpcPerformerAgentOutput,
    NpcPerformerChainResult,
    NpcSchedulerAgentOutput,
    StateAgentOutput,
)
from src.data.model.base import MemoryLogItem, ShortLogItem
from src.data.model.input.agent_chain_input import (
    DmAgentChainInput,
    E1InputInfo,
    E3RuleResult,
    E4EvolutionStepResult,
    E4SchedulerStepResult,
    E7CausalityChain,
    EvolutionAgentChainInput,
    FallbackError,
    MergerAgentChainInput,
    NarrativeAgentChainInput,
    NpcPerformerAgentChainInput,
    NpcSchedulerAgentChainInput,
    StateChangeAgentChainInput,
)
from src.data.model.input.agent_memory_input import DmMemory
from src.data.model.input.agent_narrative_input import NarrativeEntry, NarrativeInfo
from src.data.model.world_state import WorldState
from src.engine.bootstrap_validation import validate_required_dexterity
from src.interface.narrative_stream_interface import NarrativeStreamInterface
from src.rule.input_system import InputSystem
from src.rule.rule_system import RuleSystem
from src.rule.state_patch import StatePatchError, StatePatchRuntime
from src.storage.sqlite_narrative_repository import SqliteNarrativeRepository
from src.storage.sqlite_world_snapshot_repository import SqliteWorldSnapshotRepository
from src.utils.agent_io_logger import make_io_record
from src.utils.world_provider import WorldDataProvider


EngineMode = Literal["phase2", "phase3", "phase4"]


class Engine:
    """统一引擎入口，负责 phase2/3/4 的主链路、双真值池和并发分支协调。"""

    def __init__(
        self,
        world_state: WorldState,
        mode: EngineMode = "phase3",
        dm_max_retries: int = 2,
        llm_service: Optional[LLMServiceBase] = None,
        io_logger=None,
        config_path: str = "config/config.yaml",
        enable_persistence: bool = False,
    ) -> None:
        self.world_state = world_state
        validate_required_dexterity(world_state)
        self.mode = mode
        self.rule_system = RuleSystem(world_state=world_state)
        self.world_provider = WorldDataProvider(world_state=world_state)

        if llm_service is None:
            cfg = ConfigLoader.load(config_path=config_path)
            llm_service = LLMServiceBase(config=cfg, io_recorder=io_logger)
        elif io_logger is not None and hasattr(llm_service, "io_recorder") and getattr(llm_service, "io_recorder", None) is None:
            setattr(llm_service, "io_recorder", io_logger)

        self.dm_agent = DMAgent(llm_service=llm_service, max_retries=dm_max_retries)
        self.evolution_agent = EvolutionAgent(llm_service=llm_service)
        self._current_actor_id = ""
        self._routing_logs: List[Dict[str, Any]] = []
        self._io_logger = io_logger
        self._narrative_info = NarrativeInfo()

        self.input_system = InputSystem(rule_system=self.rule_system, dm_handler=self._dm_handler)

        cfg = getattr(self.dm_agent.llm_service, "config", None)
        if cfg is None:
            cfg = ConfigLoader.load(config_path=config_path)
        self.config = cfg
        self._enable_persistence = bool(enable_persistence)
        self._dm_memory = DmMemory(memory_turns=self.config.agent.dm.memory_turns)
        self._world_snapshot_repository = self._build_world_snapshot_repository()
        self._narrative_repository = self._build_narrative_repository()

        self.state_agent = StateChangeAgent(llm_service=self.dm_agent.llm_service)
        self.consistency_agent = ConsistencyAgent(llm_service=self.dm_agent.llm_service)
        self.npc_scheduler_agent = NpcSchedulerAgent(
            llm_service=self.dm_agent.llm_service,
            world_state=self.world_state,
            max_actions_per_turn=int(self.config.agent.npc.max_actions_per_turn),
            cooldown_turns=int(self.config.agent.npc.cooldown_turns),
        )
        self.npc_performer_agent = NpcPerformerAgent(
            llm_service=self.dm_agent.llm_service,
            world_state=self.world_state,
            memory_turns=int(self.config.agent.npc.memory_turns),
            shortlog_turns=int(self.config.agent.npc.shortlog_turns),
        )
        self.narrative_agent = NarrativeAgent(llm_service=self.dm_agent.llm_service)
        self.merger_agent = MergerAgent(llm_service=self.dm_agent.llm_service)

        self.state_patch_runtime = StatePatchRuntime(world_state=self.world_state)
        self._state_commit_lock = asyncio.Lock()
        self._narrative_recent_limit = int(self.config.agent.narrative.recent_turns)
        self._npc_memory_turn_limit = int(self.config.agent.npc.memory_turns)
        self._npc_shortlog_turn_limit = int(self.config.agent.npc.shortlog_turns)
        self._consistency_enabled = bool(self.config.consistency.enabled)
        # 兼容旧配置键：description.add_interval / description.merge_threshold / agent.npc.shortlog_merge_threshold。
        legacy_trigger_interval = max(1, int(self.config.description.add_interval))
        configured_trigger_interval = max(1, int(self.config.consistency.trigger_interval_turns))
        self._consistency_trigger_interval = (
            legacy_trigger_interval
            if configured_trigger_interval == 10 and legacy_trigger_interval != 10
            else configured_trigger_interval
        )

        legacy_description_threshold = max(0, int(self.config.description.merge_threshold))
        configured_description_threshold = max(0, int(self.config.consistency.description_add_threshold))
        self._consistency_description_threshold = (
            legacy_description_threshold
            if configured_description_threshold == 3 and legacy_description_threshold != 3
            else configured_description_threshold
        )

        legacy_shortlog_threshold = max(0, int(self.config.agent.npc.shortlog_merge_threshold))
        configured_shortlog_threshold = max(0, int(self.config.consistency.shortlog_threshold))
        self._consistency_shortlog_threshold = (
            legacy_shortlog_threshold
            if configured_shortlog_threshold == 5 and legacy_shortlog_threshold != 5
            else configured_shortlog_threshold
        )
        self._consistency_min_narration_candidates = max(1, int(self.config.consistency.min_narration_candidates))
        self._recent_change_logs: List[ConsistencyRecentChangeLog] = []
        self._consistency_blocking_message: Optional[str] = None
        self._narrative_event_listener: Optional[Callable[[Dict[str, Any]], None]] = None
        self._restore_narrative_info_from_storage()
        self._persist_world_snapshot()

    def set_narrative_event_listener(self, listener: Optional[Callable[[Dict[str, Any]], None]]) -> None:
        """Register a callback to receive realtime narrative stream events."""
        self._narrative_event_listener = listener

    def _emit_narrative_event(self, event: Dict[str, Any]) -> None:
        listener = self._narrative_event_listener
        if not callable(listener):
            return
        try:
            listener(event)
        except Exception:
            # Event bridge failures must never break the turn pipeline.
            return

    def _build_world_snapshot_repository(self) -> Optional[SqliteWorldSnapshotRepository]:
        """按配置创建世界真值快照仓储；未配置时返回 None。"""
        if not self._enable_persistence:
            return None
        sqlite_path = str(getattr(self.config.storage.world, "sqlite_path", "")).strip()
        if not sqlite_path:
            return None
        return SqliteWorldSnapshotRepository(sqlite_path=sqlite_path)

    def _build_narrative_repository(self) -> Optional[SqliteNarrativeRepository]:
        """按配置创建叙事真值仓储；未配置时返回 None。"""
        if not self._enable_persistence:
            return None
        sqlite_path = str(getattr(self.config.storage.narrative, "sqlite_path", "")).strip()
        if not sqlite_path:
            return None
        return SqliteNarrativeRepository(sqlite_path=sqlite_path)

    def _restore_narrative_info_from_storage(self) -> None:
        """启动时从叙事仓储恢复 NarrativeInfo，保证 narrative truth 可跨进程保留。"""
        if self._narrative_repository is None:
            return
        self._narrative_info = self._narrative_repository.load()

    def _persist_narrative_info(self) -> None:
        """把当前 NarrativeInfo 同步写入独立 SQLite 仓储。"""
        if self._narrative_repository is None:
            return
        self._narrative_repository.save(self._narrative_info)

    def _persist_world_snapshot(self) -> None:
        """把当前世界快照写入独立 world snapshot SQLite 仓储。"""
        if self._world_snapshot_repository is None:
            return
        self._world_snapshot_repository.save_snapshot(self.world_state.get_snapshot())

    def _build_consistency_blocked_event(self, *, turn_id: int, trace_id: int) -> Dict[str, Any]:
        """在一致性阻断生效时返回统一降级事件。"""
        message = self._consistency_blocking_message or self.config.system.fallback_error
        event = {
            "route": "consistency_blocked",
            "turn_id": turn_id,
            "trace_id": trace_id,
            "message": message,
            "terminated": True,
        }
        self._routing_logs.append(event)
        self._record_io(
            kind="turn_result",
            agent_name="engine",
            input_data={"route": "consistency_blocked", "turn_id": turn_id, "trace_id": trace_id},
            output_data=event,
        )
        return event

    def _append_recent_change_log(self, *, turn_id: int, route: str, summary: str) -> None:
        """维护一致性代理消费的最近变更日志窗口。"""
        normalized_summary = (summary or "").strip()
        if not normalized_summary:
            return
        self._recent_change_logs.append(
            ConsistencyRecentChangeLog(
                turn_id=turn_id,
                route=route,
                summary=normalized_summary,
            )
        )
        self._recent_change_logs = self._recent_change_logs[-self._npc_shortlog_turn_limit :]

    @staticmethod
    def _normalize_consistency_text(text: Any) -> str:
        """把一致性输入输出中的文本规整为非空字符串。"""
        return str(text or "").strip()

    def _extract_snapshot_description_entries(self, add_items: List[Any]) -> List[str]:
        """从世界快照的 description.add 里提取文本内容。"""
        entries: List[str] = []
        for item in add_items:
            if isinstance(item, dict):
                content = self._normalize_consistency_text(item.get("content", ""))
            else:
                content = self._normalize_consistency_text(item)
            if content:
                entries.append(content)
        return entries

    def _extract_snapshot_shortlog_events(self, short_log_items: List[Any]) -> List[str]:
        """从世界快照的 memory.short_log 里提取事件文本。"""
        entries: List[str] = []
        for item in short_log_items:
            if isinstance(item, dict):
                event = self._normalize_consistency_text(item.get("event", ""))
            else:
                event = self._normalize_consistency_text(getattr(item, "event", ""))
            if event:
                entries.append(event)
        return entries

    def _build_consistency_input(self, *, turn_id: int, trace_id: int) -> Optional[ConsistencyAgentInput]:
        """构建一致性维护输入：系统自动收集 narration/description/key_facts 三类候选。"""
        snapshot = self.world_state.get_snapshot()
        description_threshold = self._consistency_description_threshold
        shortlog_threshold = self._consistency_shortlog_threshold

        description_candidates: List[ConsistencyDescriptionCandidate] = []
        for bucket in ("maps", "characters", "items"):
            for entity_id, payload in snapshot.get(bucket, {}).items():
                add_items = payload.get("description", {}).get("add", [])
                if len(add_items) > description_threshold:
                    add_entries = self._extract_snapshot_description_entries(add_items)
                    if not add_entries:
                        continue
                    public_entries = [
                        self._normalize_consistency_text(text)
                        for text in payload.get("description", {}).get("public", [])
                    ]
                    description_candidates.append(
                        ConsistencyDescriptionCandidate(
                            entity_id=entity_id,
                            public=[text for text in public_entries if text],
                            add=add_entries,
                        )
                    )

        key_facts_candidates: List[ConsistencyKeyFactsCandidate] = []
        for char_id, payload in snapshot.get("characters", {}).items():
            short_log = payload.get("memory", {}).get("short_log", [])
            if len(short_log) > shortlog_threshold:
                short_log_entries = self._extract_snapshot_shortlog_events(short_log)
                if not short_log_entries:
                    continue
                key_facts_candidates.append(
                    ConsistencyKeyFactsCandidate(
                        character_id=char_id,
                        key_facts=[
                            self._normalize_consistency_text(item)
                            for item in payload.get("memory", {}).get("key_facts", [])
                            if self._normalize_consistency_text(item)
                        ],
                        short_log=short_log_entries,
                    )
                )

        narration_candidates: List[ConsistencyNarrationCandidate] = []
        for entry in self._narrative_info.recent:
            content = self._normalize_consistency_text(entry.content)
            if content:
                narration_candidates.append(ConsistencyNarrationCandidate(turn=entry.turn, content=content))
        if not narration_candidates:
            recent_changes_window = max(1, int(self.config.consistency.narration_fallback_recent_changes))
            fallback_narration = "；".join(
                self._normalize_consistency_text(item.summary)
                for item in self._recent_change_logs[-recent_changes_window:]
                if self._normalize_consistency_text(item.summary)
            )
            if fallback_narration:
                narration_candidates.append(ConsistencyNarrationCandidate(turn=turn_id, content=fallback_narration))

        consistency_config_json = self.config.model_dump(mode="json") if self.config.consistency.include_full_config_json else {}

        has_description_candidate = bool(description_candidates)
        has_key_facts_candidate = bool(key_facts_candidates)
        has_narrative_candidate = len(narration_candidates) >= self._consistency_min_narration_candidates
        if not has_description_candidate and not has_key_facts_candidate and not has_narrative_candidate:
            return None

        return ConsistencyAgentInput(
            identity=AgentIdentity(id="consistency", skill="maintain description public, key facts and narrative recent"),
            llm_input=ConsistencyAgentLlmInput(
                narration_candidates=narration_candidates,
                description_candidates=description_candidates,
                key_facts_candidates=key_facts_candidates,
                recent_change_logs=[item.model_copy(deep=True) for item in self._recent_change_logs],
                config_json=consistency_config_json,
            ),
            system_input=ConsistencyAgentSystemInput(
                execution=SystemExecutionMeta(
                    turn_id=turn_id,
                    trace_id=trace_id,
                    world_version=self.world_state.get_version(),
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

    async def _run_consistency_cycle(self, *, turn_id: int, trace_id: int) -> Optional[Dict[str, Any]]:
        """执行一致性维护回合：调用 LLM 压缩文本并由系统自动写回与清空缓冲。"""
        if not self._consistency_enabled:
            return None
        if turn_id % self._consistency_trigger_interval != 0:
            return None

        agent_input = self._build_consistency_input(turn_id=turn_id, trace_id=trace_id)
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

        validation_feedback: Optional[Dict[str, Any]] = None
        error_history: List[Dict[str, Any]] = []
        max_retry = int(self.config.system.max_retry_count)
        final_output: Optional[ConsistencyAgentOutput] = None

        for retry_seq in range(max_retry + 1):
            try:
                output = await asyncio.to_thread(
                    self.consistency_agent.run,
                    agent_input=agent_input,
                    retry_seq=retry_seq,
                    patch_id=f"consistency-{turn_id}-{trace_id}-{retry_seq}",
                    validation_feedback=validation_feedback,
                )
            except LLMServiceError as exc:
                message = f"一致性维护服务不可用: {str(exc)}"
                validation_feedback = {"message": message}
                error_history.append(
                    {
                        "message": message,
                        "retry_seq": retry_seq,
                        "error_type": "llm_service_error",
                    }
                )
                continue
            final_output = output

            if not output.llm_output.can_proceed:
                message = output.llm_output.system_message.strip() or self.config.system.fallback_error
                self._consistency_blocking_message = message
                return {
                    "triggered": True,
                    "ok": False,
                    "blocked": True,
                    "retry_count": retry_seq,
                    "patch": output.model_dump(mode="json"),
                    "maintenance": None,
                    "system_message": message,
                    "error_history": error_history,
                }

            try:
                maintenance = await asyncio.to_thread(
                    self._apply_consistency_changes,
                    output,
                    agent_input,
                    turn_id,
                )
                self._persist_world_snapshot()
                self._persist_narrative_info()
                return {
                    "triggered": True,
                    "ok": True,
                    "blocked": False,
                    "retry_count": retry_seq,
                    "patch": output.model_dump(mode="json"),
                    "maintenance": maintenance,
                    "system_message": output.llm_output.system_message,
                    "error_history": error_history,
                }
            except ValueError as exc:
                validation_feedback = {"message": str(exc)}
                error_history.append({"message": str(exc), "retry_seq": retry_seq})

        return {
            "triggered": True,
            "ok": False,
            "blocked": True,
            "retry_count": max_retry,
            "patch": final_output.model_dump(mode="json") if final_output is not None else None,
            "maintenance": None,
            "system_message": "一致性维护重试耗尽，请稍后重试。",
            "error_history": error_history,
        }

    def _apply_consistency_changes(
        self,
        output: ConsistencyAgentOutput,
        agent_input: ConsistencyAgentInput,
        turn_id: int,
    ) -> Dict[str, Any]:
        """应用一致性压缩结果，并由系统自动清空 description.add、short_log 与 recent。"""
        summary_items = list(output.llm_output.summary_items)
        if not summary_items:
            raise ValueError("consistency summary_items 不能为空")
        if summary_items[0].kind != ConsistencySummaryKind.NARRATION:
            raise ValueError("consistency summary_items 第一项必须是 narration")
        if any(item.kind == ConsistencySummaryKind.NARRATION for item in summary_items[1:]):
            raise ValueError("consistency narration 只能出现在第一项")

        narration_value = self._normalize_consistency_text(summary_items[0].value)
        if not narration_value:
            raise ValueError("consistency narration 不能为空")

        description_values = [
            self._normalize_consistency_text(item.value)
            for item in summary_items[1:]
            if item.kind == ConsistencySummaryKind.DESCRIPTION
        ]
        key_facts_values = [
            self._normalize_consistency_text(item.value)
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

        store = self.world_state.get_store_copy()

        for index, candidate in enumerate(agent_input.llm_input.description_candidates):
            compressed_description = description_values[index]
            if not compressed_description:
                raise ValueError(f"description 候选 {candidate.entity_id} 为空")
            entity = self._get_consistency_entity(store=store, entity_id=candidate.entity_id)
            entity.description.public = [compressed_description]
            entity.description.add = []

        for index, candidate in enumerate(agent_input.llm_input.key_facts_candidates):
            compressed_key_fact = key_facts_values[index]
            if not compressed_key_fact:
                raise ValueError(f"key_facts 候选 {candidate.character_id} 为空")
            character = self._get_consistency_entity(store=store, entity_id=candidate.character_id)
            character.memory.key_facts = [compressed_key_fact]
            character.memory.short_log = []

        self._normalize_npc_memory_windows(store=store)
        self.world_state.commit_store(store=store)
        self._narrative_info.recent = [NarrativeEntry(turn=turn_id, content=narration_value)]

        return {
            "world_changes": expected_description_count + expected_key_facts_count,
            "narrative_changes": 1,
            "description_entities": expected_description_count,
            "key_facts_entities": expected_key_facts_count,
            "cleared_description_add": expected_description_count,
            "cleared_short_log": expected_key_facts_count,
        }

    @staticmethod
    def _get_consistency_entity(*, store, entity_id: str):
        """按实体 ID 解析一致性维护目标实体。"""
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

    def _normalize_npc_memory_windows(self, *, store) -> None:
        """???????????????????????? key_facts?"""
        for character in store.characters.values():
            memory = character.memory
            memory.short = [item for item in memory.short if (item or "").strip()][-self._npc_memory_turn_limit :]
            memory.short_log = [
                ShortLogItem.model_validate(item.model_dump(mode="json") if hasattr(item, "model_dump") else item)
                for item in memory.short_log
                if (getattr(item, "event", "") or "").strip()
            ][-self._npc_shortlog_turn_limit :]
            memory.log = [
                MemoryLogItem.model_validate(item.model_dump(mode="json") if hasattr(item, "model_dump") else item)
                for item in memory.log
                if (getattr(item, "content", "") or "").strip()
            ]
            if memory.current_event:
                memory.current_event = memory.current_event.strip()

    def _dm_handler(self, envelope) -> Dict[str, Any]:
        chain_e1 = E1InputInfo(
            turn_id=envelope.turn,
            trace_id=envelope.trace_id,
            world_version=envelope.world_version,
            event_id=envelope.event_id,
            source_id=self._current_actor_id,
            raw_text=envelope.raw_input,
            metadata=envelope.debug,
        )

        actor = self.world_state.get_character(self._current_actor_id)
        views = self.world_provider.precompute_all_views(current_map_id=actor.location, turn=envelope.turn)

        dm_input = DmAgentInput(
            identity=AgentIdentity(id="dmagent", skill="parse user intent"),
            llm_input=DmAgentLlmInput(
                e1=E1LlmView(raw_text=envelope.raw_input, source_id=self._current_actor_id),
                world_info=views.dm_view,
                narrative_info=self._narrative_info,
                agent_memory=self._dm_memory,
                available_attributes=[
                    AvailableAttributeRef(id=attr_id, name=attr.name)
                    for attr_id, attr in actor.attributes.items()
                ],
                valid_characters=[
                    AvailableCharacterRef(id=char_id, name=self.world_state.get_character(char_id).name)
                    for char_id in sorted(self.world_state.get_snapshot().get("characters", {}).keys())
                ],
            ),
            system_input=DmAgentSystemInput(
                chain_raw=DmAgentChainInput(e1=chain_e1),
                execution=SystemExecutionMeta(
                    turn_id=envelope.turn,
                    trace_id=envelope.trace_id,
                    world_version=envelope.world_version,
                    event_id=envelope.event_id,
                    debug={k: str(v) for k, v in envelope.debug.items()},
                ),
            ),
        )

        available_attrs = sorted(actor.attributes.keys())
        valid_ids = set(self.world_state.get_snapshot().get("characters", {}).keys())
        analyzed = self.dm_agent.run(
            agent_input=dm_input,
            available_attributes=available_attrs,
            valid_character_ids=valid_ids,
        )
        self._update_dm_memory(
            turn_id=envelope.turn,
            actor_id=self._current_actor_id,
            raw_input=envelope.raw_input,
            dm_result=analyzed,
        )
        self._record_io(
            kind="agent_io",
            agent_name="dmagent",
            input_data=dm_input,
            output_data=analyzed.output,
            extra={"retries": analyzed.retries, "validation_errors": analyzed.validation_errors},
        )
        return analyzed.model_dump(mode="json")

    def run_turn(
        self,
        raw_input: str,
        actor_id: str,
        turn_id: int,
        trace_id: int,
        causality_chain: Optional[E7CausalityChain] = None,
    ) -> Dict[str, Any]:
        normalized_chain = self._normalize_causality_chain(causality_chain)
        if self._consistency_blocking_message is not None:
            return self._build_consistency_blocked_event(turn_id=turn_id, trace_id=trace_id)
        if self.mode == "phase2":
            return self._run_phase2_turn(
                raw_input=raw_input,
                actor_id=actor_id,
                turn_id=turn_id,
                trace_id=trace_id,
                causality_chain=normalized_chain,
            )
        return asyncio.run(
            self.run_turn_async(
                raw_input=raw_input,
                actor_id=actor_id,
                turn_id=turn_id,
                trace_id=trace_id,
                causality_chain=normalized_chain,
            )
        )

    async def run_turn_async(
        self,
        raw_input: str,
        actor_id: str,
        turn_id: int,
        trace_id: int,
        causality_chain: Optional[E7CausalityChain] = None,
    ) -> Dict[str, Any]:
        normalized_chain = self._normalize_causality_chain(causality_chain)
        if self._consistency_blocking_message is not None:
            return self._build_consistency_blocked_event(turn_id=turn_id, trace_id=trace_id)
        if self.mode == "phase2":
            return self._run_phase2_turn(
                raw_input=raw_input,
                actor_id=actor_id,
                turn_id=turn_id,
                trace_id=trace_id,
                causality_chain=normalized_chain,
            )
        return await self._run_phase3_turn_async(
            raw_input=raw_input,
            actor_id=actor_id,
            turn_id=turn_id,
            trace_id=trace_id,
            causality_chain=normalized_chain,
        )

    def _normalize_causality_chain(self, value: Optional[Any]) -> E7CausalityChain:
        """兼容旧会话/热重载残留对象，统一转为当前模型类。"""
        if value is None:
            return E7CausalityChain()
        if isinstance(value, E7CausalityChain):
            return value

        payload: Dict[str, Any]
        if hasattr(value, "model_dump"):
            payload = value.model_dump(mode="json")
        elif isinstance(value, dict):
            payload = value
        else:
            payload = {"narrative_list": getattr(value, "narrative_list", [])}

        try:
            return E7CausalityChain.model_validate(payload)
        except Exception:
            return E7CausalityChain()

    def get_routing_logs(self) -> List[Dict[str, Any]]:
        return list(self._routing_logs)

    def _update_dm_memory(
        self,
        *,
        turn_id: int,
        actor_id: str,
        raw_input: str,
        dm_result: DmAnalyzeResult,
    ) -> None:
        """维护 DM 对话记忆，使下一轮输入带上最近对话上下文。"""
        self._dm_memory.add_dialogue(turn=turn_id, speaker=actor_id, content=raw_input)

        dm_reply = dm_result.intent_info.dm_reply
        if dm_reply:
            self._dm_memory.add_dialogue(turn=turn_id, speaker="dmagent", content=dm_reply)

        self._dm_memory.current_event = raw_input

    def _record_io(self, *, kind: str, agent_name: str, input_data, output_data=None, extra: Optional[Dict[str, Any]] = None) -> None:
        if self._io_logger is None:
            return

        record = make_io_record(
            kind=kind,
            agent_name=agent_name,
            input_data=input_data.model_dump(mode="json") if hasattr(input_data, "model_dump") else input_data,
            output_data=output_data.model_dump(mode="json") if hasattr(output_data, "model_dump") else output_data,
            extra=extra,
        )
        self._io_logger(record)

    def _prepare_turn_context(
        self,
        *,
        raw_input: str,
        actor_id: str,
        turn_id: int,
        trace_id: int,
    ) -> Dict[str, Any]:
        self._current_actor_id = actor_id
        world_version = int(self.world_state.get_snapshot().get("version", 0))

        routed = self.input_system.dispatch(
            raw_input=raw_input,
            actor_id=actor_id,
            turn=turn_id,
            trace_id=trace_id,
            world_version=world_version,
        )

        return {
            "routed": routed,
            "world_version": world_version,
        }

    def _handle_meta_route(self, *, routed, turn_id: int, trace_id: int) -> Dict[str, Any]:
        event = {
            "route": routed.route,
            "payload": routed.payload,
            "turn_id": turn_id,
            "trace_id": trace_id,
        }
        self._routing_logs.append(event)
        self._record_io(
            kind="turn_result",
            agent_name="engine",
            input_data={"route": "rule_system_meta", "turn_id": turn_id, "trace_id": trace_id},
            output_data=event,
        )
        return event

    def _build_nl_context(
        self,
        *,
        routed,
        raw_input: str,
        actor_id: str,
        turn_id: int,
        trace_id: int,
        world_version: int,
        causality_chain: Optional[E7CausalityChain],
    ) -> Dict[str, Any]:
        dm_result = DmAnalyzeResult.model_validate(routed.payload)
        e1 = E1InputInfo(
            turn_id=turn_id,
            trace_id=trace_id,
            world_version=world_version,
            event_id=routed.envelope.event_id,
            source_id=actor_id,
            raw_text=raw_input,
            metadata=routed.envelope.debug,
        )

        coc_result: Optional[CocCheckResult] = None
        e3_result = E3RuleResult(intent=dm_result.intent_info.intent, success="")
        if dm_result.intent_info.routing_hint in {"num", "against"}:
            coc_result = self._run_check(actor_id, dm_result)
            e3_result = E3RuleResult(
                intent=dm_result.intent_info.intent,
                check_type=coc_result.check_type,
                success=coc_result.result_type,
                difficulty=coc_result.difficulty,
                actor_id=coc_result.id,
                opposed_id=coc_result.opposed_id,
                winner_id=coc_result.winner_id,
                affected_ids=coc_result.affected_ids,
            )

        actor = self.world_state.get_character(actor_id)
        views = self.world_provider.precompute_all_views(current_map_id=actor.location, turn=turn_id)
        e7_input = self._normalize_causality_chain(causality_chain)

        evo_input = EvolutionAgentInput(
            identity=AgentIdentity(id="evolution", skill="summarize world evolution"),
            llm_input=EvolutionAgentLlmInput(
                e1=E1LlmView(raw_text=raw_input, source_id=actor_id),
                e3=E3LlmView(success=e3_result.success or "none"),
                # 历史因果链仅用于系统内部追踪，避免污染本回合推演。
                e7=E7LlmView(narrative_causality=""),
                world_info=views.dm_view,
                narrative_info=self._narrative_info,
            ),
            system_input=EvolutionAgentSystemInput(
                chain_raw=EvolutionAgentChainInput(e1=e1, e3=e3_result, e7=e7_input),
                execution=SystemExecutionMeta(
                    turn_id=turn_id,
                    trace_id=trace_id,
                    world_version=world_version,
                    event_id=routed.envelope.event_id,
                    debug={k: str(v) for k, v in routed.envelope.debug.items()},
                ),
            ),
        )

        evolution_result: EvolutionResult = self.evolution_agent.evolve(
            agent_input=evo_input,
            causality_chain=causality_chain,
        )
        self._record_io(
            kind="agent_io",
            agent_name="evolution",
            input_data=evo_input,
            output_data=evolution_result.output,
            extra={"summary": evolution_result.summary, "visible_to_player": evolution_result.visible_to_player},
        )

        return {
            "dm_result": dm_result,
            "e1": e1,
            "e3_result": e3_result,
            "coc_result": coc_result,
            "views": views,
            "e7_input": e7_input,
            "evolution_result": evolution_result,
        }

    def _run_phase2_turn(
        self,
        *,
        raw_input: str,
        actor_id: str,
        turn_id: int,
        trace_id: int,
        causality_chain: Optional[E7CausalityChain],
    ) -> Dict[str, Any]:
        prepared = self._prepare_turn_context(
            raw_input=raw_input,
            actor_id=actor_id,
            turn_id=turn_id,
            trace_id=trace_id,
        )
        routed = prepared["routed"]
        if routed.route == "rule_system_meta":
            return self._handle_meta_route(routed=routed, turn_id=turn_id, trace_id=trace_id)
        dm_result = DmAnalyzeResult.model_validate(routed.payload)
        direct_event = self._build_dm_direct_reply_event(dm_result=dm_result, turn_id=turn_id, trace_id=trace_id)
        if direct_event is not None:
            self._routing_logs.append(direct_event)
            self._record_io(
                kind="turn_result",
                agent_name="engine",
                input_data={"route": "phase2_dm_direct_reply", "turn_id": turn_id, "trace_id": trace_id, "actor_id": actor_id},
                output_data=direct_event,
            )
            return direct_event

        context = self._build_nl_context(
            routed=routed,
            raw_input=raw_input,
            actor_id=actor_id,
            turn_id=turn_id,
            trace_id=trace_id,
            world_version=prepared["world_version"],
            causality_chain=causality_chain,
        )
        event = {
            "route": "serial_nl",
            "turn_id": turn_id,
            "trace_id": trace_id,
            "dm": self._serialize_dm_result(context["dm_result"]),
            "e3": context["e3_result"].model_dump(mode="json"),
            "evolution": context["evolution_result"].model_dump(mode="json"),
            "narrative_triggered": context["evolution_result"].visible_to_player,
        }
        self._routing_logs.append(event)
        self._record_io(
            kind="turn_result",
            agent_name="engine",
            input_data={"route": "phase2", "turn_id": turn_id, "trace_id": trace_id, "actor_id": actor_id},
            output_data=event,
        )
        return event

    async def _run_phase3_turn_async(
        self,
        *,
        raw_input: str,
        actor_id: str,
        turn_id: int,
        trace_id: int,
        causality_chain: Optional[E7CausalityChain],
    ) -> Dict[str, Any]:
        prepared = self._prepare_turn_context(
            raw_input=raw_input,
            actor_id=actor_id,
            turn_id=turn_id,
            trace_id=trace_id,
        )
        routed = prepared["routed"]
        if routed.route == "rule_system_meta":
            return self._handle_meta_route(routed=routed, turn_id=turn_id, trace_id=trace_id)
        dm_result = DmAnalyzeResult.model_validate(routed.payload)
        direct_event = self._build_dm_direct_reply_event(dm_result=dm_result, turn_id=turn_id, trace_id=trace_id)
        if direct_event is not None:
            self._routing_logs.append(direct_event)
            self._record_io(
                kind="turn_result",
                agent_name="engine",
                input_data={"route": "phase3_dm_direct_reply", "turn_id": turn_id, "trace_id": trace_id, "actor_id": actor_id},
                output_data=direct_event,
            )
            return direct_event

        context = self._build_nl_context(
            routed=routed,
            raw_input=raw_input,
            actor_id=actor_id,
            turn_id=turn_id,
            trace_id=trace_id,
            world_version=prepared["world_version"],
            causality_chain=causality_chain,
        )

        evolution_result = context["evolution_result"]
        e4 = E4EvolutionLlmView(summary=evolution_result.summary)
        e4_chain = E4EvolutionStepResult(summary=evolution_result.summary)
        merger_chain = evolution_result.e7.model_copy(deep=True)
        checkpoint = self.world_state.capture_checkpoint()

        scheduler_input = NpcSchedulerAgentInput(
            identity=AgentIdentity(id="npcscheduler", skill="schedule npc branch"),
            llm_input=NpcSchedulerAgentLlmInput(
                e4=e4,
                world_info=context["views"].npc_scheduler_view,
                narrative_info=self._narrative_info,
            ),
            system_input=NpcSchedulerAgentSystemInput(
                chain_raw=NpcSchedulerAgentChainInput(e4=e4_chain),
                execution=SystemExecutionMeta(
                    turn_id=turn_id,
                    trace_id=trace_id,
                    world_version=prepared["world_version"],
                    event_id=routed.envelope.event_id,
                    debug={"branch": "npc_scheduler"},
                ),
            ),
        )

        state_input = StateAgentInput(
            identity=AgentIdentity(id="state", skill="generate state patch"),
            llm_input=StateAgentLlmInput(
                e4=e4,
                world_info=context["views"].state_agent_view,
                fallback_error=None,
            ),
            system_input=StateAgentSystemInput(
                chain_raw=StateChangeAgentChainInput(e4=e4_chain, fallback_error=None),
                retry_control=SystemRetryControl(
                    can_retry=True,
                    retry_budget=self.config.system.max_retry_count,
                ),
                execution=SystemExecutionMeta(
                    turn_id=turn_id,
                    trace_id=trace_id,
                    world_version=int(checkpoint["version"]),
                    event_id=routed.envelope.event_id,
                    debug={"branch": "state"},
                ),
            ),
        )

        branch_logs: List[Dict[str, Any]] = []
        scheduler_task = asyncio.create_task(self._run_scheduler_branch(scheduler_input, branch_logs))
        state_task = asyncio.create_task(self._run_state_branch(state_input, checkpoint, branch_logs))
        narrative_out: Optional[NarrativeAgentOutput] = None
        narrative_stream_events: List[Dict[str, Any]] = []
        narrative_stream_transport: Dict[str, List[Any]] = {"sse": [], "websocket": []}
        if evolution_result.visible_to_player:
            narrative_input = NarrativeAgentInput(
                identity=AgentIdentity(id="narrative", skill="generate narrative draft"),
                llm_input=NarrativeAgentLlmInput(
                    e4=e4,
                    world_info=context["views"].narrative_view,
                    narrative_info=self._narrative_info,
                ),
                system_input=NarrativeAgentSystemInput(
                    chain_raw=NarrativeAgentChainInput(e4=e4_chain),
                    execution=SystemExecutionMeta(
                        turn_id=turn_id,
                        trace_id=trace_id,
                        world_version=prepared["world_version"],
                        event_id=routed.envelope.event_id,
                        debug={"branch": "narrative"},
                    ),
                ),
            )
            narrative_task = asyncio.create_task(
                self._run_narrative_branch(
                    narrative_input,
                    branch_logs,
                    source_kind="player",
                    source_id=actor_id,
                )
            )
            scheduler_out, state_out, narrative_pack = await asyncio.gather(scheduler_task, state_task, narrative_task)
            narrative_out, narrative_stream_events = narrative_pack
            narrative_stream_transport = NarrativeStreamInterface.build_transport_payload(narrative_stream_events)
        else:
            scheduler_out, state_out = await asyncio.gather(scheduler_task, state_task)

        fallback_error = state_out.get("fallback_error")
        performer_out: List[NpcPerformerAgentOutput] = []
        performer_chain: List[NpcPerformerChainResult] = []
        npc_visible_narratives: List[str] = []
        if fallback_error is None:
            performer_out, performer_chain = await self._run_performer_branch(
                scheduler_out=scheduler_out,
                source_actor_id=actor_id,
                turn_id=turn_id,
                trace_id=trace_id,
                world_version=prepared["world_version"],
                branch_logs=branch_logs,
                narrative_stream_events=narrative_stream_events,
            )
            npc_visible_narratives = self._collect_npc_visible_narrative_texts(performer_chain)
            merger_chain = self._merge_e7_chains(
                base_chain=merger_chain,
                extra_chains=[chain_item.e7 for chain_item in performer_chain],
            )

        narrative_fragments = self._collect_narrative_fragments_from_events(narrative_stream_events)
        aggregated_raw = self._compose_fragment_aggregate_text(narrative_fragments)
        if not aggregated_raw and narrative_out is not None:
            aggregated_raw = str(narrative_out.llm_output.narrative_str or "").strip()
        if narrative_stream_events:
            narrative_stream_transport = NarrativeStreamInterface.build_transport_payload(narrative_stream_events)

        merger_payload = None
        if narrative_out is None:
            narrative_payload = {
                "llm_output": {
                    "narrative_str": "",
                },
                "system_output": {},
                "stream_events": [],
                "stream_transport": {"sse": [], "websocket": []},
                "fragments": [],
                "aggregated_raw": "",
            }
        else:
            narrative_payload = narrative_out.model_dump(mode="json")
            narrative_payload["stream_events"] = narrative_stream_events
            narrative_payload["stream_transport"] = narrative_stream_transport
            narrative_payload["fragments"] = narrative_fragments
            narrative_payload["aggregated_raw"] = aggregated_raw
        if fallback_error is not None:
            narrative_payload = {
                "llm_output": {
                    "narrative_str": "",
                },
                "system_output": narrative_payload.get("system_output", {}),
                "stream_events": [],
                "stream_transport": {"sse": [], "websocket": []},
                "fragments": [],
                "aggregated_raw": "",
            }
        else:
            player_narrative_text = narrative_out.llm_output.narrative_str if narrative_out is not None else ""
            merger_narrative_input = aggregated_raw or self._compose_merger_narrative_input(
                player_narrative_text,
                npc_visible_narratives,
            )
            llm_payload = narrative_payload.get("llm_output", {})
            if isinstance(llm_payload, dict):
                llm_payload["narrative_str"] = merger_narrative_input
            merger_out = await self._run_merger_branch(
                context=context,
                routed=routed,
                prepared=prepared,
                branch_logs=branch_logs,
                causality_chain=merger_chain,
                narrative_str=merger_narrative_input,
            )
            merger_payload = merger_out.model_dump(mode="json")
            self._narrative_info.add_narrative(
                turn=turn_id,
                content=merger_out.llm_output.narrative_str,
                source="merger_agent",
                max_recent=self._narrative_recent_limit,
            )
            if str(player_narrative_text).strip():
                self._narrative_info.append_log(
                    turn=turn_id,
                    content=player_narrative_text,
                    source="narrative_agent",
                )
            for npc_text in npc_visible_narratives:
                self._narrative_info.append_log(
                    turn=turn_id,
                    content=npc_text,
                    source="npc_narrative",
                )
            self._persist_narrative_info()

        committed_summary = ""
        if fallback_error is None:
            if merger_payload is not None:
                committed_summary = merger_payload.get("llm_output", {}).get("narrative_str", "")
            if not committed_summary:
                committed_summary = evolution_result.summary
            self._append_recent_change_log(turn_id=turn_id, route="phase3_concurrent_nl", summary=committed_summary)

        consistency_payload = None
        if fallback_error is None:
            consistency_payload = await self._run_consistency_cycle(turn_id=turn_id, trace_id=trace_id)
            if (
                consistency_payload is not None
                and consistency_payload.get("blocked")
                and self.config.consistency.block_on_failure
            ):
                fallback_error = {
                    "code": "CONSISTENCY_BLOCKED",
                    "message": consistency_payload.get("system_message") or self.config.system.fallback_error,
                    "retry_count": consistency_payload.get("retry_count", 0),
                    "retriable": False,
                    "rollback_applied": False,
                    "degraded_output": consistency_payload.get("system_message"),
                    "details": {"phase": "consistency"},
                }

        event = {
            "route": "phase3_concurrent_nl",
            "turn_id": turn_id,
            "trace_id": trace_id,
            "dm": self._serialize_dm_result(context["dm_result"]),
            "e3": context["e3_result"].model_dump(mode="json"),
            "evolution": evolution_result.model_dump(mode="json"),
            "npcscheduler": scheduler_out.model_dump(mode="json"),
            "npcperformer": [item.model_dump(mode="json") for item in performer_out],
            "npc_performer_chain": [item.model_dump(mode="json") for item in performer_chain],
            "state": state_out,
            "narrative": narrative_payload,
            "merger": merger_payload,
            "narrative_triggered": fallback_error is None and evolution_result.visible_to_player,
            "fallback_error": fallback_error,
            "consistency": consistency_payload,
            "parallel_timeline": branch_logs,
            "terminated": fallback_error is not None,
            "narrative_info": self._narrative_info.model_dump(mode="json"),
        }
        self._routing_logs.append(event)
        self._record_io(
            kind="turn_result",
            agent_name="engine",
            input_data={"route": "phase3", "turn_id": turn_id, "trace_id": trace_id, "actor_id": actor_id},
            output_data=event,
        )
        return event

    def _build_dm_direct_reply_event(
        self,
        *,
        dm_result: DmAnalyzeResult,
        turn_id: int,
        trace_id: int,
    ) -> Optional[Dict[str, Any]]:
        intent_info = dm_result.intent_info
        if intent_info.routing_hint is not None:
            return None
        if not intent_info.dm_reply:
            return None
        return {
            "route": "dm_direct_reply",
            "turn_id": turn_id,
            "trace_id": trace_id,
            "dm": self._serialize_dm_result(dm_result),
            "reply": intent_info.dm_reply,
            "narrative_triggered": False,
            "terminated": False,
        }

    def _run_check(self, actor_id: str, dm_result: DmAnalyzeResult) -> CocCheckResult:
        actor = self.world_state.get_character(actor_id)
        attrs = dm_result.intent_info.attributes or []
        if not attrs:
            raise ValueError("check routing without attributes")

        attr_name = attrs[0]
        attr = actor.attributes.get(attr_name)
        if attr is None:
            raise ValueError(f"attribute not found on actor: {attr_name}")

        if dm_result.intent_info.routing_hint == "against":
            participant_ids = dm_result.intent_info.against_char_id or []
            if len(participant_ids) < 2:
                raise ValueError("against routing without enough participant ids")
            if participant_ids[0] != actor_id:
                raise ValueError("against routing requires actor id as first participant")

            target_id = participant_ids[1]
            if target_id == actor_id:
                raise ValueError("against routing requires a target different from actor")
            if target_id not in self.world_state.get_snapshot().get("characters", {}):
                raise ValueError(f"target character not found: {target_id}")
            target = self.world_state.get_character(target_id)
            target_attr = target.attributes.get(attr_name)
            if target_attr is None:
                raise ValueError(f"attribute not found on target: {attr_name}")

            return self.rule_system.run_against_check(
                actor_id=actor_id,
                actor_attribute_name=attr_name,
                actor_attribute_value=attr.value,
                target_id=target_id,
                target_attribute_name=attr_name,
                target_attribute_value=target_attr.value,
                difficulty=dm_result.intent_info.difficulty,
            )

        return self.rule_system.run_numeric_check(
            actor_id=actor_id,
            attribute_name=attr_name,
            attribute_value=attr.value,
            difficulty=dm_result.intent_info.difficulty,
        )

    @staticmethod
    def _serialize_dm_result(dm_result: DmAnalyzeResult) -> Dict[str, Any]:
        payload = dm_result.output.model_dump(mode="json")
        payload["intent_info"] = payload.get("llm_output", {}).get("intent_info", {})
        return payload

    @staticmethod
    def _stringify_e7(chain: E7CausalityChain) -> str:
        """把结构化 e7 压平成提示词侧可消费的简短字符串。"""
        if not chain.narrative_list:
            return ""
        return " | ".join(str(entry) for entry in chain.narrative_list)

    @staticmethod
    def _collect_npc_visible_narrative_texts(performer_chain: List[NpcPerformerChainResult]) -> List[str]:
        texts: List[str] = []
        for chain_result in performer_chain:
            for event in chain_result.e7.narrative_list:
                if not isinstance(event, dict):
                    continue
                if str(event.get("source", "")) != "npc_narrative":
                    continue
                text = str(event.get("content", "")).strip()
                if text:
                    texts.append(text)
        return texts

    @staticmethod
    def _collect_narrative_fragments_from_events(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        ordered_ids: List[str] = []
        indexed: Dict[str, Dict[str, Any]] = {}

        for event in events:
            if not isinstance(event, dict):
                continue
            data = event.get("data", {})
            if not isinstance(data, dict):
                continue

            fragment_id = str(data.get("fragment_id", "")).strip()
            if not fragment_id:
                continue

            if fragment_id not in indexed:
                indexed[fragment_id] = {
                    "fragment_id": fragment_id,
                    "source_kind": str(data.get("source_kind", "")),
                    "source_id": str(data.get("source_id", "")),
                    "turn_id": data.get("turn_id"),
                    "trace_id": data.get("trace_id"),
                    "content": "",
                }
                ordered_ids.append(fragment_id)

            event_name = str(event.get("event", ""))
            if event_name == "narrative.fragment.delta":
                indexed[fragment_id]["content"] += str(data.get("delta", ""))
            elif event_name == "narrative.fragment.completed":
                completed_text = str(data.get("content", "")).strip()
                if completed_text:
                    indexed[fragment_id]["content"] = completed_text

        fragments: List[Dict[str, Any]] = []
        for fragment_id in ordered_ids:
            payload = indexed[fragment_id]
            if str(payload.get("content", "")).strip():
                fragments.append(payload)
        return fragments

    @staticmethod
    def _compose_fragment_aggregate_text(fragments: List[Dict[str, Any]]) -> str:
        segments = [str(item.get("content", "")).strip() for item in fragments if str(item.get("content", "")).strip()]
        return "|".join(segments)

    @staticmethod
    def _compose_merger_narrative_input(base_text: str, npc_visible_narratives: List[str]) -> str:
        segments: List[str] = []
        normalized_base = str(base_text or "").strip()
        if normalized_base:
            segments.append(normalized_base)
        segments.extend([text for text in npc_visible_narratives if text])
        return " | ".join(segments)

    @staticmethod
    def _merge_e7_chains(*, base_chain: E7CausalityChain, extra_chains: List[E7CausalityChain]) -> E7CausalityChain:
        """把多个分支因果链合并回主 e7，保持顺序并避免直接修改入参。"""
        merged_chain = base_chain.model_copy(deep=True)
        for chain in extra_chains:
            merged_chain.narrative_list.extend(chain.model_copy(deep=True).narrative_list)
        return merged_chain

    async def _run_scheduler_branch(
        self,
        agent_input: NpcSchedulerAgentInput,
        branch_logs: List[Dict[str, Any]],
    ) -> NpcSchedulerAgentOutput:
        started = time.perf_counter()
        started_at = datetime.now(timezone.utc).isoformat()
        output = await asyncio.to_thread(self.npc_scheduler_agent.run, agent_input=agent_input)
        ended = time.perf_counter()
        self._record_io(
            kind="agent_io",
            agent_name="npc_scheduler",
            input_data=agent_input,
            output_data=output,
            extra={
                "branch": "npc_scheduler",
                "turn_id": agent_input.system_input.execution.turn_id,
                "trace_id": agent_input.system_input.execution.trace_id,
                "duration_ms": round((ended - started) * 1000, 3),
            },
        )
        branch_logs.append(
            {
                "branch": "npc_scheduler",
                "turn_id": agent_input.system_input.execution.turn_id,
                "trace_id": agent_input.system_input.execution.trace_id,
                "started_at": started_at,
                "duration_ms": round((ended - started) * 1000, 3),
            }
        )
        return output

    async def _run_performer_branch(
        self,
        *,
        scheduler_out: NpcSchedulerAgentOutput,
        source_actor_id: str,
        turn_id: int,
        trace_id: int,
        world_version: int,
        branch_logs: List[Dict[str, Any]],
        narrative_stream_events: Optional[List[Dict[str, Any]]] = None,
    ) -> Tuple[List[NpcPerformerAgentOutput], List[NpcPerformerChainResult]]:
        """执行 NPC performer，并在 state 成功后统一提交 NPC 目标与记忆副作用。"""
        scheduled_npc_ids = scheduler_out.llm_output.step_result.scheduled_npc_ids
        if not scheduled_npc_ids:
            return [], []

        outputs: List[NpcPerformerAgentOutput] = []
        downstream_chain: List[NpcPerformerChainResult] = []
        started = time.perf_counter()
        started_at = datetime.now(timezone.utc).isoformat()

        for npc_id in scheduled_npc_ids:
            npc_character = self.world_state.get_character(npc_id)
            valid_character_refs = [
                AvailableCharacterRef(id=char_id, name=self.world_state.get_character(char_id).name)
                for char_id in sorted(self.world_state.get_snapshot().get("characters", {}).keys())
            ]
            npc_input = NpcPerformerAgentInput(
                identity=AgentIdentity(id="npcperformer", skill="execute npc behavior"),
                llm_input=NpcPerformerAgentLlmInput(
                    e4={
                        "scheduled_npc_ids": scheduled_npc_ids,
                        "extra_npc_context": scheduler_out.llm_output.step_result.extra_npc_context,
                    },
                    e1=E1LlmView(
                        raw_text=scheduler_out.llm_output.step_result.summary,
                        source_id=source_actor_id,
                    ),
                    world_info=self.world_provider.get_npc_view(npc_id),
                    agent_memory=npc_character.memory,
                    available_attributes=[
                        AvailableAttributeRef(id=attr_id, name=attr.name)
                        for attr_id, attr in npc_character.attributes.items()
                    ],
                    valid_characters=valid_character_refs,
                ),
                system_input=NpcPerformerAgentSystemInput(
                    chain_raw=NpcPerformerAgentChainInput(
                        e4=E4SchedulerStepResult(
                            scheduled_npc_ids=scheduled_npc_ids,
                            extra_npc_context=scheduler_out.llm_output.step_result.extra_npc_context,
                        ),
                        e1=E1InputInfo(
                            turn_id=turn_id,
                            trace_id=trace_id,
                            world_version=world_version,
                            source_id=source_actor_id,
                            raw_text=scheduler_out.llm_output.step_result.summary,
                            metadata={"branch": "npc_performer"},
                        ),
                    ),
                    execution=SystemExecutionMeta(
                        turn_id=turn_id,
                        trace_id=trace_id,
                        world_version=world_version,
                        debug={"branch": "npc_performer", "npc_id": npc_id},
                    ),
                ),
            )
            output = await asyncio.to_thread(self.npc_performer_agent.run, agent_input=npc_input)
            outputs.append(output)
            chain_result = await asyncio.to_thread(
                self._run_npc_performer_downstream,
                npc_id,
                source_actor_id,
                output,
                turn_id,
                trace_id,
                world_version,
                narrative_stream_events,
            )
            downstream_chain.append(chain_result)
            await asyncio.to_thread(
                self.npc_performer_agent.apply_side_effects,
                agent_input=npc_input,
                output=output,
            )
            self._record_io(
                kind="agent_io",
                agent_name="npc_performer",
                input_data=npc_input,
                output_data=output,
                extra={
                    "branch": "npc_performer",
                    "turn_id": turn_id,
                    "trace_id": trace_id,
                    "npc_id": npc_id,
                },
            )

        ended = time.perf_counter()
        branch_logs.append(
            {
                "branch": "npc_performer",
                "turn_id": turn_id,
                "trace_id": trace_id,
                "started_at": started_at,
                "duration_ms": round((ended - started) * 1000, 3),
                "npc_ids": list(scheduled_npc_ids),
            }
        )
        return outputs, downstream_chain

    def _run_npc_performer_downstream(
        self,
        npc_id: str,
        source_actor_id: str,
        performer_output: NpcPerformerAgentOutput,
        turn_id: int,
        trace_id: int,
        world_version: int,
        narrative_stream_events: Optional[List[Dict[str, Any]]] = None,
    ) -> NpcPerformerChainResult:
        """执行 NPC performer 的下游链路，并生成可并回主 e7 的结构化结果。"""
        llm_output = performer_output.llm_output
        check_result: Optional[CocCheckResult] = None
        check_error: Optional[str] = None

        try:
            check_result = self._run_npc_check(actor_id=npc_id, performer_output=performer_output)
        except (ValueError, KeyError) as exc:
            check_error = str(exc)

        e3_result = E3RuleResult(
            intent=llm_output.intent,
            success="",
        )
        if check_result is not None:
            e3_result = E3RuleResult(
                intent=llm_output.intent,
                check_type=check_result.check_type,
                success=check_result.result_type,
                difficulty=check_result.difficulty,
                actor_id=check_result.id,
                opposed_id=check_result.opposed_id,
                winner_id=check_result.winner_id,
                affected_ids=check_result.affected_ids,
            )

        npc = self.world_state.get_character(npc_id)
        views = self.world_provider.precompute_all_views(current_map_id=npc.location, turn=turn_id)
        npc_e1 = E1InputInfo(
            turn_id=turn_id,
            trace_id=trace_id,
            world_version=world_version,
            source_id=npc_id,
            raw_text=llm_output.action_text,
            metadata={"branch": "npc_performer", "source_actor_id": source_actor_id},
        )
        evo_input = EvolutionAgentInput(
            identity=AgentIdentity(id="evolution", skill="summarize world evolution"),
            llm_input=EvolutionAgentLlmInput(
                e1=E1LlmView(raw_text=llm_output.action_text, source_id=npc_id),
                e3=E3LlmView(success=e3_result.success or "none"),
                e7=E7LlmView(narrative_causality=""),
                world_info=views.dm_view,
                narrative_info=self._narrative_info,
            ),
            system_input=EvolutionAgentSystemInput(
                chain_raw=EvolutionAgentChainInput(
                    e1=npc_e1,
                    e3=e3_result,
                    e7=E7CausalityChain(),
                ),
                execution=SystemExecutionMeta(
                    turn_id=turn_id,
                    trace_id=trace_id,
                    world_version=world_version,
                    debug={"branch": "npc_performer_evolution", "npc_id": npc_id},
                ),
            ),
        )
        evolution_result = self.evolution_agent.evolve(agent_input=evo_input, causality_chain=None)
        chain_projection = E7CausalityChain()
        action_text = str(llm_output.action_text or "").strip()
        npc_narrative_text = ""
        if evolution_result.visible_to_player:
            npc_narrative_input = NarrativeAgentInput(
                identity=AgentIdentity(id="narrative", skill="generate npc-visible narrative"),
                llm_input=NarrativeAgentLlmInput(
                    e4=E4EvolutionLlmView(summary=evolution_result.summary),
                    world_info=views.narrative_view,
                    narrative_info=self._narrative_info,
                ),
                system_input=NarrativeAgentSystemInput(
                    chain_raw=NarrativeAgentChainInput(e4=E4EvolutionStepResult(summary=evolution_result.summary)),
                    execution=SystemExecutionMeta(
                        turn_id=turn_id,
                        trace_id=trace_id,
                        world_version=world_version,
                        debug={"branch": "npc_performer_narrative", "npc_id": npc_id},
                    ),
                ),
            )
            npc_narrative_out, npc_stream_events = self.narrative_agent.run_stream(
                agent_input=npc_narrative_input,
                source_kind="npc",
                source_id=npc_id,
                event_callback=self._emit_narrative_event,
            )
            if narrative_stream_events is not None:
                narrative_stream_events.extend(npc_stream_events)
            npc_narrative_text = str(npc_narrative_out.llm_output.narrative_str or "").strip()
            self._record_io(
                kind="agent_io",
                agent_name="narrative",
                input_data=npc_narrative_input,
                output_data=npc_narrative_out,
                extra={
                    "branch": "npc_performer_narrative",
                    "turn_id": turn_id,
                    "trace_id": trace_id,
                    "npc_id": npc_id,
                },
            )
        if action_text:
            chain_projection.narrative_list.append(
                {
                    "source": "npc_performer",
                    "trace_id": str(trace_id),
                    "turn_id": str(turn_id),
                    "npc_id": npc_id,
                    "content": action_text,
                }
            )
        if check_result is not None:
            chain_projection.narrative_list.append(
                {
                    "source": "npc_check",
                    "trace_id": str(trace_id),
                    "turn_id": str(turn_id),
                    "npc_id": npc_id,
                    "content": (
                        f"NPC {npc_id} 发起 {check_result.check_type} 鉴定，"
                        f"属性={check_result.attribute}，结果={check_result.result_type}"
                    ),
                }
            )
        elif check_error is not None:
            chain_projection.narrative_list.append(
                {
                    "source": "npc_check_error",
                    "trace_id": str(trace_id),
                    "turn_id": str(turn_id),
                    "npc_id": npc_id,
                    "content": f"NPC {npc_id} 鉴定链路失败：{check_error}",
                }
            )
        if npc_narrative_text:
            chain_projection.narrative_list.append(
                {
                    "source": "npc_narrative",
                    "trace_id": str(trace_id),
                    "turn_id": str(turn_id),
                    "npc_id": npc_id,
                    "content": npc_narrative_text,
                }
            )
        chain_projection.narrative_list.extend(evolution_result.e7.model_copy(deep=True).narrative_list)
        chain_result = NpcPerformerChainResult(
            npc_id=npc_id,
            intent=llm_output.intent,
            check=check_result,
            check_error=check_error,
            evolution_summary=evolution_result.summary,
            evolution_visible_to_player=evolution_result.visible_to_player,
            e7=chain_projection,
        )
        self._record_io(
            kind="agent_io",
            agent_name="npc_performer_chain",
            input_data={
                "npc_id": npc_id,
                "performer": performer_output.model_dump(mode="json"),
                "check_requested": llm_output.routing_hint,
            },
            output_data={
                "check": check_result.model_dump(mode="json") if check_result is not None else None,
                "check_error": check_error,
                "evolution": evolution_result.model_dump(mode="json"),
                "npc_narrative_text": npc_narrative_text,
                "e7": chain_projection.model_dump(mode="json"),
            },
            extra={
                "branch": "npc_performer_chain",
                "turn_id": turn_id,
                "trace_id": trace_id,
                "npc_id": npc_id,
            },
        )
        return chain_result

    def _run_npc_check(self, *, actor_id: str, performer_output: NpcPerformerAgentOutput) -> Optional[CocCheckResult]:
        """按 performer 输出执行 NPC 鉴定；无鉴定需求时返回 None。"""
        intent_info = performer_output.llm_output
        if intent_info.routing_hint not in {"num", "against"}:
            return None

        attrs = intent_info.attributes or []
        if not attrs:
            raise ValueError("npc check routing without attributes")

        actor = self.world_state.get_character(actor_id)
        attr_name = attrs[0]
        attr = actor.attributes.get(attr_name)
        if attr is None:
            raise ValueError(f"attribute not found on npc: {attr_name}")

        if intent_info.routing_hint == "against":
            participant_ids = intent_info.against_char_id or []
            if len(participant_ids) < 2:
                raise ValueError("npc against routing requires at least 2 character ids")
            if participant_ids[0] != actor_id:
                raise ValueError("npc against routing requires actor id as first participant")
            target_id = participant_ids[1]
            if target_id == actor_id:
                raise ValueError("npc against routing requires target id")
            if target_id not in self.world_state.get_snapshot().get("characters", {}):
                raise ValueError(f"npc against target not found: {target_id}")
            target = self.world_state.get_character(target_id)
            target_attr = target.attributes.get(attr_name)
            if target_attr is None:
                raise ValueError(f"attribute not found on target: {attr_name}")

            return self.rule_system.run_against_check(
                actor_id=actor_id,
                actor_attribute_name=attr_name,
                actor_attribute_value=attr.value,
                target_id=target_id,
                target_attribute_name=attr_name,
                target_attribute_value=target_attr.value,
                difficulty=intent_info.difficulty,
            )

        return self.rule_system.run_numeric_check(
            actor_id=actor_id,
            attribute_name=attr_name,
            attribute_value=attr.value,
            difficulty=intent_info.difficulty,
        )

    async def _run_narrative_branch(
        self,
        agent_input: NarrativeAgentInput,
        branch_logs: List[Dict[str, Any]],
        *,
        source_kind: str,
        source_id: str,
    ) -> Tuple[NarrativeAgentOutput, List[Dict[str, Any]]]:
        started = time.perf_counter()
        started_at = datetime.now(timezone.utc).isoformat()
        output, stream_events = await asyncio.to_thread(
            self.narrative_agent.run_stream,
            agent_input=agent_input,
            source_kind=source_kind,
            source_id=source_id,
            event_callback=self._emit_narrative_event,
        )
        ended = time.perf_counter()
        self._record_io(
            kind="agent_io",
            agent_name="narrative",
            input_data=agent_input,
            output_data=output,
            extra={
                "branch": "narrative",
                "turn_id": agent_input.system_input.execution.turn_id,
                "trace_id": agent_input.system_input.execution.trace_id,
                "stream_event_count": len(stream_events),
                "duration_ms": round((ended - started) * 1000, 3),
            },
        )
        branch_logs.append(
            {
                "branch": "narrative",
                "turn_id": agent_input.system_input.execution.turn_id,
                "trace_id": agent_input.system_input.execution.trace_id,
                "started_at": started_at,
                "duration_ms": round((ended - started) * 1000, 3),
            }
        )
        return output, stream_events

    async def _run_merger_branch(
        self,
        *,
        context: Dict[str, Any],
        routed,
        prepared: Dict[str, Any],
        branch_logs: List[Dict[str, Any]],
        causality_chain: E7CausalityChain,
        narrative_str: str,
    ) -> MergerAgentOutput:
        """在状态提交成功后合并回合因果链并写入叙事真值池。"""
        started = time.perf_counter()
        started_at = datetime.now(timezone.utc).isoformat()
        if narrative_str.strip():
            causality_chain.narrative_list.append(
                {
                    "source": "narrative",
                    "trace_id": str(context["evolution_result"].trace_id),
                    "turn_id": str(context["evolution_result"].turn_id),
                    "content": narrative_str,
                }
            )

        actor_id = str(context["e1"].source_id)
        actor = self.world_state.get_character(actor_id)
        refreshed_views = self.world_provider.precompute_all_views(
            current_map_id=actor.location,
            turn=context["evolution_result"].turn_id,
        )

        merger_input = MergerAgentInput(
            identity=AgentIdentity(id="merger", skill="merge committed narrative"),
            llm_input=MergerAgentLlmInput(
                e7=E7LlmView(narrative_causality=self._stringify_e7(causality_chain)),
                world_info=refreshed_views.narrative_view,
                narrative_info=self._narrative_info,
                narrative_str=narrative_str,
            ),
            system_input=MergerAgentSystemInput(
                chain_raw=MergerAgentChainInput(
                    e7=causality_chain,
                ),
                execution=SystemExecutionMeta(
                    turn_id=context["evolution_result"].turn_id,
                    trace_id=context["evolution_result"].trace_id,
                    world_version=prepared["world_version"],
                    event_id=routed.envelope.event_id,
                    debug={"branch": "merger"},
                ),
            ),
        )

        output = await asyncio.to_thread(self.merger_agent.run, agent_input=merger_input)
        ended = time.perf_counter()
        self._record_io(
            kind="agent_io",
            agent_name="merger",
            input_data=merger_input,
            output_data=output,
            extra={
                "branch": "merger",
                "turn_id": merger_input.system_input.execution.turn_id,
                "trace_id": merger_input.system_input.execution.trace_id,
                "duration_ms": round((ended - started) * 1000, 3),
            },
        )
        branch_logs.append(
            {
                "branch": "merger",
                "turn_id": merger_input.system_input.execution.turn_id,
                "trace_id": merger_input.system_input.execution.trace_id,
                "started_at": started_at,
                "duration_ms": round((ended - started) * 1000, 3),
            }
        )
        return output

    async def _run_state_branch(
        self,
        base_input: StateAgentInput,
        checkpoint: Dict[str, Any],
        branch_logs: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        started = time.perf_counter()
        started_at = datetime.now(timezone.utc).isoformat()
        timeout_ms = self.config.system.retry_timeout_ms
        max_retry = self.config.system.max_retry_count

        last_error: Optional[FallbackError] = None
        last_feedback: Optional[StateErrorFeedback] = None
        error_history: List[Dict[str, Any]] = []

        for retry_seq in range(max_retry + 1):
            elapsed_ms = (time.perf_counter() - started) * 1000
            if elapsed_ms > timeout_ms:
                break

            current_input = base_input.model_copy(deep=True)
            current_input.llm_input.fallback_error = last_feedback
            current_input.system_input.chain_raw = StateChangeAgentChainInput(
                e4=base_input.system_input.chain_raw.e4 if base_input.system_input.chain_raw else E4EvolutionStepResult(),
                fallback_error=last_error,
            )
            current_input.system_input.execution.world_version = int(checkpoint["version"])

            output: Optional[StateAgentOutput] = None
            try:
                output = await asyncio.to_thread(
                    self.state_agent.run,
                    agent_input=current_input,
                    retry_seq=retry_seq,
                    patch_id=f"patch-{current_input.system_input.execution.turn_id}-{current_input.system_input.execution.trace_id}-{retry_seq}",
                )
            except LLMServiceError as exc:
                last_feedback = StateErrorFeedback(
                    message=str(exc),
                    details={"error_type": "llm_service_error"},
                    fix_hint="状态变更服务暂不可用，请稍后重试。",
                )
                last_error = FallbackError(
                    code="STATE_AGENT_UNAVAILABLE",
                    message=str(exc),
                    retry_count=retry_seq + 1,
                    retriable=(retry_seq < max_retry),
                    rollback_applied=False,
                    degraded_output=None,
                    details={"phase": "state_agent"},
                )
                error_history.append(last_error.model_dump(mode="json"))
                self._record_io(
                    kind="agent_io",
                    agent_name="state_change",
                    input_data=current_input,
                    output_data=None,
                    extra={
                        "branch": "state",
                        "turn_id": current_input.system_input.execution.turn_id,
                        "trace_id": current_input.system_input.execution.trace_id,
                        "retry_seq": retry_seq,
                        "apply_status": "llm_unavailable",
                        "error": str(exc),
                    },
                )
                continue

            try:
                async with self._state_commit_lock:
                    apply_result = await asyncio.to_thread(self.state_patch_runtime.apply_patch, output)

                ended = time.perf_counter()
                self._record_io(
                    kind="agent_io",
                    agent_name="state_change",
                    input_data=current_input,
                    output_data=output,
                    extra={
                        "branch": "state",
                        "turn_id": current_input.system_input.execution.turn_id,
                        "trace_id": current_input.system_input.execution.trace_id,
                        "retry_seq": retry_seq,
                        "duration_ms": round((ended - started) * 1000, 3),
                        "apply_status": "applied",
                    },
                )
                branch_logs.append(
                    {
                        "branch": "state",
                        "turn_id": current_input.system_input.execution.turn_id,
                        "trace_id": current_input.system_input.execution.trace_id,
                        "started_at": started_at,
                        "duration_ms": round((ended - started) * 1000, 3),
                    }
                )
                self._persist_world_snapshot()
                return {
                    "ok": True,
                    "retry_count": retry_seq,
                    "patch": output.model_dump(mode="json"),
                    "apply_result": {
                        "patch_id": apply_result.patch_id,
                        "turn_id": apply_result.turn_id,
                        "trace_id": apply_result.trace_id,
                        "world_version": apply_result.world_version,
                        "applied_ops": apply_result.applied_ops,
                    },
                    "error_history": error_history,
                    "fallback_error": None,
                }
            except StatePatchError as exc:
                last_feedback = StateErrorFeedback(
                    message=exc.message,
                    details={k: str(v) for k, v in exc.details.items()},
                    fix_hint="请改写为可写字段、匹配类型并满足数值边界后重试。",
                )
                last_error = FallbackError(
                    code=exc.code,
                    message=exc.message,
                    retry_count=retry_seq + 1,
                    retriable=(retry_seq < max_retry),
                    rollback_applied=False,
                    degraded_output=None,
                    details=exc.details,
                )
                error_history.append(last_error.model_dump(mode="json"))
                self._record_io(
                    kind="agent_io",
                    agent_name="state_change",
                    input_data=current_input,
                    output_data=output,
                    extra={
                        "branch": "state",
                        "turn_id": current_input.system_input.execution.turn_id,
                        "trace_id": current_input.system_input.execution.trace_id,
                        "retry_seq": retry_seq,
                        "apply_status": "patch_error",
                        "patch_error": {"code": exc.code, "message": exc.message, "details": exc.details},
                    },
                )

        async with self._state_commit_lock:
            await asyncio.to_thread(self.world_state.restore_checkpoint, checkpoint)
        self._persist_world_snapshot()

        fallback = FallbackError(
            code="STATE_PATCH_RETRY_EXHAUSTED",
            message=self.config.system.fallback_error,
            retry_count=max_retry,
            retriable=False,
            rollback_applied=True,
            degraded_output=self.config.system.fallback_error,
            details={"checkpoint_version": checkpoint.get("version")},
        )
        ended = time.perf_counter()
        self._record_io(
            kind="agent_io",
            agent_name="state_change",
            input_data=base_input,
            output_data={"fallback_error": fallback.model_dump(mode="json"), "error_history": error_history},
            extra={
                "branch": "state",
                "turn_id": base_input.system_input.execution.turn_id,
                "trace_id": base_input.system_input.execution.trace_id,
                "retry_seq": max_retry,
                "duration_ms": round((ended - started) * 1000, 3),
                "apply_status": "fallback_exhausted",
            },
        )
        branch_logs.append(
            {
                "branch": "state",
                "turn_id": base_input.system_input.execution.turn_id,
                "trace_id": base_input.system_input.execution.trace_id,
                "started_at": started_at,
                "duration_ms": round((ended - started) * 1000, 3),
            }
        )
        return {
            "ok": False,
            "retry_count": max_retry,
            "patch": None,
            "apply_result": None,
            "error_history": error_history,
            "fallback_error": fallback.model_dump(mode="json"),
        }
