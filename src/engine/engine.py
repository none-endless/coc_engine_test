from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from src.agent.llm.evolution_agent import EvolutionAgent, EvolutionResult
from src.agent.llm.input_agent import DMAgent, DmAnalyzeResult
from src.agent.llm.merger_agent import MergerAgent
from src.agent.llm.narrative_agent import NarrativeAgent
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
from src.data.model.agent_output import CocCheckResult, MergerAgentOutput, NarrativeAgentOutput, NpcSchedulerAgentOutput, StateAgentOutput
from src.data.model.input.agent_chain_input import (
    DmAgentChainInput,
    E1InputInfo,
    E3RuleResult,
    E4EvolutionStepResult,
    E7CausalityChain,
    EvolutionAgentChainInput,
    FallbackError,
    MergerAgentChainInput,
    NarrativeAgentChainInput,
    NpcSchedulerAgentChainInput,
    StateChangeAgentChainInput,
)
from src.data.model.input.agent_memory_input import DmMemory
from src.data.model.input.agent_narrative_input import NarrativeInfo
from src.data.model.narrative import NarrativeDraftStatus
from src.data.model.world_state import WorldState
from src.rule.input_system import InputSystem
from src.rule.rule_system import RuleSystem
from src.rule.state_patch import StatePatchError, StatePatchRuntime
from src.utils.agent_io_logger import make_io_record
from src.utils.world_provider import WorldDataProvider


EngineMode = Literal["phase2", "phase3", "phase4"]


class Engine:
    """Unified engine entrypoint for phase2 serial mode and phase3 concurrent mode."""

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

        self.state_agent = StateChangeAgent(llm_service=self.dm_agent.llm_service)
        self.npc_scheduler_agent = NpcSchedulerAgent(llm_service=self.dm_agent.llm_service)
        self.narrative_agent = NarrativeAgent(llm_service=self.dm_agent.llm_service)
        self.merger_agent = MergerAgent(llm_service=self.dm_agent.llm_service)

        self.state_patch_runtime = StatePatchRuntime(world_state=self.world_state)
        self._state_commit_lock = asyncio.Lock()
        self._narrative_recent_limit = int(self.config.agent.narrative.recent_turns)

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
                e7=E7LlmView(narrative_causality=self._stringify_e7(e7_input)),
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
        else:
            scheduler_out, state_out = await asyncio.gather(scheduler_task, state_task)

        fallback_error = state_out.get("fallback_error")
        merger_payload = None
        if narrative_out is None:
            narrative_payload = {
                "llm_output": {
                    "narrative_str": "",
                    "narrative_draft": None,
                },
                "system_output": {},
                "stream_events": [],
            }
        else:
            narrative_payload = narrative_out.model_dump(mode="json")
            narrative_payload["stream_events"] = narrative_stream_events
        if fallback_error is not None:
            if narrative_out is not None and narrative_out.llm_output.narrative_draft is not None:
                narrative_payload["llm_output"]["narrative_draft"]["status"] = "discarded"
                narrative_out.llm_output.narrative_draft.status = NarrativeDraftStatus.DISCARDED
            narrative_payload = {
                "llm_output": {
                    "narrative_str": "",
                    "narrative_draft": None,
                },
                "system_output": narrative_payload.get("system_output", {}),
                "stream_events": [],
            }
        elif narrative_out is not None and narrative_out.llm_output.narrative_draft is not None:
            merger_out = await self._run_merger_branch(
                context=context,
                routed=routed,
                prepared=prepared,
                branch_logs=branch_logs,
                narrative_out=narrative_out,
            )
            merger_payload = merger_out.model_dump(mode="json")
            narrative_out.llm_output.narrative_draft.status = NarrativeDraftStatus.COMMITTED
            narrative_payload["llm_output"]["narrative_draft"] = narrative_out.llm_output.narrative_draft.model_dump(mode="json")
            narrative_payload["llm_output"]["narrative_str"] = merger_out.llm_output.narrative_str
            self._narrative_info.add_narrative(
                turn=turn_id,
                content=merger_out.llm_output.narrative_str,
                source="merger_agent",
                max_recent=self._narrative_recent_limit,
            )
            self._narrative_info.append_log(
                turn=turn_id,
                content=narrative_out.llm_output.narrative_draft.content,
                source="narrative_agent",
            )

        event = {
            "route": "phase3_concurrent_nl",
            "turn_id": turn_id,
            "trace_id": trace_id,
            "dm": self._serialize_dm_result(context["dm_result"]),
            "e3": context["e3_result"].model_dump(mode="json"),
            "evolution": evolution_result.model_dump(mode="json"),
            "npcscheduler": scheduler_out.model_dump(mode="json"),
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
        if not chain.narrative_list:
            return ""
        return " | ".join(str(entry) for entry in chain.narrative_list)

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
        narrative_out: NarrativeAgentOutput,
    ) -> MergerAgentOutput:
        """在状态提交成功后合并叙事草稿并写入叙事真值池。"""
        started = time.perf_counter()
        started_at = datetime.now(timezone.utc).isoformat()
        draft = narrative_out.llm_output.narrative_draft
        if draft is None:
            raise ValueError("merger branch requires narrative draft")

        causality_chain = context["e7_input"].model_copy(deep=True)
        causality_chain.narrative_list.append(
            {
                "trace_id": str(draft.trace_id),
                "turn_id": str(draft.turn_id),
                "draft_id": draft.draft_id,
                "content": draft.content,
            }
        )

        merger_input = MergerAgentInput(
            identity=AgentIdentity(id="merger", skill="merge committed narrative"),
            llm_input=MergerAgentLlmInput(
                e7=E7LlmView(narrative_causality=self._stringify_e7(causality_chain)),
                world_info=context["views"].narrative_view,
                narrative_info=self._narrative_info,
                narrative_draft=draft,
            ),
            system_input=MergerAgentSystemInput(
                chain_raw=MergerAgentChainInput(
                    e7=causality_chain,
                    narrative_draft=draft,
                ),
                execution=SystemExecutionMeta(
                    turn_id=draft.turn_id,
                    trace_id=draft.trace_id,
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
