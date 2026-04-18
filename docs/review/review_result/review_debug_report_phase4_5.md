# Phase4-5 Review Debug 汇报

## 1. 本轮目标

本轮工作围绕 `phase4 -> phase5` 已进入验收范围的实现做审查，不把 `phase6 -> phase7` 的后续内容直接记为问题。重点目标有三类：

- 按 `docs/spec/draft_spec.md` 与 `docs/phase/phase4/line.md`、`docs/phase/phase5/line.md` 审查当前实现是否真正闭环。
- 识别 phase4/5 中影响正确性、可维护性和验收判断的真实问题，尤其是“看起来打通了，但实际上没有进入真值链”的链路缺口。
- 输出一份可直接指导下一步整改的 review-debug 结果，作为后续全面对齐的基线。

## 2. 本轮范围与阶段结论

### 2.1 审查范围

- `docs/spec/draft_spec.md`
- `docs/phase/phase4/line.md`
- `docs/phase/phase5/line.md`
- `docs/phase/phase4/donelist/`
- `docs/phase/phase5/donelist/`
- `src/engine/engine.py`
- `src/agent/llm/narrative_agent.py`
- `src/agent/llm/merger_agent.py`
- `src/agent/llm/npc_schedul_agent.py`
- `src/agent/llm/npc_perform_agent.py`
- `src/data/model/agent_input.py`
- `src/data/model/agent_output.py`
- `src/data/model/input/agent_chain_input.py`
- `src/data/model/input/agent_narrative_input.py`
- `tests/phase4/test_phase4_narrative_merger.py`
- `tests/phase5/test_phase5_npc_scheduler.py`
- `tests/phase5/test_phase5_npc_performer.py`

### 2.2 阶段判断

- `phase4`：进行中
  - `NarrativeAgent -> MergerAgent -> NarrativeInfo.recent` 主链已经打通。
  - `state_change_agent` 也没有直接消费 `NarrativeInfo`。
  - 但“物理隔离存储”和“SSE/WebSocket 流式接口”尚未真正落地，因此不能判定为“已完成”。

- `phase5`：进行中，且存在阻塞性缺口
  - scheduler 的预算控制、冷却控制、敏捷排序已基本落地。
  - performer 的目标与记忆维护也已落地。
  - 但 NPC 下游结果没有正式并入主真值链，且回滚失败时仍会留下 performer 副作用，因此不能判定为“已完成”。

## 3. 本轮确认过的问题

### 3.1 state 回滚失败后，NPC performer 仍会写入世界状态

- 规范要求：
  - `npc_agent` 必须等待 `state_change` 完整结束后再继续动作。
  - 回滚必须恢复到本回合开始前快照。
- 当前实现中，`state_task` 完成后，无论是否出现 `fallback_error`，都会继续执行 performer 分支。
- performer 分支内部又会直接把 `current_event`、`short`、`short_log`、`log`、`goal` 写回 `WorldState`。

这会导致一个明显偏差：

- 本回合已经因为 state patch 失败而终止；
- 世界真值理论上应该恢复到回合开始前；
- 但 NPC 的记忆和目标已经被 performer 提前写进去了；
- 最终留下“状态已回滚，但 NPC 副作用未回滚”的脏数据。

这是当前 phase5 最严重的闭环问题之一，因为它直接破坏了事务边界和回滚语义。

### 3.2 NPC performer 的下游结果没有进入正式真值链

- 现在 performer 已经能够产出结构化 `routing_hint / attributes / against_char_id / difficulty`，也能够触发 `check` 与 `evolution`。
- 但当前实现只是把这些结果记录到 `result["npc_performer_chain"]` 和 agent io 日志里。
- 它们没有进入：
  - `state_change_agent`
  - 主回合 `e7`
  - `merger_agent`
  - `NarrativeInfo.recent`
  - 正式世界状态提交链

这意味着当前 phase5 更像：

- “NPC 分支能独立做出判断并输出调试结果”

而不是：

- “NPC 自主行为已成为主系统真值的一部分”

因此从规范角度看，这属于典型的“链路级改动未闭环”。

### 3.3 Phase4 的双真值池仍停留在内存隔离，不是物理隔离存储

- `phase4/line.md` 要求 `WorldInfo` 与 `NarrativeInfo` 物理隔离存储。
- 当前实现里，`WorldState` 与 `NarrativeInfo` 确实是两个不同对象。
- 但 `NarrativeInfo` 目前只是 `Engine` 内部维护的内存对象，并没有对应的持久化读写实现。

仓库里虽然已经有：

- `docs/phase/phase4/donelist/sqlite_dual_truth_schema.md`

但目前缺少：

- narrative truth 持久化仓储实现
- world truth / narrative truth 的真实分离存取逻辑
- 对应的运行时读写测试

所以 phase4 现在最多只能判定为“逻辑分离”或“进程内分离”，不能判定为规范要求的“物理隔离存储”。

### 3.4 Phase4 的流式叙事只完成了事件切片，没有完成接口闭环

- 规范要求 narrative 支持 SSE 或 WebSocket 推送叙事内容给前端。
- 当前 `NarrativeAgent.build_stream_events(...)` 已经能把 `narrative_str` 切成 `narrative.delta / narrative.completed` 事件。
- 但仓库中没有看到 SSE/WebSocket 服务接口、协议出口或延迟验证链路。

这说明当前实现完成的是：

- “可供接口层消费的流式事件格式”

而不是：

- “面向前端可用的流式叙事接口能力”

因此 phase4 的“流式输出”只能算部分完成。

## 4. 本轮已确认落地的部分

### 4.1 phase4 主链已基本成形

- `evolution` 会把 summary 写入 `e7`。
- `visible_to_player=true` 时会触发 `narrative_agent`。
- 状态提交成功后会再触发 `merger_agent`。
- `merger` 输出会写入 `NarrativeInfo.recent`。
- 状态提交失败时，本回合 narrative / merger 不会进入正式叙事池。

这部分说明 phase4 主链不再只是空实现，而是已经具备可验证的基础闭环。

### 4.2 phase5 scheduler 的系统后处理已落地

- 候选 NPC 存在性过滤
- `hp/health/san/sanity` 等关键状态值为 0 的过滤
- `cooldown_turns` 应用
- 按 `dexterity / 敏捷` 排序
- `max_actions_per_turn` 限流

这部分已经不是只依赖 prompt，而是有系统侧强约束，方向是正确的。

### 4.3 phase5 performer 的目标与记忆维护已落地

- 会把 `current_event` 写入 NPC 记忆
- 会维护 `short / short_log / log`
- 会维护 `base_goal / active_goal / goal_history`

这部分已经能证明 performer 不再只是“吐一句话”，而是开始承担 NPC 本地状态维护职责。

## 5. 上下游闭环缺口

当前最需要明确指出的闭环缺口有四个：

### 5.1 state 回滚与 performer 写入未统一事务边界

- `state` 失败后仍执行 performer
- performer 的世界副作用不参与回滚

### 5.2 NPC check/evolution 未并入主因果链

- performer 下游结果没有进入主回合 `e7`
- merger 看不到 NPC 分支结论
- narrative truth 看不到 NPC 分支结论

### 5.3 NPC evolution 结果未进入 state patch 正式提交

- NPC 的动作结论目前没有驱动新的 state patch
- 这让“NPC 能移动/交互/影响世界”的验收结论缺乏正式证据

### 5.4 phase4 的流式与双库存储缺少运行时实现

- 现有文档和模型足够支持继续实现
- 但当前链路还没有真正落地到可部署接口和可持久化存储

## 6. 已完成回归验证

本轮实际执行的定向测试：

- `python -m unittest tests.phase4.test_phase4_narrative_merger`
- `python -m unittest tests.phase5.test_phase5_npc_scheduler`
- `python -m unittest tests.phase5.test_phase5_npc_performer`

结果：

- `Ran 6 tests`
- `OK`

说明：

- 现有 phase4/5 单测可以证明局部能力已经落地；
- 但这些测试尚未覆盖“回滚后 performer 仍写入副作用”和“NPC 结果未进入主真值链”这两个关键问题；
- 因此测试通过不能直接推导为 phase4/5 已验收完成。

## 7. 下一步全面对齐计划

下面的计划按优先级分为 `P0 -> P3`，目标不是局部补洞，而是把 phase4/5 的主链、事务边界、真值池边界一次性对齐到规范。

### 7.1 P0：先修事务边界与验收阻塞项

1. 调整 `engine` 执行顺序，确保当 `state` 进入 fallback / rollback 时，不再执行 performer 写入分支。
2. 为 performer 当前的世界写入增加统一事务保护，至少做到：
   - 在 state 成功后再提交
3. 新增失败回合回归测试：
   - state patch 重试耗尽后，NPC 的 `goal`、`current_event`、`short_log` 等不得被污染。
4. 重新界定 phase5 当前可验收边界，避免“performer 跑过了”被误判为“NPC 自主行为已闭环”。

### 7.2 P1：把 NPC 下游结果接回主真值链

1. 设计 NPC performer 下游统一投影结构，明确哪些结果进入：
   - 主回合 `e7`
   - `state_change_agent`
   - `merger_agent`
   - `NarrativeInfo`
2. 将 NPC `check/evolution` 结果从临时 `npc_performer_chain` 调试输出，提升为正式链路输入。
3. 明确 NPC 分支与玩家主回合的合并规则：
   - 同回合共享一个 `e7`
   - 作为 `e7.narrative_list` 的新增 source
   - merger 完整消费 NPC 和玩家引发的叙事片段
4. 增加回归测试，验证 NPC 动作不仅有日志，而且真正改变正式输出。

### 7.3 P2：补齐 phase4 的流式接口与双真值池存储

1. 增加 narrative stream 输出接口层实现：
   - 选定 SSE 或 WebSocket 作为 V1 主实现
   - 保留现有 `build_stream_events(...)` 作为数据层
2. 增加接口文档与最小延迟测试样例，补齐 `phase4/line.md` 的物料要求。
3. 为 `NarrativeInfo` 增加独立仓储层，实现与 `WorldState` 的真实物理分离。
4. 将 `sqlite_dual_truth_schema.md` 从“设计文档”推进到“真实可运行实现 + 测试验证”。

### 7.4 P3：做结构收口，避免后续继续漂移

1. 收口 `engine.py` 中 phase4/5 的分支拼装逻辑，避免：
   - 主玩家链路一套
   - NPC 下游旁路一套
   - narrative / merger 再单独拼一套
2. 明确“哪些输出是 debug 辅助，哪些输出是正式真值”，避免调试结构长期冒充系统契约。
3. 为 phase4/5 建立统一的验收测试矩阵，覆盖：
   - 成功提交
   - 回滚
   - 不可见叙事
   - NPC 调度预算
   - NPC 下游进入真值链
   - 双真值池隔离

## 8. 建议的实施顺序

建议按下面顺序推进，避免一边补 narrative 一边继续扩大 NPC 脏写问题：

1. 先做 `P0`：修复回滚与 performer 副作用污染
2. 再做 `P1`：把 NPC 下游结果接回主真值链
3. 然后做 `P2`：补齐 narrative stream 接口与双真值池持久化
4. 最后做 `P3`：统一结构、补测试矩阵、更新 donelist

## 9. 本轮产出文件

- 审查结果：`docs/review/review_result/review_debug_report_phase4_5.md`
- 审查依据：
  - `docs/spec/draft_spec.md`
  - `docs/phase/phase4/line.md`
  - `docs/phase/phase5/line.md`
  - `docs/phase/phase4/donelist/`
  - `docs/phase/phase5/donelist/`
