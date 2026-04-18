# Phase5 NPC Performer 鉴定与演化链路修复

## 背景输入
- 依据 `docs/spec/draft_spec.md` 与 `docs/phase/agent.md` 规范。
- 依据 `docs/review/review_result/review_debug_report_phase3_4.md` 的 Issue 2：NPC 无法唤起 check/evolution。

## 修复范围
1. 提示词层：补齐 NPC performer 结构化鉴定输出字段约束。
2. 模型层：补齐 `NpcPerformerAgentLlmOutput` 的 check 相关字段。
3. 引擎层：在 performer 分支执行 NPC check 与 evolution 下游链路。
4. 测试层：新增回归测试覆盖 NPC 触发鉴定并进入 evolution。

## 变更明细
- `src/data/model/agent_output.py`
  - `NpcPerformerAgentLlmOutput` 新增字段：
    - `routing_hint: Optional[str]`
    - `attributes: List[str]`
    - `against_char_id: List[str]`
    - `difficulty: Optional[str]`

- `src/agent/prompt/npc_performer_prompt.py`
  - 输出 JSON 契约新增上述字段。
  - 明确不鉴定时必须输出：`routing_hint=null, attributes=[], against_char_id=[], difficulty=null`。

- `src/engine/engine.py`
  - ` _run_performer_branch` 返回 `(performer_outputs, performer_chain_outputs)`。
  - 新增 `_run_npc_performer_downstream`：执行 NPC performer 的下游链路。
  - 新增 `_run_npc_check`：按 performer 输出执行 `num/against` 鉴定。
  - 回合事件新增 `npc_performer_chain` 字段，包含每个 NPC 的 check/evolution 结果。

- `tests/phase5/test_phase5_npc_performer.py`
  - `PerformerPipelineFakeLLMService` 支持注入 performer payload。
  - 新增 `test_npc_performer_can_trigger_numeric_check_and_evolution`。
  - 原有用例补充对 `npc_performer_chain` 的断言。

## 测试执行记录（真实执行日志）
### 用例 1
- 目标：`tests.phase5.test_phase5_npc_performer`
- 结果：通过（2/2）
- 关键输出：
  - `test_engine_runs_performer_and_updates_goal_and_memory ... ok`
  - `test_npc_performer_can_trigger_numeric_check_and_evolution ... ok`

### 用例 2
- 目标：`tests.phase5.test_phase5_npc_scheduler`
- 结果：通过（2/2）

## 失败原因与修复动作
- 失败原因（评审报告）：`NpcPerformer` 输出缺少 check 字段，engine performer 分支未处理 check/evolution。
- 修复动作：按规范补齐字段契约 + 引擎路由 + 回归测试，确保能力可执行且可验证。

## 结果结论
- NPC performer 已可通过结构化字段触发鉴定，并进入 evolution_agent。
- 变更已覆盖 prompt -> model -> code -> test，并沉淀文档证据。
