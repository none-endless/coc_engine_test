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
    """负责将回合因果链压缩为可提交的叙事真值。"""

    def __init__(self, llm_service: LLMServiceBase) -> None:
        self.llm_service = llm_service

    def run(self, *, agent_input: MergerAgentInput) -> MergerAgentOutput:
        """消费 e7（及可选 narrative 文本），输出合并后的精简叙事。"""
        execution = agent_input.system_input.execution
        user_payload = agent_input.llm_input.model_dump(mode="json")
        user_payload.pop("narrative_info", None)

        llm_output = self.llm_service.call_llm_json(
            agent_name="merger",
            system_prompt=MERGER_SYSTEM_PROMPT,
            user_payload=user_payload,
            output_model=MergerAgentLlmOutput,
            retry_budget=0,
            validation_feedback=None,
        )

        if not llm_output.narrative_str.strip():
            if agent_input.llm_input.narrative_str.strip():
                llm_output.narrative_str = agent_input.llm_input.narrative_str
            else:
                llm_output.narrative_str = agent_input.llm_input.e7.narrative_causality

        return MergerAgentOutput(
            llm_output=llm_output,
            system_output=MergerAgentSystemOutput(
                trace_id=execution.trace_id,
                turn_id=execution.turn_id,
            ),
        )
