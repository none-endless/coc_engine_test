from __future__ import annotations

from src.agent.llm.service import LLMServiceBase
from src.agent.prompt.npc_scheduler_prompt import NPC_SCHEDULER_SYSTEM_PROMPT
from src.data.model.agent_input import NpcSchedulerAgentInput
from src.data.model.agent_output import NpcSchedulerAgentLlmOutput, NpcSchedulerAgentOutput, NpcSchedulerAgentSystemOutput


class NpcSchedulerAgent:
	"""LLM-driven scheduler for NPC side branch."""

	def __init__(self, llm_service: LLMServiceBase) -> None:
		self.llm_service = llm_service

	def run(self, *, agent_input: NpcSchedulerAgentInput) -> NpcSchedulerAgentOutput:
		execution = agent_input.system_input.execution
		llm_output = self.llm_service.call_llm_json(
			agent_name="npc_scheduler",
			system_prompt=NPC_SCHEDULER_SYSTEM_PROMPT,
			user_payload=agent_input.llm_input.model_dump(mode="json"),
			output_model=NpcSchedulerAgentLlmOutput,
			retry_budget=0,
			validation_feedback=None,
		)
		return NpcSchedulerAgentOutput(
			llm_output=llm_output,
			system_output=NpcSchedulerAgentSystemOutput(trace_id=execution.trace_id, turn_id=execution.turn_id),
		)
