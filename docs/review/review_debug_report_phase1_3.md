# Phase1-3 Review Debug 汇报

## 1. 本轮目标

本轮工作围绕 `phase1 -> phase3` 已实现部分做收口，不把 `phase4 -> phase7` 的空实现或未开始实现记为问题。目标主要有三类：

- 重新按规范审查 `src/agent` 与 `src/rule` 已实现部分。
- 修复 review 过程中确认存在的真实偏差，尤其是规则鉴定链路与 model 真值消费问题。
- 沉淀一份可复用的 review-debug 经验，给下一轮迭代直接复用。

## 2. 本轮确认过的问题

### 2.1 规则系统对多种鉴定方式支持不足

- 之前 `rule_system` 的实现更接近“单人数值检定”，对 `against` 的表达和结算不完整。
- `engine` 侧 `_run_check(...)` 也存在把复杂鉴定简化消费的倾向，导致 phase2/3 范围内已实现的对抗语义没有真正闭环。

### 2.2 model 真值未被完整消费

- `src/data/model` 已经是唯一真值来源，但业务代码仍存在在 model 外重新定义同义结果结构的情况。
- 这会让 prompt、engine、rule、tests 对“同一个结构”各自理解，后续维护成本快速上升。

### 2.3 输入校验与 prompt 语义存在错位风险

- `num` 与 `against` 两类路由虽然在 prompt 中已有区分，但代码校验如果混写，就会把单人鉴定误判成必须提供对抗对象。
- 这类问题不会立刻让代码报错，却会持续污染 agent 输出质量和后续调试结论。

### 2.4 状态扩展字段写入约束只停留在模型层不够安全

- `extension_registry`、`mutable` 这类写入权限约束如果只存在于 model 设计里，而运行时 patch 不校验，就会出现“规范声明了不可写，但代码仍能写入”的偏差。

## 3. 本轮已完成修复

### 3.1 规则系统改为显式支持多种鉴定

- `src/rule/rule_system.py` 已收口为显式区分：
  - `run_numeric_check(...)`
  - `run_against_check(...)`
  - `run_coc_check(...)` 作为兼容入口
- 对抗鉴定已补齐参与方、胜者、受影响对象等结构化结果，避免只剩一段文本结果。

### 3.2 规则结果改为直接消费 model 真值

- `src/rule/rule_system.py` 不再保留本地同义 `CocCheckResult` 定义。
- 规则结算统一消费 `src/data/model/agent_output.py` 中的 `CocCheckResult` 与 `CocCheckParticipant`。
- `src/data/model/input/agent_chain_input.py` 中的 `E3RuleResult` 已同步承接 `check_type`、`difficulty`、`winner_id`、`affected_ids` 等信息，保证链路继续可传递。

### 3.3 DM 输入校验与路由语义对齐

- `src/agent/llm/input_agent.py` 已修正：
  - `num` 不再错误要求 `against_char_id`
  - `against` 继续要求至少两个参与方 ID
- 这让 prompt 语义、agent 输出和代码校验重新保持一致。

### 3.4 engine 对 rule 路由做了最小必要修复

- `src/engine/engine.py` 中 `_run_check(...)` 已按 `routing_hint` 分流：
  - `num` -> `run_numeric_check(...)`
  - `against` -> `run_against_check(...)`
- phase2/phase3 的下游自然语言上下文构造也已同步消费 richer rule result，而不是丢掉关键信息。

### 3.5 状态扩展字段写入增加运行时约束

- `src/rule/state_patch.py` 已增加对 `extension_registry` 的运行时检查。
- 对 `mutable` 与基础 `value_type` 做了最小必要校验，避免扩展字段声明与运行时行为脱节。

## 4. 已完成回归验证

已跑通的本地回归测试：

- `py -3 tests/phase1/test_input_rule_dsl_phase1.py`
- `py -3 tests/phase2/test_phase2_serial_pipeline.py`
- `py -3 tests/phase3/test_phase3_concurrent_state_pipeline.py`

本轮新增或同步覆盖的重点包括：

- `against` 检定结果结构
- `num` 与 `against` 输入校验差异
- phase3 状态链对 `extension_registry.mutable` 的运行时 enforcement

## 5. 当前仍保留的边界与后续建议

### 5.1 本轮不是所有问题都已经完全收口

- `engine.py` 的统一单类收口仍属于后续应继续推进的结构优化项。
- DSL 的“字段路径受限访问”仍有继续细化空间，但本轮没有改 DSL 语义。
- phase3 的 state 可观测性字段仍可继续收口，但本轮优先先把规范偏差修正到位。

### 5.2 下一轮优先建议

1. 继续把 `engine.py` 收口为单一 `Engine`，减少 phase2/phase3 双实现漂移风险。
2. agent/rule/engine 任一处新增结构化字段时，先查 model，再改 prompt、代码、测试和 donelist，避免再出现 model 外重复定义。
3. 所有“可写扩展字段”都要默认做运行时校验，不要只在 model 中声明。
4. 对需要真实 LLM 的测试，明确标记环境前提与验证状态，避免把“未联调”误写成“未实现”。

## 6. 本轮产出文件

- review 文档：`docs/review/review_result/1.md`
- 过程汇报：`docs/review/review_debug_report_phase1_3.md`
- 经验补充：
  - `docs/phase/agent.md`
  - `docs/review/implementation_check_skill.md`
