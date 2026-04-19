## Plan: Phase8 高中优先级系统修复

基于 draft_spec 与 phase8 line 清单，本轮一次性完成高优先级与中优先级问题（1-10）：先做快照类型化全量替换和契约稳定，再完成 Engine 大拆分、Agent 契约统一与 NPC 串行事务化提交（先玩家 state_commit，再逐 NPC 提交并刷新视图），最后收敛视图可写边界、常量治理与模型清理，并以 phase3-6 回归测试和新增关键单测闭环验证。

**Steps**
1. Phase A - 基线与回归护栏（可并行）
   - 盘点并锁定现有关键行为：并发分支、回滚与降级、NPC 调度预算、一致性阻断、叙事合并。
   - 先跑现有 phase3-6 测试建立基线；记录当前失败与通过项，作为重构回归对照。
   - 依赖：无。

2. Phase B - WorldSnapshot 类型化全量替换（高优先级 #2，阻塞后续重构）
   - 在 world_state 模块引入结构化快照模型（包含 version、snapshot_at、maps、characters、items），并将 WorldState.get_snapshot 改为返回该模型深拷贝。
   - 全量替换快照消费者的根层 dict 访问为强类型访问：Engine、bootstrap 校验、NpcScheduler、NpcPerformer、RuleSystem、DSL、StatePatchRuntime、SQLite 快照仓储。
   - 保留序列化出口用于持久化与日志（例如 model_dump），但业务读取统一使用强类型字段。
   - 依赖：Phase A。

3. Phase C - Engine 一次性拆分（高优先级 #1，用户已决策一次性重构）
   - 在 src/engine 目录新增三个编排/管理模块：
     - TurnOrchestrator：承接 _run_phase3_turn_async 及分支调度。
     - ConsistencyOrchestrator：承接一致性输入构建、重试、应用与阻断决策。
     - NarrativeTruthManager：承接 narrative_info 的恢复、落库、追加与事件发射。
   - Engine 保留依赖注入、顶层入口 run_turn/run_turn_async 与全局对象装配；重逻辑迁移到新模块。
   - 保证锁与事务边界不变：状态提交临界区仍受单锁保护；NPC 分支必须在玩家 state_change 提交成功后才启动，并以“单 NPC 提交完成 -> 下一 NPC 激活”的串行栅栏执行。
   - 依赖：Phase B。

4. Phase D - Agent 契约统一与因果链归一（高优先级 #4 #5）
   - 统一 DMAgent.run 签名为仅接收 DmAgentInput；将 available attributes 与 valid characters 完全由 llm_input 提供并在 Agent 内部归一化。
   - Engine 的 DM 调用点改为新签名，移除额外参数拼装。
   - EvolutionAgent.run 移除重复兼容分支，直接消费 E7CausalityChain；链归一化仅保留在 Engine 一处。
   - 对应更新 Agent 输入模型、调用点和单测桩数据。
   - 依赖：Phase C（可与 Phase E 并行部分推进）。

5. Phase E - NPC 副作用统一提交（中优先级 #7，用户选择 Pending 模式）
   - 扩展 NpcPerformer 输出契约，新增 pending side effects（目标变更、记忆变更、current_event 等）结构，并补充 NPC 下游 state_change 输入输出契约；保留并显式校验 scheduler 的系统侧优先级刷新（按敏捷降序重排，且任意 status value<=0 的 NPC 从调用队列剔除）。
   - NpcPerformer.run 只生成输出与 pending 数据，不直接写世界状态；每个 NPC 的世界变更由 TurnOrchestrator 驱动 state_change + patch 提交。
   - TurnOrchestrator 在玩家 state 分支成功后，按 scheduler 顺序逐个执行 NPC：为当前 NPC 重新读取最新世界视图 -> 执行 performer/rule/evolution -> 提交该 NPC 的 state patch 与 pending side effects -> 再激活下一 NPC。
   - 失败路径采用“逐 NPC 事务隔离”：当前 NPC 提交失败时回滚当前 NPC、停止后续 NPC 激活，不提交其余 NPC 副作用，并保持 fallback/terminated 语义一致。
   - 依赖：Phase C；可与 Phase D 并行。

6. Phase F - 视图层去重与可写字段显式声明（中优先级 #6 #9）
   - 在 agent_map_input 模型中抽取通用地图基础视图，消除 DMWorldView/MapSlice/StateAgentWorldView 重复字段定义。
   - 在实体模型层引入可写声明元数据（writable 标记与子路径策略），将“可写边界”从 Provider 的硬编码迁移为模型驱动。
   - WorldDataProvider 重构 StateAgentWorldView 构建逻辑：按元数据+extension registry 生成 writable_fields，并与 DSL/StatePatchRuntime 的可写规则对齐。
   - 回归校验 location 真值优先、description.public 禁写、索引字段禁写、extensions mutable 约束。
   - 依赖：Phase B。

7. Phase G - 健壮性与术语常量治理（高优先级 #3，中优先级 #8 #10）
   - service 的 JSON 提取改为正则提取 fenced json + 边界回退，覆盖 markdown 前后缀和多余空白等场景。
   - 将敏捷判定键与阻断状态键抽到统一配置（loader + config.yaml + schema + form），消除 bootstrap 与 scheduler 的重复魔法字符串。
   - 保留对旧键/旧行为兼容，确保“缺少敏捷属性禁止进入系统”约束仍强制生效。
   - 清理未使用模型 NarrativeHistory，并修正相关导入。
   - 依赖：Phase B；可与 Phase D/F 并行。

8. Phase H - 测试与验收闭环（收尾）
   - 运行现有回归：phase3 并发状态管线、phase4 叙事合并、phase5 scheduler/performer、phase6 consistency。
   - 新增针对本轮改动的回归用例：
     - 快照强类型访问与序列化互通。
     - DMAgent 新签名调用契约。
     - NPC 执行栅栏：仅在玩家 state 成功后启动，且按 scheduler 顺序逐 NPC 提交完成后再激活下一 NPC。
     - 可写字段元数据驱动视图生成。
     - JSON 提取对 markdown 包裹响应的健壮性。
     - 敏捷配置化与缺失阻断行为。
   - 完成静态错误检查并清理告警。
   - 依赖：Phase D/E/F/G。

**Relevant files**
- c:/Users/25173/Desktop/engine_refacting/src/engine/engine.py — 入口瘦身、编排逻辑迁移、调用契约调整。
- c:/Users/25173/Desktop/engine_refacting/src/engine/bootstrap_validation.py — 敏捷必需校验改为配置驱动。
- c:/Users/25173/Desktop/engine_refacting/src/engine — 新增 TurnOrchestrator / ConsistencyOrchestrator / NarrativeTruthManager 模块。
- c:/Users/25173/Desktop/engine_refacting/src/data/model/world_state.py — WorldSnapshot 类型定义与 get_snapshot 返回契约。
- c:/Users/25173/Desktop/engine_refacting/src/data/model/agent_input.py — DM/NPC 输入契约字段一致化。
- c:/Users/25173/Desktop/engine_refacting/src/data/model/agent_output.py — NpcPerformer pending side effects 输出契约。
- c:/Users/25173/Desktop/engine_refacting/src/data/model/base.py — writable 元数据声明与实体约束。
- c:/Users/25173/Desktop/engine_refacting/src/data/model/input/agent_map_intput.py — 视图基类抽象、重复字段收敛。
- c:/Users/25173/Desktop/engine_refacting/src/data/model/input/agent_narrative_input.py — 删除未使用 NarrativeHistory。
- c:/Users/25173/Desktop/engine_refacting/src/utils/world_provider.py — StateAgent 可写字段生成逻辑改造。
- c:/Users/25173/Desktop/engine_refacting/src/agent/llm/input_agent.py — DMAgent.run 签名统一与语义校验入参来源调整。
- c:/Users/25173/Desktop/engine_refacting/src/agent/llm/evolution_agent.py — 因果链归一化逻辑收敛。
- c:/Users/25173/Desktop/engine_refacting/src/agent/llm/npc_perform_agent.py — 输出 pending 副作用，移除直接提交。
- c:/Users/25173/Desktop/engine_refacting/src/agent/llm/npc_schedul_agent.py — 常量配置化接入。
- c:/Users/25173/Desktop/engine_refacting/src/agent/llm/service.py — JSON 提取健壮化。
- c:/Users/25173/Desktop/engine_refacting/src/rule/rule_system.py — 快照类型签名更新。
- c:/Users/25173/Desktop/engine_refacting/src/rule/dsl.py — 快照访问强类型化。
- c:/Users/25173/Desktop/engine_refacting/src/rule/state_patch.py — 快照与可写约束消费逻辑同步更新。
- c:/Users/25173/Desktop/engine_refacting/src/storage/sqlite_world_snapshot_repository.py — 强类型快照持久化入口。
- c:/Users/25173/Desktop/engine_refacting/src/config/loader.py — 新增术语常量配置模型。
- c:/Users/25173/Desktop/engine_refacting/config/config.yaml — 默认值落地。
- c:/Users/25173/Desktop/engine_refacting/config/config.schema.yaml — 新配置字段校验。
- c:/Users/25173/Desktop/engine_refacting/config/config.form.yaml — 表单字段同步。
- c:/Users/25173/Desktop/engine_refacting/tests/phase3/test_phase3_concurrent_state_pipeline.py — 并发/回滚/副作用回归。
- c:/Users/25173/Desktop/engine_refacting/tests/phase4/test_phase4_narrative_merger.py — 叙事真值闭环回归。
- c:/Users/25173/Desktop/engine_refacting/tests/phase5/test_phase5_npc_scheduler.py — 调度排序与预算回归。
- c:/Users/25173/Desktop/engine_refacting/tests/phase5/test_phase5_npc_performer.py — performer 输出与下游链路回归。
- c:/Users/25173/Desktop/engine_refacting/tests/phase6/test_phase6_consistency_agent.py — 一致性循环回归。

**Verification**
1. 使用工作区 Python 解释器执行现有 phase3-phase6 单测，确认行为不回退。
2. 执行新增高风险回归用例（快照类型、DM 签名、NPC 串行提交栅栏、敏捷重排与任意 status<=0 过滤、可写字段元数据、JSON 提取、敏捷配置化）。
3. 对比关键事件结构：phase3_concurrent_nl 返回中的 state/npcscheduler/npcperformer/merger/consistency 字段保持向后兼容。
4. 手动走查一条失败链路：state patch 连续失败时，NPC pending 不落库、narrative 不写入正式池、fallback 正常返回。
5. 手动走查一条成功链路：玩家 state 成功后按 scheduler 顺序逐 NPC 执行并逐次提交；验证 NPC2 获取到 NPC1 提交后的最新世界视图，且 goal/memory 与 narrative truth 同步。
6. 运行静态错误检查，确认无新增类型错误与引用错误。

**Decisions**
- Engine 拆分深度：选择一次性重构（TurnOrchestrator + ConsistencyOrchestrator + NarrativeTruthManager）。
- NPC 提交语义：选择 Pending 副作用 + 串行提交栅栏；玩家 state 成功后再启动 NPC，且每个 NPC 必须在上一个 NPC 提交完成后再激活并读取最新世界视图。
- 快照类型化范围：选择全量替换（所有快照消费方切换到强类型访问）。
- 范围边界：本轮仅处理 line 清单高优先级与中优先级（#1-#10），低优先级项（#11-#14）不纳入本轮实施。

**Further Considerations**
1. 为降低一次性重构风险，建议在 Phase C 完成后先做一次中间回归（仅到 Engine 拆分完成），再进入 Phase D-G 的行为级改造。