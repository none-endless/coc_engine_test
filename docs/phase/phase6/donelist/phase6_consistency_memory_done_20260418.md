# Phase6 一致性维护与长期记忆接口完成记录

## 本次完成内容

- 新增 `ConsistencyAgent`，输出改为复用 `state_change_agent` 的原子 DSL：
  - `changes`
  - `can_proceed`
  - `system_message`
- 引擎接入 phase6 周期维护流程：
  - 按 `description.add_interval` 固定回合触发
  - 执行一致性 DSL 修补
  - 合并全部 `description.add -> description.public`
  - 清空 `description.add`
  - 维护 NPC `short / short_log / key_facts`
  - 压缩 `narrative_info.recent`
  - 不可修复时设置全局阻断并拒绝后续回合继续消费不一致快照
- 新增 `VectorStoreInterface`，为长期记忆向量库接入预留抽象接口。

## 关键实现文件

- `src/agent/llm/consistency_agent.py`
- `src/agent/prompt/consistency_prompt.py`
- `src/data/model/agent_output.py`
- `src/engine/engine.py`
- `src/interface/vector_store_interface.py`
- `tests/phase6/test_phase6_consistency_agent.py`

## 验证命令

```bash
python -m unittest tests.phase6.test_phase6_consistency_agent
python -m unittest tests.phase4.test_phase4_narrative_merger tests.phase5.test_phase5_npc_scheduler tests.phase5.test_phase5_npc_performer
python -m unittest tests.phase3.test_phase3_concurrent_state_pipeline
```

## 验证结果

- `tests.phase6.test_phase6_consistency_agent`：3 条通过
- `tests.phase4.test_phase4_narrative_merger`：通过
- `tests.phase5.test_phase5_npc_scheduler`：通过
- `tests.phase5.test_phase5_npc_performer`：通过
- `tests.phase3.test_phase3_concurrent_state_pipeline`：9 条通过

## 当前边界

- 一致性 agent 只负责世界池修补 DSL 与阻断判定，符合 draft_spec 中“世界真值优先”和 `agent.md` 中“LLM I/O 精确且短”的约束。
