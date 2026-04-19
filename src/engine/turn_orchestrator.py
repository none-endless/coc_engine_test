from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from src.agent.llm.input_agent import DmAnalyzeResult
from src.data.model.agent_input import (
    AgentIdentity,
    E4EvolutionLlmView,
    E4EvolutionStepResult,
    NarrativeAgentInput,
    NarrativeAgentLlmInput,
    NarrativeAgentSystemInput,
    NpcSchedulerAgentInput,
    NpcSchedulerAgentLlmInput,
    NpcSchedulerAgentSystemInput,
    StateAgentInput,
    StateAgentLlmInput,
    StateAgentSystemInput,
    SystemExecutionMeta,
    SystemRetryControl,
)
from src.data.model.agent_output import NarrativeAgentOutput, NpcPerformerAgentOutput, NpcPerformerChainResult
from src.data.model.input.agent_chain_input import (
    E7CausalityChain,
    NarrativeAgentChainInput,
    NpcSchedulerAgentChainInput,
    StateChangeAgentChainInput,
)
from src.interface.narrative_stream_interface import NarrativeStreamInterface

if TYPE_CHECKING:
    from src.engine.engine import Engine


class TurnOrchestrator:
    """Owns phase3 turn orchestration so Engine can stay focused on dependency wiring."""

    def __init__(self, engine: "Engine") -> None:
        self._engine = engine

    async def run_phase3_turn_async(
        self,
        *,
        raw_input: str,
        actor_id: str,
        turn_id: int,
        trace_id: int,
        causality_chain: Optional[E7CausalityChain],
    ) -> Dict[str, Any]:
        e = self._engine
        prepared = e._prepare_turn_context(
            raw_input=raw_input,
            actor_id=actor_id,
            turn_id=turn_id,
            trace_id=trace_id,
        )
        routed = prepared["routed"]
        if routed.route == "rule_system_meta":
            return e._handle_meta_route(routed=routed, turn_id=turn_id, trace_id=trace_id)

        dm_result = DmAnalyzeResult.model_validate(routed.payload)
        direct_event = e._build_dm_direct_reply_event(dm_result=dm_result, turn_id=turn_id, trace_id=trace_id)
        if direct_event is not None:
            e._routing_logs.append(direct_event)
            e._record_io(
                kind="turn_result",
                agent_name="engine",
                input_data={"route": "phase3_dm_direct_reply", "turn_id": turn_id, "trace_id": trace_id, "actor_id": actor_id},
                output_data=direct_event,
            )
            return direct_event

        context = e._build_nl_context(
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
        checkpoint = e.world_state.capture_checkpoint()

        scheduler_input = NpcSchedulerAgentInput(
            identity=AgentIdentity(id="npcscheduler", skill="schedule npc branch"),
            llm_input=NpcSchedulerAgentLlmInput(
                e4=e4,
                world_info=context["views"].npc_scheduler_view,
                narrative_info=e._narrative_info,
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
                    retry_budget=e.config.system.max_retry_count,
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
        scheduler_task = asyncio.create_task(e._run_scheduler_branch(scheduler_input, branch_logs))
        state_task = asyncio.create_task(e._run_state_branch(state_input, checkpoint, branch_logs))
        narrative_out: Optional[NarrativeAgentOutput] = None
        narrative_stream_events: List[Dict[str, Any]] = []
        narrative_stream_transport: Dict[str, List[Any]] = {"sse": [], "websocket": []}
        if evolution_result.visible_to_player:
            narrative_input = NarrativeAgentInput(
                identity=AgentIdentity(id="narrative", skill="generate narrative draft"),
                llm_input=NarrativeAgentLlmInput(
                    e4=e4,
                    world_info=context["views"].narrative_view,
                    narrative_info=e._narrative_info,
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
                e._run_narrative_branch(
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
            performer_out, performer_chain, npc_fallback_error = await e._run_performer_branch(
                scheduler_out=scheduler_out,
                source_actor_id=actor_id,
                turn_id=turn_id,
                trace_id=trace_id,
                world_version=prepared["world_version"],
                branch_logs=branch_logs,
                narrative_stream_events=narrative_stream_events,
            )
            if npc_fallback_error is not None:
                fallback_error = npc_fallback_error
            else:
                npc_visible_narratives = e._collect_npc_visible_narrative_texts(performer_chain)
                merger_chain = e._merge_e7_chains(
                    base_chain=merger_chain,
                    extra_chains=[chain_item.e7 for chain_item in performer_chain],
                )

        narrative_fragments = e._collect_narrative_fragments_from_events(narrative_stream_events)
        aggregated_raw = e._compose_fragment_aggregate_text(narrative_fragments)
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
            merger_narrative_input = aggregated_raw or e._compose_merger_narrative_input(
                player_narrative_text,
                npc_visible_narratives,
            )
            llm_payload = narrative_payload.get("llm_output", {})
            if isinstance(llm_payload, dict):
                llm_payload["narrative_str"] = merger_narrative_input

            merger_out = await e._run_merger_branch(
                context=context,
                routed=routed,
                prepared=prepared,
                branch_logs=branch_logs,
                causality_chain=merger_chain,
                narrative_str=merger_narrative_input,
            )
            merger_payload = merger_out.model_dump(mode="json")
            e._narrative_truth_manager.commit_merged_narrative(
                repository=e._narrative_repository,
                narrative_info=e._narrative_info,
                turn_id=turn_id,
                merged_text=merger_out.llm_output.narrative_str,
                player_narrative_text=player_narrative_text,
                npc_visible_narratives=npc_visible_narratives,
                recent_limit=e._narrative_recent_limit,
                emit_event=e._emit_narrative_event,
            )

        committed_summary = ""
        if fallback_error is None:
            if merger_payload is not None:
                committed_summary = merger_payload.get("llm_output", {}).get("narrative_str", "")
            if not committed_summary:
                committed_summary = evolution_result.summary
            e._append_recent_change_log(turn_id=turn_id, route="phase3_concurrent_nl", summary=committed_summary)

        consistency_payload = None
        if fallback_error is None:
            consistency_payload = await e._consistency_orchestrator.run_consistency_cycle(
                turn_id=turn_id,
                trace_id=trace_id,
            )
            if (
                consistency_payload is not None
                and consistency_payload.get("blocked")
                and e.config.consistency.block_on_failure
            ):
                fallback_error = {
                    "code": "CONSISTENCY_BLOCKED",
                    "message": consistency_payload.get("system_message") or e.config.system.fallback_error,
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
            "dm": e._serialize_dm_result(context["dm_result"]),
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
            "narrative_info": e._narrative_info.model_dump(mode="json"),
        }
        e._routing_logs.append(event)
        e._record_io(
            kind="turn_result",
            agent_name="engine",
            input_data={"route": "phase3", "turn_id": turn_id, "trace_id": trace_id, "actor_id": actor_id},
            output_data=event,
        )
        return event
