from __future__ import annotations

from typing import List, Optional, Set

from pydantic import BaseModel, Field

from src.agent.llm.service import LLMServiceBase, LLMValidationError
from src.agent.prompt.dm_prompt import DM_SYSTEM_PROMPT
from src.data.model.agent_input import DmAgentInput
from src.data.model.agent_output import DmAgentLlmOutput, DmAgentOutput, DmAgentSystemOutput


class DmAnalyzeResult(BaseModel):
	output: DmAgentOutput
	retries: int = Field(default=0)
	validation_errors: List[str] = Field(default_factory=list)

	@property
	def intent_info(self):
		return self.output.llm_output.intent_info


class DMAgent:
	"""LLM-driven DM agent with output self-healing retries."""

	def __init__(self, llm_service: LLMServiceBase, max_retries: int = 2) -> None:
		self.llm_service = llm_service
		self.max_retries = max_retries

	def run(
		self,
		agent_input: DmAgentInput,
		*,
		available_attributes: List[str],
		valid_character_ids: Set[str],
	) -> DmAnalyzeResult:
		retries = 0
		errors: List[str] = []
		feedback: Optional[str] = None

		llm_output: Optional[DmAgentLlmOutput] = None
		for _ in range(self.max_retries + 1):
			try:
				llm_output = self.llm_service.call_llm_json(
					agent_name="dmagent",
					system_prompt=DM_SYSTEM_PROMPT,
					user_payload=agent_input.llm_input.model_dump(mode="json"),
					output_model=DmAgentLlmOutput,
					retry_budget=0,
					validation_feedback=feedback,
				)
			except LLMValidationError as exc:
				retries += 1
				errors = [f"schema_error: {x.get('loc')} {x.get('msg')}" for x in exc.errors]
				feedback = " ; ".join(errors)
				continue

			errors = self._validate_semantics(
				llm_output=llm_output,
				available_attributes=available_attributes,
				valid_character_ids=valid_character_ids,
			)
			if not errors:
				break

			retries += 1
			feedback = " ; ".join(errors)

		if llm_output is None:
			raise RuntimeError("DM agent produced no output")

		if errors:
			llm_output = DmAgentLlmOutput.model_validate(
				{
					"intent_info": {
						"intent": "blocked",
						"routing_hint": None,
						"attributes": [],
						"against_char_id": [],
						"difficulty": None,
						"dm_reply": "你的输入包含无效属性或目标，请重试。",
					}
				}
			)

		return DmAnalyzeResult(
			output=DmAgentOutput(
				llm_output=llm_output,
				system_output=DmAgentSystemOutput(e1_view=agent_input.llm_input.e1),
			),
			retries=retries,
			validation_errors=errors,
		)

	@staticmethod
	def _validate_semantics(
		*,
		llm_output: DmAgentLlmOutput,
		available_attributes: List[str],
		valid_character_ids: Set[str],
	) -> List[str]:
		errors: List[str] = []
		intent = llm_output.intent_info

		if intent.routing_hint not in {None, "num", "against"}:
			errors.append("routing_hint must be null/num/against")

		attrs = intent.attributes or []
		ids = intent.against_char_id or []

		if intent.routing_hint is None:
			if attrs:
				errors.append("routing_hint is null but attributes is not empty")
			if ids:
				errors.append("routing_hint is null but against_char_id is not empty")
			return errors

		if not attrs:
			errors.append("check routing requires attributes")
		if not ids:
			errors.append("check routing requires against_char_id")

		for attr in attrs:
			if attr not in available_attributes:
				errors.append(f"invalid attribute: {attr}")

		for char_id in ids:
			if char_id not in valid_character_ids:
				errors.append(f"invalid char id: {char_id}")

		if intent.routing_hint == "against" and len(ids) < 2:
			errors.append("against routing requires at least 2 character ids")

		return errors
