# Phase4 变更记录：e7 合并链路与 narrative 输出收敛

## 变更目标

1. evolution 生成的 summary 无论是否对玩家可见，都必须进入回合因果链 e7，并交给 merger 合并。
2. narrative_agent 仅输出 narrative_str，不再输出 narrative_draft。

## 实施内容

- 模型契约更新：
  - `NarrativeAgentLlmOutput` 删除 `narrative_draft` 字段。
  - `MergerAgentLlmInput` 改为消费 `e7 + 可选 narrative_str`。
  - `MergerAgentChainInput` 删除 `narrative_draft`，仅保留 `e7`。
- 执行链路更新：
  - `EvolutionAgent` 统一将 summary 写入 `e7.narrative_list`（包含 `visible_to_player` 标记）。
  - `Engine` 在状态提交成功后总是执行 merger 分支；可见分支有 narrative_str，不可见分支 narrative_str 为空。
  - narrative 分支流式完成事件改为携带 `trace_id/turn_id/content`。
- 提示词更新：
  - `evolution_prompt`：强调 summary 始终进入 e7。
  - `narrative_prompt`：输出仅保留 `narrative_str`。
  - `merger_prompt`：输入来源调整为 `e7 + 可选 narrative_str`。

## 回归测试

执行：

- `tests/phase3/test_phase3_concurrent_state_pipeline.py`
- `tests/phase3/test_agent_io_logging.py`
- `tests/phase4/test_phase4_narrative_merger.py`

结果：10 passed。

## 过程中出现的问题与修复

- 问题：merger 分支错误读取 `EvolutionAgentOutput.system_output.turn_id/trace_id`，但该字段在现实现中为 `None`。
- 现象：phase3/phase4 回归测试出现 AttributeError。
- 修复：改为读取 `EvolutionResult.turn_id/trace_id`。
