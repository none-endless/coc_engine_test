from __future__ import annotations

from src.agent.llm.service import LLMServiceBase
from src.agent.prompt.merger_prompt import MERGER_SYSTEM_PROMPT
from src.data.model.agent_input import MergerAgentInput
from src.data.model.agent_output import (
    MergerAgentLlmOutput,
    MergerAgentOutput,
    MergerAgentSystemOutput,
)


class MergerAgent:
    """负责将 narrative 草稿压缩为可提交的叙事真值。"""

    def __init__(self, llm_service: LLMServiceBase) -> None:
        self.llm_service = llm_service

    def run(self, *, agent_input: MergerAgentInput) -> MergerAgentOutput:
        """消费 e7 与 NarrativeDraft，输出合并后的精简叙事。"""
        execution = agent_input.system_input.execution
        llm_output = self.llm_service.call_llm_json(
            agent_name="merger",
            system_prompt=MERGER_SYSTEM_PROMPT,
            user_payload=agent_input.llm_input.model_dump(mode="json"),
            output_model=MergerAgentLlmOutput,
            retry_budget=0,
            validation_feedback=None,
        )

        if not llm_output.narrative_str.strip():
            llm_output.narrative_str = agent_input.llm_input.narrative_draft.content

        return MergerAgentOutput(
            llm_output=llm_output,
            system_output=MergerAgentSystemOutput(
                trace_id=execution.trace_id,
                turn_id=execution.turn_id,
            ),
        )
