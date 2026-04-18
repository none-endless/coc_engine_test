from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Tuple

from src.agent.llm.evolution_agent import EvolutionAgent, EvolutionResult
from src.agent.llm.input_agent import DMAgent, DmAnalyzeResult
from src.agent.llm.merger_agent import MergerAgent
from src.agent.llm.narrative_agent import NarrativeAgent
from src.agent.llm.npc_perform_agent import NpcPerformerAgent
from src.agent.llm.npc_schedul_agent import NpcSchedulerAgent
from src.agent.llm.service import LLMServiceBase
from src.agent.llm.statechange_agent import StateChangeAgent
from src.config.loader import ConfigLoader
from src.data.model.agent_input import (
    AgentIdentity,
    AvailableAttributeRef,
    AvailableCharacterRef,
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
    MergerAgentOutput,
    NarrativeAgentOutput,
    NpcPerformerAgentOutput,
    NpcPerformerChainResult,
    NpcSchedulerAgentOutput,
    StateAgentOutput,
)
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
from src.data.model.input.agent_narrative_input import NarrativeInfo
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
        self._dm_memory = DmMemory(memory_turns=self.config.agent.dm.memory_turns)
        self._world_snapshot_repository = self._build_world_snapshot_repository()
        self._narrative_repository = self._build_narrative_repository()

        self.state_agent = StateChangeAgent(llm_service=self.dm_agent.llm_service)
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
        self._restore_narrative_info_from_storage()
        self._persist_world_snapshot()

    def _build_world_snapshot_repository(self) -> Optional[SqliteWorldSnapshotRepository]:
        """按配置创建世界真值快照仓储；未配置时返回 None。"""
        sqlite_path = str(getattr(self.config.storage.world, "sqlite_path", "")).strip()
        if not sqlite_path:
            return None
        return SqliteWorldSnapshotRepository(sqlite_path=sqlite_path)

    def _build_narrative_repository(self) -> Optional[SqliteNarrativeRepository]:
        """按配置创建叙事真值仓储；未配置时返回 None。"""
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
        if self.mode == "phase2":
            return self._run_phase2_turn(
                raw_input=raw_input,
                actor_id=actor_id,
                turn_id=turn_id,
                trace_id=trace_id,
                causality_chain=causality_chain,
            )
        return asyncio.run(
            self.run_turn_async(
                raw_input=raw_input,
                actor_id=actor_id,
                turn_id=turn_id,
                trace_id=trace_id,
                causality_chain=causality_chain,
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
        if self.mode == "phase2":
            return self._run_phase2_turn(
                raw_input=raw_input,
                actor_id=actor_id,
                turn_id=turn_id,
                trace_id=trace_id,
                causality_chain=causality_chain,
            )
        return await self._run_phase3_turn_async(
            raw_input=raw_input,
            actor_id=actor_id,
            turn_id=turn_id,
            trace_id=trace_id,
            causality_chain=causality_chain,
        )

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
        e7_input = causality_chain or E7CausalityChain()

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
            narrative_task = asyncio.create_task(self._run_narrative_branch(narrative_input, branch_logs))
            scheduler_out, state_out, narrative_out = await asyncio.gather(scheduler_task, state_task, narrative_task)
            narrative_stream_events = self.narrative_agent.build_stream_events(narrative_out)
            narrative_stream_transport = NarrativeStreamInterface.build_transport_payload(narrative_stream_events)
        else:
            scheduler_out, state_out = await asyncio.gather(scheduler_task, state_task)

        fallback_error = state_out.get("fallback_error")
        performer_out: List[NpcPerformerAgentOutput] = []
        performer_chain: List[NpcPerformerChainResult] = []
        if fallback_error is None:
            performer_out, performer_chain = await self._run_performer_branch(
                scheduler_out=scheduler_out,
                source_actor_id=actor_id,
                turn_id=turn_id,
                trace_id=trace_id,
                world_version=prepared["world_version"],
                branch_logs=branch_logs,
            )
            merger_chain = self._merge_e7_chains(
                base_chain=merger_chain,
                extra_chains=[chain_item.e7 for chain_item in performer_chain],
            )

        merger_payload = None
        if narrative_out is None:
            narrative_payload = {
                "llm_output": {
                    "narrative_str": "",
                },
                "system_output": {},
                "stream_events": [],
                "stream_transport": {"sse": [], "websocket": []},
            }
        else:
            narrative_payload = narrative_out.model_dump(mode="json")
            narrative_payload["stream_events"] = narrative_stream_events
            narrative_payload["stream_transport"] = narrative_stream_transport
        if fallback_error is not None:
            narrative_payload = {
                "llm_output": {
                    "narrative_str": "",
                },
                "system_output": narrative_payload.get("system_output", {}),
                "stream_events": [],
                "stream_transport": {"sse": [], "websocket": []},
            }
        else:
            narrative_text = narrative_out.llm_output.narrative_str if narrative_out is not None else ""
            merger_out = await self._run_merger_branch(
                context=context,
                routed=routed,
                prepared=prepared,
                branch_logs=branch_logs,
                causality_chain=merger_chain,
                narrative_str=narrative_text,
            )
            merger_payload = merger_out.model_dump(mode="json")
            self._narrative_info.add_narrative(
                turn=turn_id,
                content=merger_out.llm_output.narrative_str,
                source="merger_agent",
                max_recent=self._narrative_recent_limit,
            )
            if narrative_text.strip():
                self._narrative_info.append_log(
                    turn=turn_id,
                    content=narrative_text,
                    source="narrative_agent",
                )
            self._persist_narrative_info()

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

            target_id = participant_ids[1]
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
                    agent_memory=self.world_state.get_character(npc_id).memory,
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
    ) -> NpcPerformerChainResult:
        """执行 NPC performer 的下游链路，并生成可并回主 e7 的结构化结果。"""
        llm_output = performer_output.llm_output
        check_result: Optional[CocCheckResult] = None
        check_error: Optional[str] = None

        try:
            check_result = self._run_npc_check(actor_id=npc_id, performer_output=performer_output)
        except ValueError as exc:
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
            target_id = next((char_id for char_id in participant_ids if char_id != actor_id), None)
            if target_id is None:
                raise ValueError("npc against routing requires target id")
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
    ) -> NarrativeAgentOutput:
        started = time.perf_counter()
        started_at = datetime.now(timezone.utc).isoformat()
        output = await asyncio.to_thread(self.narrative_agent.run, agent_input=agent_input)
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
        return output

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

        merger_input = MergerAgentInput(
            identity=AgentIdentity(id="merger", skill="merge committed narrative"),
            llm_input=MergerAgentLlmInput(
                e7=E7LlmView(narrative_causality=self._stringify_e7(causality_chain)),
                world_info=context["views"].narrative_view,
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

            output: StateAgentOutput = await asyncio.to_thread(
                self.state_agent.run,
                agent_input=current_input,
                retry_seq=retry_seq,
                patch_id=f"patch-{current_input.system_input.execution.turn_id}-{current_input.system_input.execution.trace_id}-{retry_seq}",
            )

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
