from __future__ import annotations

from typing import Optional

from src.agent.llm.service import LLMServiceBase
from src.agent.prompt.state_change_prompt import STATE_CHANGE_SYSTEM_PROMPT
from src.data.model.agent_input import StateAgentInput
from src.data.model.agent_output import PatchMeta, StateAgentLlmOutput, StateAgentOutput, StateAgentSystemOutput


class StateChangeAgent:
	"""LLM-driven state patch generator."""

	def __init__(self, llm_service: LLMServiceBase) -> None:
		self.llm_service = llm_service

	def run(
		self,
		*,
		agent_input: StateAgentInput,
		retry_seq: int = 0,
		patch_id: Optional[str] = None,
	) -> StateAgentOutput:
		llm_output = self.llm_service.call_llm_json(
			agent_name="state_change",
			system_prompt=STATE_CHANGE_SYSTEM_PROMPT,
			user_payload=agent_input.llm_input.model_dump(mode="json"),
			output_model=StateAgentLlmOutput,
			retry_budget=0,
			validation_feedback=None,
		)

		execution = agent_input.system_input.execution
		patch_meta = PatchMeta(
			trace_id=execution.trace_id,
			turn_id=execution.turn_id,
			retry_seq=retry_seq,
			patch_id=patch_id,
			expected_version=execution.world_version,
		)
		return StateAgentOutput(
			llm_output=llm_output,
			system_output=StateAgentSystemOutput(patch_meta=patch_meta),
		)
