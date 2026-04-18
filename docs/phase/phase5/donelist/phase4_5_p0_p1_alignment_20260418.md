# Phase4/5 P0-P1 对齐完成记录

## 本轮目标

根据 `docs/review/review_result/review_debug_report_phase4_5.md` 中的 `P0-P1` 计划，优先收口两类问题：

1. `state_change` 失败并回滚后，`npc_performer` 仍然写入世界状态，破坏事务边界。
2. NPC 下游 `check/evolution` 结果只停留在调试输出，没有并回主 `e7/merger` 链路。

## 已完成的对齐内容

### 1. performer 副作用改为延迟提交

- 修改 `src/agent/llm/npc_perform_agent.py`
- `NpcPerformerAgent.run(...)` 现在只负责生成结构化行为结果
- 新增 `apply_side_effects(...)`，仅在系统确认本回合 `state` 成功后才回写：
  - `memory.current_event`
  - `memory.short`
  - `memory.short_log`
  - `memory.log`
  - `goal.base_goal`
  - `goal.active_goal`
  - `goal.goal_history`

这样可以保证：

- `state` 失败并回滚时，不再留下 NPC 记忆/目标脏写
- performer 的世界副作用不再早于主事务边界提交

### 2. engine 在 state 失败时跳过 performer 分支

- 修改 `src/engine/engine.py`
- 在 `fallback_error is not None` 时，不再执行 `_run_performer_branch(...)`

这样可以保证：

- 失败回合不会继续生成和提交 NPC 分支副作用
- 返回结果中的 `npcperformer` 与 `npc_performer_chain` 也会保持为空

### 3. 新增 NPC 下游结构化真值模型

- 修改 `src/data/model/agent_output.py`
- 新增 `NpcPerformerChainResult`

该模型统一承接：

- `npc_id`
- `intent`
- `check`
- `check_error`
- `evolution_summary`
- `evolution_visible_to_player`
- `e7`

避免继续在 `engine` 里用裸字典长期承载同义结构。

### 4. NPC 下游结果正式并回主 e7

- 修改 `src/engine/engine.py`
- `_run_npc_performer_downstream(...)` 现在会生成结构化 `NpcPerformerChainResult`
- 会把：
  - `npc_check`
  - `npc_check_error`
  - NPC `evolution` 产出的 `e7`
 统一合成为 NPC 分支自己的因果链投影

- 主流程会在 `state` 成功后，把所有 NPC 分支 `e7` 合并回主 `merger_chain`

这样可以保证：

- NPC 分支不再只是 `npc_performer_chain` 调试信息
- `merger_agent` 现在能够消费 NPC 分支带来的因果链结果
- Phase5 的下游结果开始真正进入 phase4 的叙事主链

## 新增或更新的验证

### 1. 回滚边界验证

- 更新 `tests/phase3/test_phase3_concurrent_state_pipeline.py`
- 新增 `test_state_rollback_skips_npc_performer_side_effects`

验证点：

- 当 `state` 重试耗尽并回滚时：
  - `npcperformer == []`
  - `npc_performer_chain == []`
  - NPC 的 `goal` 不被污染
  - NPC 的 `current_event / short / short_log / log` 不被污染

### 2. NPC 下游进入 merger 验证

- 更新 `tests/phase5/test_phase5_npc_performer.py`

验证点：

- `npc_performer_chain` 中存在结构化 `e7`
- `merger` 的输入 `e7.narrative_causality` 能看到 NPC 分支并回后的结果
- 数值鉴定分支会把 `npc_check` 信息写回 merger 输入

## 本轮执行的测试

已执行：

```powershell
python -m unittest tests.phase3.test_phase3_concurrent_state_pipeline
python -m unittest tests.phase4.test_phase4_narrative_merger tests.phase5.test_phase5_npc_scheduler tests.phase5.test_phase5_npc_performer
```

结果：

- `tests.phase3.test_phase3_concurrent_state_pipeline`：`Ran 9 tests, OK`
- `tests.phase4.test_phase4_narrative_merger + tests.phase5.*`：`Ran 6 tests, OK`

## 当前已收口的边界

本轮已经收口：

- `state` 失败后 performer 不再污染世界状态
- NPC 下游结果不再只停留在临时调试字段，而是并回主 `e7 -> merger` 链
- `NpcPerformer` 下游结果已有正式 model 真值承接

## 当前仍保留的边界

本轮没有继续推进：

- Phase4 的 SSE/WebSocket 流式接口层
- Phase4 的 narrative truth 持久化与双真值池物理隔离存储
- NPC 下游结果进一步进入独立 world patch 提交链

这些内容仍属于后续 `P2-P3` 工作范围，不能误判为已完成。
