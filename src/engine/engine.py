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
from src.engine.consistency_orchestrator import ConsistencyOrchestrator
from src.engine.narrative_truth_manager import NarrativeTruthManager
from src.engine.turn_orchestrator import TurnOrchestrator
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

        cfg = getattr(llm_service, "config", None) if llm_service is not None else None
        if cfg is None:
            cfg = ConfigLoader.load(config_path=config_path)

        if llm_service is None:
            llm_service = LLMServiceBase(config=cfg, io_recorder=io_logger)
        elif io_logger is not None and hasattr(llm_service, "io_recorder") and getattr(llm_service, "io_recorder", None) is None:
            setattr(llm_service, "io_recorder", io_logger)

        self.config = cfg
        validate_required_dexterity(
            world_state,
            dexterity_attribute_keys=self.config.system.dexterity_attribute_keys,
        )

        self.mode = mode
        self.rule_system = RuleSystem(world_state=world_state)
        self.world_provider = WorldDataProvider(world_state=world_state)

        self.dm_agent = DMAgent(llm_service=llm_service, max_retries=dm_max_retries)
        self.evolution_agent = EvolutionAgent(llm_service=llm_service)
        self._current_actor_id = ""
        self._routing_logs: List[Dict[str, Any]] = []
        self._io_logger = io_logger
        self._narrative_info = NarrativeInfo()

        self.input_system = InputSystem(rule_system=self.rule_system, dm_handler=self._dm_handler)

        self._enable_persistence = bool(enable_persistence)
        self._dm_memory = DmMemory(memory_turns=self.config.agent.dm.memory_turns)
        self._world_snapshot_repository = self._build_world_snapshot_repository()
        self._narrative_repository = self._build_narrative_repository()
        self._turn_orchestrator = TurnOrchestrator(self)
        self._consistency_orchestrator = ConsistencyOrchestrator(self)
        self._narrative_truth_manager = NarrativeTruthManager()

        self.state_agent = StateChangeAgent(llm_service=self.dm_agent.llm_service)
        self.consistency_agent = ConsistencyAgent(llm_service=self.dm_agent.llm_service)
        self.npc_scheduler_agent = NpcSchedulerAgent(
            llm_service=self.dm_agent.llm_service,
            world_state=self.world_state,
            max_actions_per_turn=int(self.config.agent.npc.max_actions_per_turn),
            cooldown_turns=int(self.config.agent.npc.cooldown_turns),
            dexterity_attribute_keys=self.config.system.dexterity_attribute_keys,
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
        self._narrative_info = self._narrative_truth_manager.restore(
            repository=self._narrative_repository,
            current=self._narrative_info,
        )

    def _persist_narrative_info(self) -> None:
        """把当前 NarrativeInfo 同步写入独立 SQLite 仓储。"""
        self._narrative_truth_manager.persist(
            repository=self._narrative_repository,
            narrative_info=self._narrative_info,
        )

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
            elif hasattr(item, "content"):
                content = self._normalize_consistency_text(getattr(item, "content", ""))
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
        """兼容旧入口：一致性输入构建已迁移到 ConsistencyOrchestrator。"""
        return self._consistency_orchestrator.build_consistency_input(turn_id=turn_id, trace_id=trace_id)

    async def _run_consistency_cycle(self, *, turn_id: int, trace_id: int) -> Optional[Dict[str, Any]]:
        """兼容旧入口：一致性循环已迁移到 ConsistencyOrchestrator。"""
        return await self._consistency_orchestrator.run_consistency_cycle(turn_id=turn_id, trace_id=trace_id)

    def _apply_consistency_changes(
        self,
        output: ConsistencyAgentOutput,
        agent_input: ConsistencyAgentInput,
        turn_id: int,
    ) -> Dict[str, Any]:
        """兼容旧入口：一致性应用已迁移到 ConsistencyOrchestrator。"""
        return self._consistency_orchestrator.apply_consistency_changes(output, agent_input, turn_id)

    @staticmethod
    def _get_consistency_entity(*, store, entity_id: str):
        """兼容旧入口：实体解析已迁移到 ConsistencyOrchestrator。"""
        return ConsistencyOrchestrator.get_consistency_entity(store=store, entity_id=entity_id)

    def _normalize_npc_memory_windows(self, *, store) -> None:
        """兼容旧入口：记忆窗口规整已迁移到 ConsistencyOrchestrator。"""
        self._consistency_orchestrator.normalize_npc_memory_windows(store=store)

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
        attribute_refs = self._build_available_attribute_refs()
        character_refs = self._build_valid_character_refs()

        dm_input = DmAgentInput(
            identity=AgentIdentity(id="dmagent", skill="parse user intent"),
            llm_input=DmAgentLlmInput(
                e1=E1LlmView(raw_text=envelope.raw_input, source_id=self._current_actor_id),
                world_info=views.dm_view,
                narrative_info=self._narrative_info,
                agent_memory=self._dm_memory,
                available_attributes=attribute_refs,
                valid_characters=character_refs,
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

        analyzed = self.dm_agent.run(agent_input=dm_input)
        self._update_dm_memory(
            turn_id=envelope.turn,
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
        return await self._turn_orchestrator.run_phase3_turn_async(
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
        dm_result: DmAnalyzeResult,
    ) -> None:
        """维护 DM 对话记忆，使下一轮输入带上最近对话上下文。"""
        dm_reply = dm_result.intent_info.dm_reply
        if dm_reply:
            self._dm_memory.add_dialogue(turn=turn_id, speaker="dmagent", content=dm_reply)

    def _build_valid_character_refs(self) -> List[AvailableCharacterRef]:
        return [
            AvailableCharacterRef(id=char_id, name=self.world_state.get_character(char_id).name)
            for char_id in sorted(self.world_state.get_snapshot().characters.keys())
        ]

    def _build_available_attribute_refs(self) -> List[AvailableAttributeRef]:
        """聚合当前世界内所有角色可用属性，确保 ID + name 完整可见。"""
        attrs: Dict[str, str] = {}
        for char_id in sorted(self.world_state.get_snapshot().characters.keys()):
            character = self.world_state.get_character(char_id)
            for attr_id, attr in character.attributes.items():
                normalized_id = str(attr_id or "").strip()
                if not normalized_id:
                    continue
                attr_name = str(getattr(attr, "name", "") or normalized_id).strip()
                if normalized_id not in attrs or (not attrs[normalized_id] and attr_name):
                    attrs[normalized_id] = attr_name

        return [
            AvailableAttributeRef(id=attr_id, name=attrs[attr_id] or attr_id)
            for attr_id in sorted(attrs.keys())
        ]

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
        world_version = int(self.world_state.get_snapshot().version)

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
        """兼容旧入口：phase3 主编排已迁移到 TurnOrchestrator。"""
        return await self._turn_orchestrator.run_phase3_turn_async(
            raw_input=raw_input,
            actor_id=actor_id,
            turn_id=turn_id,
            trace_id=trace_id,
            causality_chain=causality_chain,
        )

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
            if target_id not in self.world_state.get_snapshot().characters:
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
    ) -> Tuple[List[NpcPerformerAgentOutput], List[NpcPerformerChainResult], Optional[Dict[str, Any]]]:
        """执行 NPC 串行链路：每个 NPC 都在自身状态提交完成后才激活下一个 NPC。"""
        scheduled_npc_ids = scheduler_out.llm_output.step_result.scheduled_npc_ids
        if not scheduled_npc_ids:
            return [], [], None

        outputs: List[NpcPerformerAgentOutput] = []
        downstream_chain: List[NpcPerformerChainResult] = []
        started = time.perf_counter()
        started_at = datetime.now(timezone.utc).isoformat()

        for npc_id in scheduled_npc_ids:
            current_world_version = int(self.world_state.get_version())
            npc_character = self.world_state.get_character(npc_id)
            valid_character_refs = self._build_valid_character_refs()
            available_attribute_refs = self._build_available_attribute_refs()
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
                    available_attributes=available_attribute_refs,
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
                            world_version=current_world_version,
                            source_id=source_actor_id,
                            raw_text=scheduler_out.llm_output.step_result.summary,
                            metadata={"branch": "npc_performer"},
                        ),
                    ),
                    execution=SystemExecutionMeta(
                        turn_id=turn_id,
                        trace_id=trace_id,
                        world_version=current_world_version,
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
                current_world_version,
                narrative_stream_events,
            )
            downstream_chain.append(chain_result)

            refreshed_npc = self.world_state.get_character(npc_id)
            npc_views = self.world_provider.precompute_all_views(current_map_id=refreshed_npc.location, turn=turn_id)
            npc_checkpoint = self.world_state.capture_checkpoint()
            npc_state_input = StateAgentInput(
                identity=AgentIdentity(id="state", skill="generate state patch"),
                llm_input=StateAgentLlmInput(
                    e4=E4EvolutionLlmView(summary=chain_result.evolution_summary),
                    world_info=npc_views.state_agent_view,
                    fallback_error=None,
                ),
                system_input=StateAgentSystemInput(
                    chain_raw=StateChangeAgentChainInput(
                        e4=E4EvolutionStepResult(summary=chain_result.evolution_summary),
                        fallback_error=None,
                    ),
                    retry_control=SystemRetryControl(
                        can_retry=True,
                        retry_budget=self.config.system.max_retry_count,
                    ),
                    execution=SystemExecutionMeta(
                        turn_id=turn_id,
                        trace_id=trace_id,
                        world_version=int(npc_checkpoint["version"]),
                        debug={"branch": "npc_state", "npc_id": npc_id},
                    ),
                ),
            )
            npc_state_result = await self._run_state_branch(
                npc_state_input,
                npc_checkpoint,
                branch_logs,
                post_apply_hook=lambda _output=output: self.npc_performer_agent.apply_side_effects(output=_output),
            )
            npc_fallback = npc_state_result.get("fallback_error")
            if npc_fallback is not None:
                fallback_code = str(npc_fallback.get("code", ""))
                stopped_reason = "npc_side_effects_failed" if fallback_code == "STATE_POST_APPLY_FAILED" else "npc_state_fallback"
                ended = time.perf_counter()
                branch_logs.append(
                    {
                        "branch": "npc_performer",
                        "turn_id": turn_id,
                        "trace_id": trace_id,
                        "started_at": started_at,
                        "duration_ms": round((ended - started) * 1000, 3),
                        "npc_ids": list(scheduled_npc_ids),
                        "stopped_at_npc_id": npc_id,
                        "stopped_reason": stopped_reason,
                    }
                )
                if fallback_code == "STATE_POST_APPLY_FAILED":
                    return (
                        outputs,
                        downstream_chain,
                        {
                            "code": "NPC_SIDE_EFFECTS_FAILED",
                            "message": self.config.system.fallback_error,
                            "retry_count": int(npc_fallback.get("retry_count", 0)),
                            "retriable": False,
                            "rollback_applied": bool(npc_fallback.get("rollback_applied", True)),
                            "degraded_output": npc_fallback.get("degraded_output", self.config.system.fallback_error),
                            "details": {
                                "phase": "npc_performer",
                                "npc_id": npc_id,
                                "origin": npc_fallback.get("details", {}).get("error", "state_post_apply_failed"),
                            },
                        },
                    )
                return (
                    outputs,
                    downstream_chain,
                    {
                        "code": "NPC_STATE_PATCH_FAILED",
                        "message": npc_fallback.get("message", self.config.system.fallback_error),
                        "retry_count": int(npc_fallback.get("retry_count", 0)),
                        "retriable": False,
                        "rollback_applied": bool(npc_fallback.get("rollback_applied", True)),
                        "degraded_output": npc_fallback.get("degraded_output", self.config.system.fallback_error),
                        "details": {
                            "phase": "npc_state",
                            "npc_id": npc_id,
                            "origin": npc_fallback,
                        },
                    },
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
                    "npc_state": npc_state_result,
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
        return outputs, downstream_chain, None

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
            if target_id not in self.world_state.get_snapshot().characters:
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
        post_apply_hook: Optional[Callable[[], None]] = None,
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
                    if post_apply_hook is not None:
                        await asyncio.to_thread(post_apply_hook)

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
            except Exception as exc:
                if post_apply_hook is None:
                    raise

                async with self._state_commit_lock:
                    await asyncio.to_thread(self.world_state.restore_checkpoint, checkpoint)
                self._persist_world_snapshot()

                fallback = FallbackError(
                    code="STATE_POST_APPLY_FAILED",
                    message=self.config.system.fallback_error,
                    retry_count=retry_seq,
                    retriable=False,
                    rollback_applied=True,
                    degraded_output=self.config.system.fallback_error,
                    details={"phase": "state_post_apply", "error": str(exc)},
                )
                error_history.append(fallback.model_dump(mode="json"))

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
                        "apply_status": "post_apply_failed",
                        "error": str(exc),
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
                return {
                    "ok": False,
                    "retry_count": retry_seq,
                    "patch": output.model_dump(mode="json") if output is not None else None,
                    "apply_result": None,
                    "error_history": error_history,
                    "fallback_error": fallback.model_dump(mode="json"),
                }

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
