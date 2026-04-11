"""agent_output 序列化示例。

该文件用于验证输出模型可构造、可序列化，便于联调时快速对照字段结构。
"""

from data.model.agent_output import (
    DmAgentLlmOutput,
    DmAgentOutput,
    DmAgentSystemOutput,
    NpcSchedulerAgentLlmOutput,
    NpcSchedulerAgentOutput,
    NpcSchedulerAgentSystemOutput,
    NpcSchedulerStepResultOutput,
    PatchMeta,
    StateAgentLlmOutput,
    StateAgentOutput,
    StateAgentSystemOutput,
    StateChangeOp,
    StateOperator,
)
from data.model.agent_input import E1LlmView
from data.model.input.agent_chain_input import E2IntentInfo


def build_dm_output_example() -> DmAgentOutput:
    return DmAgentOutput(
        llm_output=DmAgentLlmOutput(
            intent_info=E2IntentInfo(
                intent="inspect_room",
                routing_hint=None,
                attributes=None,
                charlist=None,
                hard=None,
                is_dialogue=None,
            )
        ),
        system_output=DmAgentSystemOutput(
            e1_view=E1LlmView(raw_text="我查看房间", source_id="char-player-0000")
        ),
    )


def build_state_output_example() -> StateAgentOutput:
    return StateAgentOutput(
        llm_output=StateAgentLlmOutput(
            changes=[
                StateChangeOp(
                    op=StateOperator.MOVE,
                    target_path="item-room_key-0008.location",
                    value="char-player-0000",
                    reason="玩家拾取钥匙",
                ),
                StateChangeOp(
                    op=StateOperator.UPDATE,
                    target_path="char-player-0000.attributes.san.value",
                    value=95,
                ),
            ]
        ),
        system_output=StateAgentSystemOutput(
            patch_meta=PatchMeta(
                trace_id=2,
                turn_id=12,
                retry_seq=0,
                patch_id="patch-turn12-0001",
                expected_version=134,
            )
        ),
    )


def build_scheduler_output_example() -> NpcSchedulerAgentOutput:
    return NpcSchedulerAgentOutput(
        llm_output=NpcSchedulerAgentLlmOutput(
            step_result=NpcSchedulerStepResultOutput(
                summary="玩家拿起钥匙，酒馆老板注意到异常动静。",
                extra_npc_context={
                    "char-innkeeper-0001": "优先观察玩家并给出试探性发言",
                    "char-guard-0002": None,
                },
            )
        ),
        system_output=NpcSchedulerAgentSystemOutput(trace_id=2, turn_id=12),
    )


def dump_examples() -> None:
    dm_output = build_dm_output_example()
    state_output = build_state_output_example()
    scheduler_output = build_scheduler_output_example()

    print("DM Output:")
    print(dm_output.model_dump(mode="json", indent=2))
    print("\nState Output:")
    print(state_output.model_dump(mode="json", indent=2))
    print("\nScheduler Output:")
    print(scheduler_output.model_dump(mode="json", indent=2))


if __name__ == "__main__":
    dump_examples()
