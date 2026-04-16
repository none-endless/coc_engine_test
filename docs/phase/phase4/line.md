
### Phase 4：叙事闭环与双真值池 (Narrative & Merger)

**目标**：打通叙事生成与合并链路，落实“世界真值”与“叙事真值”的物理隔离。

#### 需要做什么
1.  **NarrativeAgent 实现**：
    -   消费 `ShortSummary` 生成自然语言片段（`NarrativeDraft`）。
    -   **流式输出**：支持 SSE 或 WebSocket 推送叙事内容给前端。
2.  **MergerAgent 实现**：
    -   消费 `回合因果链 (e7)` 和 `NarrativeDraft`。
    -   执行去重、简化、合并，生成 **叙事真值** 写入 `叙事信息.recent`。
3.  **真值池隔离**：
    -   `WorldInfo` (StateChangeAgent 维护) 与 `NarrativeInfo` (MergerAgent 维护) 物理隔离存储。
    -   Agent 读取权限校验：确保 `StateChangeAgent` 无法读取 `NarrativeInfo` 的草稿内容。

#### 验收需求
-   一回合结束后，`叙事信息.recent` 列表长度不超过配置的 `5` 回合。
-   玩家视角的叙事文本包含由 MergerAgent 合并后的精简描述，而非冗长的内部推演文本。

#### 生产待检查物料
-   [ ] 流式叙事输出接口文档与延迟测试数据。
-   [ ] 世界真值表 (SQLite) 与叙事真值表 (SQLite) 的表结构定义。
