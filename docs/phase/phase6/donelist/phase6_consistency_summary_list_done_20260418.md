# Phase6 一致性维护链路改造完成记录（summary_list 版本）

## 需求来源
- 依据文档：`docs/phase/agent.md`、`docs/spec/draft_spec.md`
- 本轮需求：`docs/phase/phase6/todo/新增需求.md`

## 本次目标
将一致性维护从「复用 StatePatch DSL 输出」改为「LLM 仅返回压缩后的列表结果」，并由系统自动完成写回与清空。

## 已完成实现
1. 一致性输出协议改造
- 新增 `ConsistencySummaryKind`：`narration/description/key_facts`
- 新增 `ConsistencySummaryItem`：`{kind, value}`
- `ConsistencyAgentLlmOutput` 从 `changes` 改为 `summary_items`
- 结构约束：`summary_items` 首项必须为 `narration`，且 `value` 不可为空

2. 一致性输入收集改造（系统自动收集）
- 引擎自动收集三类候选并传给 LLM：
  - narration_candidates
  - description_candidates（public + add）
  - key_facts_candidates（key_facts + short_log）
- 不再把大而杂的 DSL 目标路径交给 LLM 决策

3. 一致性落地逻辑改造（系统自动清空）
- 系统按 `summary_items` 写回结果：
  - narration -> `narrative_info.recent` 压缩为 1 条
  - description -> 对应实体 `description.public`
  - key_facts -> 对应 NPC `memory.key_facts`
- 系统自动清空：
  - 所有命中的 `description.add`
  - 所有命中的 `memory.short_log`
- 不再依赖 consistency 返回 `ADD/REMOVE/SET/...` DSL

4. 阻断链路补全
- 当一致性输出 `can_proceed=false` 时：
  - 引擎写入 `_consistency_blocking_message`
  - 当前回合返回 `CONSISTENCY_BLOCKED`
  - 后续回合直接进入 `consistency_blocked` 事件

5. 提示词同步
- consistency prompt 全量改为 summary_list 协议说明
- 明确输出顺序：首项 narration，随后 description（按候选顺序），最后 key_facts（按候选顺序）

6. 测试同步
- phase6 测试由旧 DSL 断言改为 summary_list 断言
- 新增阻断行为测试（`can_proceed=false`）

## 变更文件
- `src/data/model/agent_input.py`
- `src/data/model/agent_output.py`
- `src/agent/prompt/consistency_prompt.py`
- `src/agent/llm/consistency_agent.py`
- `src/engine/engine.py`
- `tests/phase6/test_phase6_consistency_agent.py`

## 测试执行与结果
说明：终端 `conda run` 在当前会话存在 Python 路径污染（落到 `msys64`），改为使用工作区解释器直连执行（`mcp_pylance_mcp_s_pylanceRunCodeSnippet`），结果可复现。

1. Phase6 一致性专项
- 命令：`unittest` 加载 `tests.phase6.test_phase6_consistency_agent`
- 结果：`Ran 3 tests`，`OK`

2. 回归（Phase3 + Phase4）
- 命令：`unittest` 加载
  - `tests.phase3.test_phase3_concurrent_state_pipeline`
  - `tests.phase4.test_phase4_narrative_merger`
- 结果：`Ran 12 tests`，`OK`

## 关键行为验收对照
1. 不再使用 consistency 的 DSL add/remove/set 输出：已完成
2. LLM 输出压缩列表，首项 narration：已完成
3. 系统自动清空 description.add 与 short_log：已完成
4. 叙事 recent 压缩为单条：已完成
5. 提示词/模型/代码/测试全链路同步：已完成

## 当前边界
- 本轮把一致性维护结果写入 `narrative_info.recent`（压缩为 1 条），未清空 `narrative_log`（保留 debug 与回溯能力）。
- 若后续希望“叙事池全量仅保留压缩句”，可在下一轮增加 `narrative_log` 的归档策略配置化处理。
