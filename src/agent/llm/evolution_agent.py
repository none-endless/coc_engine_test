from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from src.agent.llm.service import LLMServiceBase
from src.agent.prompt.evolution_prompt import EVOLUTION_SYSTEM_PROMPT
from src.data.model.agent_input import EvolutionAgentInput
from src.data.model.agent_output import EvolutionAgentLlmOutput, EvolutionAgentOutput
from src.data.model.input.agent_chain_input import E7CausalityChain


class EvolutionResult(BaseModel):
	summary: str = Field(default="")
	visible_to_player: bool = Field(default=True)
	turn_id: int = Field(default=0)
	trace_id: int = Field(default=0)
	should_skip_narrative: bool = Field(default=False)
	e7: E7CausalityChain = Field(default_factory=E7CausalityChain)
	output: EvolutionAgentOutput = Field(default_factory=lambda: EvolutionAgentOutput(llm_output=EvolutionAgentLlmOutput()))


class EvolutionAgent:
	"""LLM-driven Evolution agent."""

	def __init__(self, llm_service: LLMServiceBase) -> None:
		self.llm_service = llm_service

	def run(
		self,
		agent_input: EvolutionAgentInput,
		*,
		causality_chain: Optional[E7CausalityChain] = None,) -> EvolutionResult:
		user_payload = agent_input.llm_input.model_dump(mode="json")
		user_payload.pop("narrative_info", None)

		llm_output = self.llm_service.call_llm_json(
			agent_name="evolution",
			system_prompt=EVOLUTION_SYSTEM_PROMPT,
			user_payload=user_payload,
			output_model=EvolutionAgentLlmOutput,
			retry_budget=0,
			validation_feedback=None,
		)

		turn_id = agent_input.system_input.execution.turn_id
		trace_id = agent_input.system_input.execution.trace_id
		summary = llm_output.summary

		if f"turn={turn_id}" not in summary:
			summary = f"turn={turn_id} | {summary}"
		if f"trace={trace_id}" not in summary:
			summary = f"trace={trace_id} | {summary}"

		llm_output.summary = summary

		if causality_chain is None:
			chain = E7CausalityChain()
		else:
			chain = causality_chain.model_copy(deep=True)
		chain.narrative_list.append(
			{
				"source": "evolution",
				"trace_id": str(trace_id),
				"turn_id": str(turn_id),
				"summary": summary,
				"visible_to_player": "true" if llm_output.visible_to_player else "false",
			}
		)

		out = EvolutionAgentOutput(llm_output=llm_output, system_output=None)
		return EvolutionResult(
			summary=summary,
			visible_to_player=llm_output.visible_to_player,
			turn_id=turn_id,
			trace_id=trace_id,
			should_skip_narrative=not llm_output.visible_to_player,
			e7=chain,
			output=out,
		)

	def evolve(
		self,
		*,
		agent_input: EvolutionAgentInput,
		causality_chain: Optional[E7CausalityChain] = None,
	) -> EvolutionResult:
		return self.run(agent_input=agent_input, causality_chain=causality_chain)
