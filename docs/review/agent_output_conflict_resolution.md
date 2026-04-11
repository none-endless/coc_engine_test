# agent_output 冲突决议记录

## 范围

用于记录输出层模型在实现时，与 spec 草案及输入层现状之间的冲突点和统一决议。

## 决议列表

1. 命名统一
- 决议：系统执行元信息采用 `turn_id/trace_id/retry_seq`。
- 影响：`SystemExecutionMeta` 字段由 `turn` 统一为 `turn_id`。

2. state 操作符集合
- 决议：输出层按 DSL 完整集合建模，采用 `ADD/REMOVE/SET/UPDATE/MOVE/ASSERT`。
- 影响：`StateOperator` 完整覆盖 6 类操作；`StateChangeOp` 保留 `condition` 字段支持 ASSERT。

3. e4 来源拆分
- 决议：不再共享同一 e4 模型，按生产者拆分。
- 影响：
  - evolution 使用 `E4EvolutionStepResult` / `E4EvolutionLlmView`（仅 summary）。
  - scheduler 使用 `E4SchedulerStepResult` / `E4SchedulerLlmView`（仅 extra_npc_context）。

4. npc_scheduler 输出边界
- 决议：`npc_scheduler_agent` 的 `llm_output.step_result` 同时包含 `summary` 与 `extra_npc_context`，用于消费完整调度上下文。
- 影响：输出层新增 `NpcSchedulerStepResultOutput` 作为组合结果容器。

5. 幂等与版本占位
- 决议：在 `PatchMeta` 增加扩展占位字段 `patch_id` 和 `expected_version`。
- 影响：当前业务可先不强制填写，后续提交层可无缝接入去重与版本冲突检测。

## 未决事项

无。

## 补充决议（2026-04-11）

1. `trace_id` 全局统一语义
- 决议：全链路仅保留一种 `trace_id` 语义，即“链路追踪编号”。
- 影响：禁止将 `trace_id` 解释为“同回合叙事片段序号”；若未来需要片段序号，必须新增独立字段，不复用 `trace_id`。

2. 历史兼容策略
- 决议：项目初期不做历史字段兼容，不引入旧字段名共存窗口。
- 影响：输入/输出模型全面采用当前命名策略，发现旧字段调用直接改调用方，不新增兼容层。
