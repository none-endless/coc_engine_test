# line.md 完成情况汇报表

## 范围说明
- 本轮目标按你的要求优先完成 C、B，并补齐 H 缺失用例。
- 本表逐条对齐 [docs/phase/phase8/line.md](docs/phase/phase8/line.md) 的 1-14 条目。

## 回归结果
- 组合回归：44 tests，0 failures，0 errors，0 skipped。
- 静态检查：0 errors。

## 逐条对照

| 条目 | 状态 | 对应阶段 | 关键证据 | 验证证据 | 备注 |
| --- | --- | --- | --- | --- | --- |
| [#1 Engine 拆分](docs/phase/phase8/line.md#L7) | 完成 | C | [src/engine/turn_orchestrator.py#L36](src/engine/turn_orchestrator.py#L36), [src/engine/consistency_orchestrator.py#L26](src/engine/consistency_orchestrator.py#L26), [src/engine/narrative_truth_manager.py#L24](src/engine/narrative_truth_manager.py#L24), [src/engine/engine.py#L702](src/engine/engine.py#L702) | [src/engine/engine.py#L327](src/engine/engine.py#L327), [src/engine/engine.py#L331](src/engine/engine.py#L331) | Engine 已改为编排委托入口，非薄封装。 |
| [#2 快照强类型](docs/phase/phase8/line.md#L21) | 完成（本轮收口） | B | [src/data/model/world_state.py#L13](src/data/model/world_state.py#L13), [src/data/model/world_state.py#L142](src/data/model/world_state.py#L142), [src/rule/dsl.py#L205](src/rule/dsl.py#L205), [src/rule/state_patch.py#L49](src/rule/state_patch.py#L49) | [tests/phase0/test_phase0_foundation.py#L110](tests/phase0/test_phase0_foundation.py#L110), [tests/phase4/test_phase4_narrative_merger.py#L161](tests/phase4/test_phase4_narrative_merger.py#L161) | DSL 与 StatePatch 的 typed snapshot 消费链路已收口。 |
| [#3 JSON fenced 提取](docs/phase/phase8/line.md#L33) | 完成 | H | [src/agent/llm/service.py#L363](src/agent/llm/service.py#L363), [src/agent/llm/service.py#L373](src/agent/llm/service.py#L373) | [tests/phase1/test_input_rule_dsl_phase1.py#L87](tests/phase1/test_input_rule_dsl_phase1.py#L87) | 支持 markdown fenced json 提取。 |
| [#4 因果链归一](docs/phase/phase8/line.md#L44) | 完成 | C | [src/engine/engine.py#L467](src/engine/engine.py#L467), [src/agent/llm/evolution_agent.py#L30](src/agent/llm/evolution_agent.py#L30) | 44/44 回归通过 | 归一化入口集中在 Engine，EvolutionAgent 直接消费标准链。 |
| [#5 Agent run 签名统一](docs/phase/phase8/line.md#L56) | 完成 | H | [src/agent/llm/input_agent.py#L30](src/agent/llm/input_agent.py#L30) | [tests/phase2/test_phase2_serial_pipeline.py#L206](tests/phase2/test_phase2_serial_pipeline.py#L206) | DMAgent.run 仅保留 self + agent_input。 |
| [#6 视图基类去重](docs/phase/phase8/line.md#L74) | 完成 | 历史已完成 | [src/data/model/input/agent_map_intput.py#L66](src/data/model/input/agent_map_intput.py#L66), [src/data/model/input/agent_map_intput.py#L73](src/data/model/input/agent_map_intput.py#L73), [src/data/model/input/agent_map_intput.py#L108](src/data/model/input/agent_map_intput.py#L108), [src/data/model/input/agent_map_intput.py#L148](src/data/model/input/agent_map_intput.py#L148) | 44/44 回归通过 | BaseMapView 已复用到核心 view 模型。 |
| [#7 NPC 副作用提交模式](docs/phase/phase8/line.md#L85) | 完成 | C | [src/engine/engine.py#L1025](src/engine/engine.py#L1025), [src/engine/engine.py#L1469](src/engine/engine.py#L1469), [src/engine/engine.py#L1617](src/engine/engine.py#L1617), [src/agent/llm/npc_perform_agent.py#L125](src/agent/llm/npc_perform_agent.py#L125) | [tests/phase5/test_phase5_npc_performer.py#L310](tests/phase5/test_phase5_npc_performer.py#L310) | 副作用提交已内聚到 state 分支 post_apply_hook，失败统一回滚并返回 NPC_SIDE_EFFECTS_FAILED。 |
| [#8 魔法字符串治理](docs/phase/phase8/line.md#L97) | 完成 | H | [src/config/constants.py#L4](src/config/constants.py#L4), [src/config/loader.py#L28](src/config/loader.py#L28), [src/engine/bootstrap_validation.py#L5](src/engine/bootstrap_validation.py#L5), [src/agent/llm/npc_schedul_agent.py#L5](src/agent/llm/npc_schedul_agent.py#L5) | [tests/phase3/test_phase3_concurrent_state_pipeline.py#L594](tests/phase3/test_phase3_concurrent_state_pipeline.py#L594), [tests/phase5/test_phase5_npc_scheduler.py#L139](tests/phase5/test_phase5_npc_scheduler.py#L139) | 敏捷默认键已统一收口到配置常量，调度与启动校验共用同一源。 |
| [#9 可写字段显式机制](docs/phase/phase8/line.md#L111) | 完成 | H | [src/data/model/base.py#L225](src/data/model/base.py#L225), [src/data/model/base.py#L257](src/data/model/base.py#L257), [src/data/model/base.py#L288](src/data/model/base.py#L288), [src/utils/world_provider.py#L191](src/utils/world_provider.py#L191) | [tests/phase1/test_input_rule_dsl_phase1.py#L93](tests/phase1/test_input_rule_dsl_phase1.py#L93) | writable + extension registry 驱动已落地并回归。 |
| [#10 NarrativeHistory 清理](docs/phase/phase8/line.md#L122) | 完成 | 历史已完成 | [src/data/model/input/agent_narrative_input.py](src/data/model/input/agent_narrative_input.py) | 44/44 回归通过 | 当前模型文件已无 NarrativeHistory 定义。 |
| [#11 service 异步化 httpx](docs/phase/phase8/line.md#L134) | 未纳入本轮 | 非本轮范围 | [src/agent/llm/service.py](src/agent/llm/service.py) | - | 仍使用同步 transport + 线程包装。 |
| [#12 日志国际化](docs/phase/phase8/line.md#L144) | 未纳入本轮 | 非本轮范围 | [src/agent/llm/input_agent.py](src/agent/llm/input_agent.py) | - | 本轮未改动该策略。 |
| [#13 EntityIdGenerator 防循环](docs/phase/phase8/line.md#L154) | 未纳入本轮 | 非本轮范围 | [src/data/model/entity_id.py](src/data/model/entity_id.py) | - | 本轮未引入最大尝试次数上限。 |
| [#14 Consistency 重试内聚到 Agent](docs/phase/phase8/line.md#L164) | 完成 | C | [src/agent/llm/consistency_agent.py#L18](src/agent/llm/consistency_agent.py#L18), [src/agent/llm/consistency_agent.py#L62](src/agent/llm/consistency_agent.py#L62), [src/engine/consistency_orchestrator.py#L154](src/engine/consistency_orchestrator.py#L154) | [tests/phase6/test_phase6_consistency_agent.py#L260](tests/phase6/test_phase6_consistency_agent.py#L260) | Consistency 重试循环（LLM异常+应用校验反馈）已内聚到 ConsistencyAgent.run_with_retry。 |

## 结论
- 你要求的 C、B、H 目标已完成并通过同组全量回归。
- 本轮已完成 #7、#8、#14 收尾；当前仅剩低优先级 #11-#13 未纳入实施范围。