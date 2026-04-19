from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple

from pydantic import BaseModel, Field

from src.agent.llm.service import LLMServiceBase, LLMValidationError
from src.agent.prompt.dm_prompt import DM_SYSTEM_PROMPT
from src.data.model.agent_input import AvailableAttributeRef, DmAgentInput
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
	) -> DmAnalyzeResult:
		user_payload = agent_input.llm_input.model_dump(mode="json")
		user_payload.pop("narrative_info", None)
		agent_memory_payload = user_payload.get("agent_memory")
		if isinstance(agent_memory_payload, dict):
			agent_memory_payload.pop("dialogue_log", None)

		retries = 0
		errors: List[str] = []
		feedback: Optional[str] = None
		attr_ids, attr_name_to_id = self._build_attribute_refs(agent_input.llm_input.available_attributes)
		available_attributes = sorted(set(attr_ids))
		char_ids = {char.id for char in agent_input.llm_input.valid_characters if char.id}

		llm_output: Optional[DmAgentLlmOutput] = None
		for _ in range(self.max_retries + 1):
			try:
				llm_output = self.llm_service.call_llm_json(
					agent_name="dmagent",
					system_prompt=DM_SYSTEM_PROMPT,
					user_payload=user_payload,
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
				actor_id=agent_input.llm_input.e1.source_id,
				available_attributes=available_attributes,
				valid_character_ids=char_ids,
				attribute_name_to_id=attr_name_to_id,
			)
			if not errors:
				break

			retries += 1
			feedback = self._build_validation_feedback(
				errors=errors,
				attribute_ids=attr_ids,
				attribute_name_to_id=attr_name_to_id,
				valid_character_ids=char_ids,
			)

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
		actor_id: str,
		available_attributes: List[str],
		valid_character_ids: Set[str],
		attribute_name_to_id: Optional[Dict[str, str]] = None,
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

		normalized_attrs: List[str] = []
		attribute_name_to_id = attribute_name_to_id or {}
		for attr in attrs:
			canonical_attr = attribute_name_to_id.get(attr, attr)
			if canonical_attr not in available_attributes:
				errors.append(f"invalid attribute: {attr}")
				continue
			normalized_attrs.append(canonical_attr)

		if normalized_attrs:
			intent.attributes = normalized_attrs

		for char_id in ids:
			if char_id not in valid_character_ids:
				errors.append(f"invalid char id: {char_id}")

		if intent.routing_hint == "num":
			if len(ids) > 1:
				errors.append("num routing should not include multiple against_char_id values")
			return errors

		if not ids:
			errors.append("against routing requires against_char_id")
		else:
			if len(ids) < 2:
				errors.append("against routing requires at least 2 character ids")
			if len(set(ids)) != len(ids):
				errors.append("against routing contains duplicate character ids")
			if actor_id and actor_id not in ids:
				errors.append("against routing must include actor id")
			if actor_id and ids and ids[0] != actor_id:
				errors.append("against routing requires actor id as first against_char_id")

		return errors

	@staticmethod
	def _build_attribute_refs(available_attributes: List[AvailableAttributeRef]) -> Tuple[List[str], Dict[str, str]]:
		attr_ids: List[str] = []
		name_to_id: Dict[str, str] = {}
		for attr in available_attributes:
			if not attr.id:
				continue
			attr_ids.append(attr.id)
			if attr.name:
				name_to_id[attr.name] = attr.id
		return attr_ids, name_to_id

	@staticmethod
	def _build_validation_feedback(
		*,
		errors: List[str],
		attribute_ids: List[str],
		attribute_name_to_id: Dict[str, str],
		valid_character_ids: Set[str],
	) -> str:
		parts = list(errors)
		if attribute_ids:
			parts.append(f"allowed attribute ids: {', '.join(attribute_ids)}")
		if attribute_name_to_id:
			name_pairs = [f"{name}->{attr_id}" for name, attr_id in sorted(attribute_name_to_id.items())]
			parts.append(f"attribute name to id mapping: {', '.join(name_pairs)}")
		if valid_character_ids:
			parts.append(f"valid character ids: {', '.join(sorted(valid_character_ids))}")
		parts.append("when you output attributes or against_char_id, you must return exact ids from the provided lists")
		return " ; ".join(parts)
