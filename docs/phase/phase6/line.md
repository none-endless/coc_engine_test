
### Phase 6：一致性维护与长期记忆接口 (Consistency & Memory)

**目标**：引入定时维护机制，保障世界状态与叙事状态无逻辑冲突，并预留长期记忆扩展槽。

#### 需要做什么
1.  **ConsistencyAgent 实现**：
    -   **定时触发**：每 `description.add_interval` (默认 10) 回合触发。
    -   **描述合并**：将 `description.add` 内容整理写入 `description.public` 并清空缓冲。
    -   **冲突检测**：检查 `location` 真值与叙事描述中的位置信息是否矛盾。
    -   **关键事实提取**：从 `shortLog` 提取内容更新 `keyFacts`,压缩`narrative_infor`。
2.  **Memory 维护**：
    -   实现 `short` 队列的 FIFO 溢出逻辑（保留最近 15 条）。
    -   实现 `shortLog` 队列维护。
    -   **预留接口**：定义 `VectorStore` 抽象基类，用于未来接入向量数据库。

#### 验收需求
-   经过 10 回合后，`description.add` 被自动清空，`description.public` 新增了一条描述。
-   NPC 的 `keyFacts` 列表能正确反映过去 30 回合内的重要事件摘要。
-   `narrative_infor`被压缩,从由多个元素组成的列表被压缩为仅一个压缩后事实组成的列表

#### 生产待检查物料
-   [ ] 一致性维护流程的回合计数器日志。
-   [ ] `VectorStore` 接口定义文件 (`interface.py`)。
