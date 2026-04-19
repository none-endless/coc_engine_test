from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from src.agent.llm.service import LLMServiceBase, LLMServiceError
from src.agent.prompt.consistency_prompt import CONSISTENCY_SYSTEM_PROMPT
from src.data.model.agent_input import ConsistencyAgentInput
from src.data.model.agent_output import (
    ConsistencyAgentLlmOutput,
    ConsistencyAgentOutput,
    ConsistencyAgentSystemOutput,
    PatchMeta,
)


@dataclass
class ConsistencyRetryResult:
    output: Optional[ConsistencyAgentOutput]
    retry_count: int
    error_history: List[Dict[str, Any]]
    exhausted: bool


class ConsistencyAgent:
    """一致性维护代理，负责生成最小化压缩摘要列表。"""

    def __init__(self, llm_service: LLMServiceBase) -> None:
        self.llm_service = llm_service

    def run(
        self,
        *,
        agent_input: ConsistencyAgentInput,
        retry_seq: int = 0,
        patch_id: Optional[str] = None,
        validation_feedback: Optional[Dict[str, Any]] = None,
    ) -> ConsistencyAgentOutput:
        """调用 LLM 生成一致性压缩结果，并补齐系统侧补丁元数据。"""
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

    def run_with_retry(
        self,
        *,
        agent_input: ConsistencyAgentInput,
        max_retry: int,
        patch_id_prefix: str,
        validate_output: Optional[Callable[[ConsistencyAgentOutput], None]] = None,
    ) -> ConsistencyRetryResult:
        """统一处理一致性重试，包括 LLM 服务异常与系统侧校验反馈。"""
        retry_limit = max(0, int(max_retry))
        validation_feedback: Optional[Dict[str, Any]] = None
        error_history: List[Dict[str, Any]] = []
        final_output: Optional[ConsistencyAgentOutput] = None

        for retry_seq in range(retry_limit + 1):
            try:
                output = self.run(
                    agent_input=agent_input,
                    retry_seq=retry_seq,
                    patch_id=f"{patch_id_prefix}-{retry_seq}",
                    validation_feedback=validation_feedback,
                )
            except LLMServiceError as exc:
                message = f"一致性维护服务不可用: {str(exc)}"
                validation_feedback = {"message": message}
                error_history.append(
                    {
                        "message": message,
                        "retry_seq": retry_seq,
                        "error_type": "llm_service_error",
                    }
                )
                continue

            final_output = output
            if validate_output is None or not output.llm_output.can_proceed:
                return ConsistencyRetryResult(
                    output=output,
                    retry_count=retry_seq,
                    error_history=error_history,
                    exhausted=False,
                )

            try:
                validate_output(output)
                return ConsistencyRetryResult(
                    output=output,
                    retry_count=retry_seq,
                    error_history=error_history,
                    exhausted=False,
                )
            except ValueError as exc:
                validation_feedback = {"message": str(exc)}
                error_history.append({"message": str(exc), "retry_seq": retry_seq})

        return ConsistencyRetryResult(
            output=final_output,
            retry_count=retry_limit,
            error_history=error_history,
            exhausted=True,
        )
