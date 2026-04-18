from __future__ import annotations

from typing import Optional

from src.agent.llm.service import LLMServiceBase
from src.agent.prompt.consistency_prompt import CONSISTENCY_SYSTEM_PROMPT
from src.data.model.agent_input import ConsistencyAgentInput
from src.data.model.agent_output import (
    ConsistencyAgentLlmOutput,
    ConsistencyAgentOutput,
    ConsistencyAgentSystemOutput,
    PatchMeta,
)


class ConsistencyAgent:
    """一致性维护代理，负责生成可复用的状态修补 DSL。"""

    def __init__(self, llm_service: LLMServiceBase) -> None:
        self.llm_service = llm_service

    def run(
        self,
        *,
        agent_input: ConsistencyAgentInput,
        retry_seq: int = 0,
        patch_id: Optional[str] = None,
        validation_feedback: Optional[dict] = None,
    ) -> ConsistencyAgentOutput:
        """调用 LLM 生成一致性修补结果，并补齐系统侧补丁元数据。"""
        llm_output = self.llm_service.call_llm_json(
            agent_name="consistency",
            system_prompt=CONSISTENCY_SYSTEM_PROMPT,
            user_payload=agent_input.llm_input.model_dump(mode="json"),
            output_model=ConsistencyAgentLlmOutput,
            retry_budget=0,
            validation_feedback=validation_feedback,
        )

        execution = agent_input.system_input.execution
        patch_meta = PatchMeta(
            trace_id=execution.trace_id,
            turn_id=execution.turn_id,
            retry_seq=retry_seq,
            patch_id=patch_id,
            expected_version=execution.world_version,
        )
        return ConsistencyAgentOutput(
            llm_output=llm_output,
            system_output=ConsistencyAgentSystemOutput(patch_meta=patch_meta),
        )
