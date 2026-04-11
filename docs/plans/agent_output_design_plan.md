# agent_output 输出层方案与实施计划

## 1. 目标

基于现有代码模型与规范文档，先确定统一的 Agent 输出层数据模型（LLM 输出 + 系统输出），并明确当前代码与规范的偏差点，作为后续实现 `src/data/model/agent_output.py` 的执行蓝图。

本计划阶段只做方案与问题归档，不改代码。

## 2. 设计依据

- 代码侧输入模型：`src/data/model/agent_input.py`
- 代码侧链路模型：`src/data/model/input/agent_chain_input.py`
- 规范文档：`docs/spec/draft_spec.md`
- 输出草案：`docs/spec/输入层草案.md`

## 3. 输出层统一原则

1. 每个 Agent 输出都拆分为两段：`llm_output` 与 `system_output`。
2. `llm_output` 只承载模型生成内容，不承载调度与追踪控制字段。
3. `system_output` 承载回合控制与链路追踪字段（如 `turn_id`、`trace_id`、`retry_seq`）。
4. 所有输出模型使用 Pydantic 明确类型与默认值，禁止隐式 Any。
5. 输出层字段命名优先与输入层保持一致（例如 `trace_id` / `turn` 的语义需统一）。
6. 状态补丁输出必须显式建模操作符、目标路径、值、校验信息，确保可做提交前校验。

## 4. 目标输出模型映射（按 Agent）

### 4.1 dm_agent

- llm_output:
  - `intent_info: E2IntentInfo`（来源 `agent_chain_input.py`）
- system_output:
  - `e1_view: E1LlmView`（来源 `agent_input.py`）

### 4.2 evolution_agent

- llm_output:
  - `summary: str`（对应 `E4LlmView.summary`）
- system_output:
  - 无

### 4.3 narrative_agent

- llm_output:
  - `narrative_str: str`
- system_output:
  - `turn_id: int`
  - `trace_id: int`（同一回合内叙事片段序号）

### 4.4 merger_agent

- llm_output:
  - `narrative_str: str`
- system_output:
  - 无

### 4.5 state_agent

- llm_output:
  - `changes: List[StateChangeOp]`
  - 每条操作至少包含：`op`、`target_path`、`value`
  - `op` 值域先按 `ADD/REMOVE/SET/UPDATE/MOVE/ASSERT` 对齐
- system_output:
  - `patch_meta: PatchMeta`
  - `PatchMeta = {trace_id: int, turn_id: int, retry_seq: int}`

### 4.6 npc_scheduler_agent

- llm_output:
  - `step_result: E4LlmView`（重点消费其中 `summary` 与 `extra_npc_context`）
- system_output:
  - `trace_id: int`
  - `turn_id: int`

### 4.7 npc_performer_agent

- llm_output:
  - `raw_input: str`
  - `change_basic_goal: Optional[str]`
  - `change_activate_goal: Optional[str]`
- system_output:
  - `id: str`（角色 id）
  - `trace_id: int`
  - `turn_id: int`

## 5. 当前主要问题清单（需先统一再实现）

### 5.1 结构缺失问题

1. `src/data/model/agent_output.py` 为空文件，尚无输出层类型定义。
2. 输入层已经做了 `llm_input/system_input` 双通道拆分，但输出层尚未建立同构设计。

### 5.2 规范冲突与命名不一致

1. `turn` 与 `turn_id` 并存，`trace_id` 在不同文件语义描述不稳定（输入追踪 vs 同回合叙事序号）。
2. `state_agent` 操作符草案（小写 add/update/move/set）与规范 DSL（大写且含 REMOVE/ASSERT）存在差异。
3. `evolution_agent` 在输出草案里只要求 summary，但输入层 `E4LlmView` 含 `extra_npc_context`，边界待定。

### 5.3 代码模型质量问题（会影响输出层联动）

1. `SystemExecutionMeta.trace_id` 类型为 `int`，默认值却是空字符串。
2. `E2IntentInfo` 多个字段拼写与类型不稳定（如 `atirrbutes`、`is_diloggue`、`routing_hint/hard` 类型宽泛）。
3. `E3RuleResult.sucusess` 字段拼写错误，与 `agent_input.py` 中 `E3LlmView.success` 不一致。
4. `E7CausalityChain.narrative_list` 当前写法非法（将类型注解与赋值混写），需重构为标准字段定义。

### 5.4 事务与幂等约束待落到输出模型

1. `PatchMeta` 在规范里关键，但当前代码侧尚无统一复用定义。
2. 缺少输出层对 `patch_id/expected_version` 的建模占位，后续并发提交与去重将难以扩展。

## 6. 实施计划（仅规划）

### 阶段 A：模型蓝图冻结

1. 冻结统一命名：`turn_id/trace_id/retry_seq`。
2. 冻结各 Agent 输出契约（字段、类型、是否可空）。
3. 冻结状态变更操作符集合（纳入 `REMOVE/ASSERT`）。
4. 在代码层agent_chain_input,agent_input中将e4明确区分,schduler产生context字段的是schduler的,evolution产生的summary字段是evolution的,不再共享同一数据模型

### 阶段 B：输出层建模设计稿

1. 在 `agent_output.py` 设计基础通用壳：
   - `AgentOutputEnvelope`
   - `AgentLlmOutputBase`
   - `AgentSystemOutputBase`
2. 为每个 Agent 设计专属 `LlmOutput` 与 `SystemOutput`。
3. 为 state 输出补充：`StateChangeOp`、`PatchMeta`、`StateAgentOutput`。

### 阶段 C：与输入层和规范对齐检查

1. 检查输入输出同名字段语义一致性。
2. 检查草案与 DSL 规范冲突点并出一版决议。
3. 检查可选字段默认值与 `None` 语义是否一致。

### 阶段 D：验收清单

1. 每个 Agent 是否都具备 `llm_output/system_output` 双段定义。
2. 状态补丁是否可被规则层直接校验与执行。
3. 是否满足回合事务字段最小集：`turn_id`、`trace_id`。
4. 是否满足失败重试最小集：`retry_seq`。

## 7. 计划产出物

1. 输出层模型文件：`src/data/model/agent_output.py`（下一步实现）
2. 对齐说明文档：本计划文件
3. 冲突决议记录（阶段 C 产出，建议放 docs/review）

## 8. 下一步执行建议

1. 先按本计划完成字段冻结（特别是 `state_agent` 操作符集合）。
2. 再开始实现 `agent_output.py`，优先完成 `PatchMeta` 与 `StateChangeOp`。
3. 最后做输入/输出互转与序列化示例，验证链路可用性。
