from __future__ import annotations

from src.agent.llm.service import LLMServiceBase
from src.agent.prompt.narrative_prompt import NARRATIVE_SYSTEM_PROMPT
from src.data.model.agent_input import NarrativeAgentInput
from src.data.model.agent_output import (
	NarrativeAgentLlmOutput,
	NarrativeAgentOutput,
	NarrativeAgentSystemOutput,
	NarrativeDraft,
	NarrativeDraftStatus,
)
from src.data.model.narrative import NarrativeStreamEvent


class NarrativeAgent:
	"""LLM-driven narrative draft generator."""

	def __init__(self, llm_service: LLMServiceBase) -> None:
		self.llm_service = llm_service

	def run(self, *, agent_input: NarrativeAgentInput) -> NarrativeAgentOutput:
		execution = agent_input.system_input.execution
		llm_output = self.llm_service.call_llm_json(
			agent_name="narrative",
			system_prompt=NARRATIVE_SYSTEM_PROMPT,
			user_payload=agent_input.llm_input.model_dump(mode="json"),
			output_model=NarrativeAgentLlmOutput,
			retry_budget=0,
			validation_feedback=None,
		)

		if llm_output.narrative_draft is None:
			llm_output.narrative_draft = NarrativeDraft(
				draft_id=f"draft-{execution.turn_id}-{execution.trace_id}",
				trace_id=execution.trace_id,
				turn_id=execution.turn_id,
				content=llm_output.narrative_str,
				visible_to_player=True,
				status=NarrativeDraftStatus.DRAFT,
			)
		else:
			llm_output.narrative_draft.trace_id = execution.trace_id
			llm_output.narrative_draft.turn_id = execution.turn_id
			if not llm_output.narrative_draft.content:
				llm_output.narrative_draft.content = llm_output.narrative_str
			llm_output.narrative_draft.status = NarrativeDraftStatus.DRAFT

		return NarrativeAgentOutput(
			llm_output=llm_output,
			system_output=NarrativeAgentSystemOutput(trace_id=execution.trace_id, turn_id=execution.turn_id),
		)

	@staticmethod
	def build_stream_events(output: NarrativeAgentOutput) -> list[dict]:
		"""将叙事文本切成前端可消费的流式事件。"""
		text = output.llm_output.narrative_str.strip()
		if not text:
			return []

		chunks = [segment for segment in text.replace("。", "。|").split("|") if segment]
		events = [
			NarrativeStreamEvent(
				event="narrative.delta",
				data={"index": index, "content": chunk},
			).model_dump(mode="json")
			for index, chunk in enumerate(chunks)
		]
		events.append(
			NarrativeStreamEvent(
				event="narrative.completed",
				data={
					"draft_id": output.llm_output.narrative_draft.draft_id if output.llm_output.narrative_draft else "",
					"content": text,
				},
			).model_dump(mode="json")
		)
		return events
