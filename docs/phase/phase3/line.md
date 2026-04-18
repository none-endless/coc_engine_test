### Phase 3：并发分支与状态变更核心 (The Blue Dashed Lines)

**目标**：实现文档中蓝色虚线标注的 **并发三叉戟** 以及 **状态写入 DSL** 与 **容错回滚机制**。这是系统最难部分。

#### 需要做什么
1.  **ShortSummary 分发器**：
    -   在 EvolutionAgent 完成后，**并发生成** 三个异步任务：
        -   任务 A：`NpcSchedulerAgent` 输入
        -   任务 B：`StateChangeAgent` 输入
        -   任务 C：`NarrativeAgent` 输入
2.  **StateChangeAgent 与 DSL 实现**：
    -   LLM 根据 `ShortSummary` 生成 `StatePatch` 指令列表（`MOVE`、`SET`、`UPDATE`、`ADD`、`ASSERT`）。
    -   **配套校验系统**：严格校验字段权限（`mutable`）、类型匹配、数值边界、目标有效性。
    -   **提交临界区**：使用 `asyncio.Lock` 保证 `StatePatch` **串行提交**。
3.  **容错与回滚机制**：
    -   状态变更失败触发 **自动重试**（LLM 修正指令）。
    -   超过 `max_retry_count` 或超时：**执行回滚**（恢复至回合开始快照）。
    -   **降级处理**：丢弃本次叙事分支输出，输出 `fallback_error`，终止交互。

#### 验收需求
-   并发任务必须携带相同的 `turn_id`。
-   尝试直接 `SET` `description.public` 或修改 `char_index` 会被系统拦截（`FIELD_NOT_MUTABLE`）。
-   模拟连续 3 次 StatePatch 失败，系统能自动回滚世界状态并输出兜底文案。

#### 生产待检查物料
-   [ ] 并发三任务执行时序图与日志文件（证明三个任务是同时启动的）。
-   [ ] 状态回滚测试用例与恢复后的世界状态比对报告。
-   [ ] 写入 DSL 错误类型定义表与对应返回码。

