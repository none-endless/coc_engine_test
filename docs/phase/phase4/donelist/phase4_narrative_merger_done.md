# Phase4 叙事闭环完成记录

## 已完成

- 在 [src/engine/engine.py](c:/Users/25173/Desktop/engine_refacting/src/engine/engine.py) 中补齐 `NarrativeAgent -> MergerAgent -> NarrativeInfo.recent` 的提交闭环。
- 新增 [src/agent/llm/merger_agent.py](c:/Users/25173/Desktop/engine_refacting/src/agent/llm/merger_agent.py)，由其消费 `NarrativeDraft + e7` 输出精简后的叙事真值。
- 在 [src/data/model/narrative.py](c:/Users/25173/Desktop/engine_refacting/src/data/model/narrative.py) 中抽出叙事草稿与流式事件模型，避免在业务代码里重复定义结构。
- 将 `NarrativeInfo` 与 `WorldState` 保持物理分离：世界真值仍由 [src/data/model/world_state.py](c:/Users/25173/Desktop/engine_refacting/src/data/model/world_state.py) 持有，叙事真值由引擎内独立的 `NarrativeInfo` 持有并仅在 merger 成功后写入。
- `state_change_agent` 输入仍只包含 `e4 + world_info + fallback_error`，不读取叙事池，也不接触叙事草稿。

## 验收对应

- `NarrativeInfo.recent` 通过 `agent.narrative.recent_turns` 配置限长，默认保留最近 5 回合。
- 玩家侧返回的 `narrative.llm_output.narrative_str` 现在是 merger 合并后的精简结果，不再直接暴露 narrative 草稿文本。
- 当状态提交失败并回滚时，草稿会被标记为 `discarded`，不会写入 `NarrativeInfo.recent`。

## 验证

- 单测：
  - [tests/phase3/test_phase3_concurrent_state_pipeline.py](c:/Users/25173/Desktop/engine_refacting/tests/phase3/test_phase3_concurrent_state_pipeline.py)
  - [tests/phase4/test_phase4_narrative_merger.py](c:/Users/25173/Desktop/engine_refacting/tests/phase4/test_phase4_narrative_merger.py)
