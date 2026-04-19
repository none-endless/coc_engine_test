from __future__ import annotations

from typing import Callable, Dict, List, Optional, Tuple
from uuid import uuid4

from src.agent.llm.service import LLMServiceBase
from src.agent.prompt.narrative_prompt import NARRATIVE_SYSTEM_PROMPT
from src.data.model.agent_input import NarrativeAgentInput
from src.data.model.agent_output import (
	NarrativeAgentLlmOutput,
	NarrativeAgentOutput,
	NarrativeAgentSystemOutput,
)
from src.data.model.narrative import NarrativeStreamEvent


class NarrativeAgent:
	"""LLM-driven narrative string generator."""

	def __init__(self, llm_service: LLMServiceBase) -> None:
		self.llm_service = llm_service

	def run(self, *, agent_input: NarrativeAgentInput) -> NarrativeAgentOutput:
		execution = agent_input.system_input.execution
		user_payload = agent_input.llm_input.model_dump(mode="json")
		user_payload.pop("narrative_info", None)

		llm_output = self.llm_service.call_llm_json(
			agent_name="narrative",
			system_prompt=NARRATIVE_SYSTEM_PROMPT,
			user_payload=user_payload,
			output_model=NarrativeAgentLlmOutput,
			retry_budget=0,
			validation_feedback=None,
		)

		return NarrativeAgentOutput(
			llm_output=llm_output,
			system_output=NarrativeAgentSystemOutput(trace_id=execution.trace_id, turn_id=execution.turn_id),
		)

	def run_stream(
		self,
		*,
		agent_input: NarrativeAgentInput,
		source_kind: str,
		source_id: str,
		event_callback: Optional[Callable[[Dict], None]] = None,
	) -> Tuple[NarrativeAgentOutput, List[Dict]]:
		execution = agent_input.system_input.execution
		fragment_id = (
			f"t{execution.turn_id}-tr{execution.trace_id}-"
			f"{source_kind}-{source_id or 'unknown'}-{uuid4().hex[:8]}"
		)

		common_data = {
			"fragment_id": fragment_id,
			"source_kind": source_kind,
			"source_id": source_id,
			"trace_id": execution.trace_id,
			"turn_id": execution.turn_id,
		}
		events: List[Dict] = []

		def emit(event_name: str, extra_data: Optional[Dict] = None) -> None:
			payload = dict(common_data)
			if isinstance(extra_data, dict):
				payload.update(extra_data)
			event = NarrativeStreamEvent(event=event_name, data=payload).model_dump(mode="json")
			events.append(event)
			if callable(event_callback):
				try:
					event_callback(event)
				except Exception:
					# Never allow UI/event consumers to break narrative generation.
					pass

		emit("narrative.fragment.started")

		text = ""
		user_payload = agent_input.llm_input.model_dump(mode="json")
		user_payload.pop("narrative_info", None)
		stream_callable = getattr(self.llm_service, "call_llm_stream_text", None)
		if callable(stream_callable):
			chunks: List[str] = []
			stream_prompt = (
				f"{NARRATIVE_SYSTEM_PROMPT}\n\n"
				"你正在执行流式输出。请直接输出叙事正文纯文本，"
				"不要输出 JSON、不要输出代码块、不要输出额外字段名。"
			)
			for delta in stream_callable(
				agent_name="narrative",
				system_prompt=stream_prompt,
				user_payload=user_payload,
				validation_feedback=None,
			):
				normalized_delta = str(delta)
				if not normalized_delta:
					continue
				chunks.append(normalized_delta)
				emit("narrative.fragment.delta", {"delta": normalized_delta})
			text = "".join(chunks).strip()

		if not text:
			fallback = self.run(agent_input=agent_input)
			text = str(fallback.llm_output.narrative_str or "").strip()
			if text:
				emit("narrative.fragment.delta", {"delta": text})

		emit("narrative.fragment.completed", {"content": text})

		output = NarrativeAgentOutput(
			llm_output=NarrativeAgentLlmOutput(narrative_str=text),
			system_output=NarrativeAgentSystemOutput(trace_id=execution.trace_id, turn_id=execution.turn_id),
		)
		return output, events

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
					"trace_id": output.system_output.trace_id,
					"turn_id": output.system_output.turn_id,
					"content": text,
				},
			).model_dump(mode="json")
		)
		return events
