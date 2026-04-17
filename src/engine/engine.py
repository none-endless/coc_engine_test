from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.agent.llm.evolution_agent import EvolutionAgent, EvolutionResult
from src.agent.llm.input_agent import DMAgent, DmAnalyzeResult
from src.agent.llm.narrative_agent import NarrativeAgent
from src.agent.llm.npc_schedul_agent import NpcSchedulerAgent
from src.agent.llm.service import LLMServiceBase
from src.agent.llm.statechange_agent import StateChangeAgent
from src.config.loader import ConfigLoader
from src.data.model.agent_input import (
    AgentIdentity,
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
from src.data.model.agent_output import NarrativeAgentOutput, NpcSchedulerAgentOutput, StateAgentOutput
from src.data.model.input.agent_chain_input import (
    DmAgentChainInput,
    E4EvolutionStepResult,
    E1InputInfo,
    E3RuleResult,
    E7CausalityChain,
    EvolutionAgentChainInput,
    FallbackError,
    NarrativeAgentChainInput,
    NpcSchedulerAgentChainInput,
    StateChangeAgentChainInput,
)
from src.data.model.input.agent_memory_input import DmMemory
from src.data.model.input.agent_narrative_input import NarrativeInfo
from src.data.model.world_state import WorldState
from src.rule.input_system import InputSystem
from src.rule.rule_system import CocCheckResult, RuleSystem
from src.rule.state_patch import StatePatchError, StatePatchRuntime
from src.utils.world_provider import WorldDataProvider


class Phase2Engine:
    """Serial natural-language pipeline: DM -> RuleSystem -> Evolution."""

    def __init__(
        self,
        world_state: WorldState,
        dm_max_retries: int = 2,
        llm_service: Optional[LLMServiceBase] = None,
        config_path: str = "config/config.yaml",
    ) -> None:
        self.world_state = world_state
        self.rule_system = RuleSystem(world_state=world_state)
        self.world_provider = WorldDataProvider(world_state=world_state)

        if llm_service is None:
            cfg = ConfigLoader.load(config_path=config_path)
            llm_service = LLMServiceBase(config=cfg)

        self.dm_agent = DMAgent(llm_service=llm_service, max_retries=dm_max_retries)
        self.evolution_agent = EvolutionAgent(llm_service=llm_service)
        self._current_actor_id = ""
        self._routing_logs: List[Dict[str, Any]] = []
        self._narrative_info = NarrativeInfo()
        self._dm_memory = DmMemory()

        self.input_system = InputSystem(rule_system=self.rule_system, dm_handler=self._dm_handler)

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
        return analyzed.model_dump(mode="json")

    def run_turn(
        self,
        raw_input: str,
        actor_id: str,
        turn_id: int,
        trace_id: int,
        causality_chain: Optional[E7CausalityChain] = None,
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

        if routed.route == "rule_system_meta":
            event = {
                "route": routed.route,
                "payload": routed.payload,
                "turn_id": turn_id,
                "trace_id": trace_id,
            }
            self._routing_logs.append(event)
            return event

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
            e3_result = E3RuleResult(intent=dm_result.intent_info.intent, success=coc_result.result_type)

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

        event = {
            "route": "serial_nl",
            "turn_id": turn_id,
            "trace_id": trace_id,
            "dm": dm_result.output.model_dump(mode="json"),
            "e3": e3_result.model_dump(mode="json"),
            "evolution": evolution_result.model_dump(mode="json"),
            "narrative_triggered": evolution_result.visible_to_player,
        }
        self._routing_logs.append(event)
        return event

    def get_routing_logs(self) -> List[Dict[str, Any]]:
        return list(self._routing_logs)

    def _run_check(self, actor_id: str, dm_result: DmAnalyzeResult) -> CocCheckResult:
        actor = self.world_state.get_character(actor_id)
        attrs = dm_result.intent_info.attributes or []
        if not attrs:
            raise ValueError("check routing without attributes")

        attr_name = attrs[0]
        attr = actor.attributes.get(attr_name)
        if attr is None:
            raise ValueError(f"attribute not found on actor: {attr_name}")

        return self.rule_system.run_coc_check(actor_id=actor_id, attribute_value=attr.value)

    @staticmethod
    def _stringify_e7(chain: E7CausalityChain) -> str:
        if not chain.narrative_list:
            return ""
        return " | ".join(str(entry) for entry in chain.narrative_list)


class Phase3Engine(Phase2Engine):
    """Concurrent phase-3 pipeline: summary -> scheduler/state/narrative branches."""

    def __init__(
        self,
        world_state: WorldState,
        dm_max_retries: int = 2,
        llm_service: Optional[LLMServiceBase] = None,
        config_path: str = "config/config.yaml",
    ) -> None:
        super().__init__(
            world_state=world_state,
            dm_max_retries=dm_max_retries,
            llm_service=llm_service,
            config_path=config_path,
        )
        cfg = getattr(self.dm_agent.llm_service, "config", None)
        if cfg is None:
            cfg = ConfigLoader.load(config_path=config_path)
        self.config = cfg

        shared_llm = self.dm_agent.llm_service
        self.state_agent = StateChangeAgent(llm_service=shared_llm)
        self.npc_scheduler_agent = NpcSchedulerAgent(llm_service=shared_llm)
        self.narrative_agent = NarrativeAgent(llm_service=shared_llm)

        self.state_patch_runtime = StatePatchRuntime(world_state=self.world_state)
        self._state_commit_lock = asyncio.Lock()

    def run_turn(
        self,
        raw_input: str,
        actor_id: str,
        turn_id: int,
        trace_id: int,
        causality_chain: Optional[E7CausalityChain] = None,
    ) -> Dict[str, Any]:
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
        self._current_actor_id = actor_id
        world_version = int(self.world_state.get_snapshot().get("version", 0))

        routed = self.input_system.dispatch(
            raw_input=raw_input,
            actor_id=actor_id,
            turn=turn_id,
            trace_id=trace_id,
            world_version=world_version,
        )

        if routed.route == "rule_system_meta":
            event = {
                "route": routed.route,
                "payload": routed.payload,
                "turn_id": turn_id,
                "trace_id": trace_id,
            }
            self._routing_logs.append(event)
            return event

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
            e3_result = E3RuleResult(intent=dm_result.intent_info.intent, success=coc_result.result_type)

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

        evolution_result = self.evolution_agent.evolve(agent_input=evo_input, causality_chain=causality_chain)
        e4 = E4EvolutionLlmView(summary=evolution_result.summary)
        e4_chain = E4EvolutionStepResult(summary=evolution_result.summary)
        checkpoint = self.world_state.capture_checkpoint()

        scheduler_input = NpcSchedulerAgentInput(
            identity=AgentIdentity(id="npcscheduler", skill="schedule npc branch"),
            llm_input=NpcSchedulerAgentLlmInput(
                e4=e4,
                world_info=views.npc_scheduler_view,
                narrative_info=self._narrative_info,
            ),
            system_input=NpcSchedulerAgentSystemInput(
                chain_raw=NpcSchedulerAgentChainInput(e4=e4_chain),
                execution=SystemExecutionMeta(
                    turn_id=turn_id,
                    trace_id=trace_id,
                    world_version=world_version,
                    event_id=routed.envelope.event_id,
                    debug={"branch": "npc_scheduler"},
                ),
            ),
        )

        state_input = StateAgentInput(
            identity=AgentIdentity(id="state", skill="generate state patch"),
            llm_input=StateAgentLlmInput(
                e4=e4,
                world_info=views.state_agent_view,
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

        narrative_input = NarrativeAgentInput(
            identity=AgentIdentity(id="narrative", skill="generate narrative draft"),
            llm_input=NarrativeAgentLlmInput(
                e4=e4,
                world_info=views.narrative_view,
                narrative_info=self._narrative_info,
            ),
            system_input=NarrativeAgentSystemInput(
                chain_raw=NarrativeAgentChainInput(e4=e4_chain),
                execution=SystemExecutionMeta(
                    turn_id=turn_id,
                    trace_id=trace_id,
                    world_version=world_version,
                    event_id=routed.envelope.event_id,
                    debug={"branch": "narrative"},
                ),
            ),
        )

        branch_logs: List[Dict[str, Any]] = []
        scheduler_task = asyncio.create_task(self._run_scheduler_branch(scheduler_input, branch_logs))
        state_task = asyncio.create_task(self._run_state_branch(state_input, checkpoint, branch_logs))
        narrative_task = asyncio.create_task(self._run_narrative_branch(narrative_input, branch_logs))
        scheduler_out, state_out, narrative_out = await asyncio.gather(scheduler_task, state_task, narrative_task)

        fallback_error = state_out.get("fallback_error")
        narrative_payload = narrative_out.model_dump(mode="json")
        if fallback_error is not None:
            if narrative_out.llm_output.narrative_draft is not None:
                narrative_payload["llm_output"]["narrative_draft"]["status"] = "discarded"
            narrative_payload = {
                "llm_output": {
                    "narrative_str": "",
                    "narrative_draft": None,
                },
                "system_output": narrative_payload.get("system_output", {}),
            }

        event = {
            "route": "phase3_concurrent_nl",
            "turn_id": turn_id,
            "trace_id": trace_id,
            "dm": dm_result.output.model_dump(mode="json"),
            "e3": e3_result.model_dump(mode="json"),
            "evolution": evolution_result.model_dump(mode="json"),
            "npcscheduler": scheduler_out.model_dump(mode="json"),
            "state": state_out,
            "narrative": narrative_payload,
            "narrative_triggered": fallback_error is None and evolution_result.visible_to_player,
            "fallback_error": fallback_error,
            "parallel_timeline": branch_logs,
            "terminated": fallback_error is not None,
        }
        self._routing_logs.append(event)
        return event

    async def _run_scheduler_branch(
        self,
        agent_input: NpcSchedulerAgentInput,
        branch_logs: List[Dict[str, Any]],
    ) -> NpcSchedulerAgentOutput:
        started = time.perf_counter()
        started_at = datetime.now(timezone.utc).isoformat()
        output = await asyncio.to_thread(self.npc_scheduler_agent.run, agent_input=agent_input)
        ended = time.perf_counter()
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
