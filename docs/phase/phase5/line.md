### Phase 5：NPC 自主行为与调度闭环 (NPC Scheduler & Performer)

**目标**：实现 NPC 的自主思考与行为执行，并应用 **调度预算规范** 防止无限循环。

#### 需要做什么
1.  **NpcSchedulerAgent 实现**：
    -   消费 `ShortSummary`，决定激活哪些 NPC。
    -   **预算控制**：硬限制 `max_actions_per_turn` 与 `cooldown`。
    -   **顺序排序**：根据 `敏捷` 属性排序执行列表。
2.  **NpcPerformerAgent 实现**：
    -   **注意**：输出直接进入 `RuleSystem` 或 `EvolutionAgent`，**绕过 InputSystem 和 DMAgent**。
    -   **目标管理**：自主维护 `Goal` 系统（`baseGoal`, `activeGoal`）。
    -   **上下文注入**：读取 `currentEvent` 并压入短期记忆。

#### 验收需求
-   验证单个回合内 NPC 动作数不超过配置的 `3` 次。
-   同一 NPC 在冷却回合内不会再次出现在调度列表。
-   NPC 能够自主决定移动、对话或修改自身目标。

#### 生产待检查物料
-   [ ] NPC 敏捷度排序执行逻辑的单元测试。
-   [ ] NPC Goal 历史记录（`goalHistory`）写入验证截图。
