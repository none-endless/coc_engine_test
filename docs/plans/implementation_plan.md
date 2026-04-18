根据你提供的规范文档，我为你制定了一份从 Phase 0 到 Phase 7 的实施计划。每个 Phase 均包含具体任务、验收需求和待检查物料清单，并明确标注了蓝色虚线并发部分的边界。

---

### Phase 0：基础设施与核心模型搭建 (Foundation)

**目标**：确立项目骨架、核心数据模型、配置管理和最基础的存储/快照能力，为后续所有 Agent 开发提供不可变基础。

#### 需要做什么
1.  **项目初始化与配置管理**：
    -   搭建 Python 项目结构（`src/`、`tests/`、`config/`）。
    -   实现配置加载器，严格遵循 **优先级：命令行 > 环境变量 > 配置文件 > 默认值**。
    -   编写 `config.schema.yaml` 与 `config.yaml` 模板。
2.  **数据模型定义**：
    -   使用 Pydantic 或 Dataclasses 严格定义 **世界模型** (`Map`, `Item`, `Character`) 和 **记忆/目标模型** (`Memory`, `Goal`)。
    -   实现 **实体 ID 系统规范**（`[实体类型]-[有意义名称]-[唯一后缀]`）的校验逻辑，提供全局 ID 生成器与注册表。
3.  **世界状态容器与快照**：
    -   实现 `WorldState` 单例容器，维护 `char_index` / `item_index` 的自动派生逻辑。
    -   实现只读快照生成器 (`get_snapshot`)，确保读取不阻塞。
4.  **回合事务与日志基底**：
    -   定义 `TurnEnvelope` 结构（`turn_id`, `trace_id`）。
    -   实现基础的事件日志记录器（用于 `memory.log` 和 `shortLog`）。

#### 验收需求
-   配置文件修改后系统行为能正确覆盖。
-   尝试创建非法格式 ID（如 `char#bedroom`）会被拒绝。
-   修改 `Character.location` 后，对应的 `Map.char_index` 自动更新且无法通过 Agent 代码直接篡改索引。

#### 生产待检查物料
-   [ ] `config.schema.yaml` 文件。
-   [ ] 实体 ID 校验工具类单元测试通过报告。
-   [ ] 世界状态快照读写分离逻辑验证脚本。

---

### Phase 1：输入分流与规则引擎 (Input & Rule System)

**目标**：打通玩家输入入口，实现元命令与自然语言的精准分流，并落地条件 DSL 解析与执行。

#### 需要做什么
1.  **InputSystem 实现**：
    -   检测输入是否以 `\` 开头，路由 `\look`、`\inventory` 至 `RuleSystem`。
    -   自然语言输入路由至 `DMAgent` 接口。
2.  **条件 DSL 解析器**：
    -   实现词法/语法解析器，支持 `==`, `!=`, `>`, `<`, `in`, `not in` 及逻辑组合 `and`, `or`。
    -   确保解析器基于 **同一版本世界快照** 求值。
3.  **RuleSystem (规则系统)**：
    -   处理元命令：直接读取快照生成结果文本。
    -   处理 COC 鉴定：接收 `DMAgent` 的鉴定请求，计算大成功/成功/失败/大失败。
    -   处理 `ASSERT` 断言校验。

#### 验收需求
-   输入 `\look` 不经过任何 LLM 调用，直接返回当前 `location` 的 `description.public`。
-   输入复杂的条件 DSL 字符串（如 `char-player-0000.attributes.hp.value > 0 and item-key-0008.location == char-player-0000`）能正确返回布尔值。
-   COC 鉴定骰子逻辑正确且结果格式包含 `id`、`name`、`结果类型`。

#### 生产待检查物料
-   [ ] 条件 DSL 语法解析器 AST 设计文档。
-   [ ] 元命令执行耗时日志（用于证明未调用 LLM）。

---

### Phase 2：核心 Agent 串行链路 (DM -> Evolution)

**目标**：实现自然语言输入下的 **主链路串行流程**，即 `DMAgent` -> `RuleSystem` -> `EvolutionAgent`，确保单线程逻辑稳固。

#### 需要做什么
1.  **DMAgent 实现**：
    -   **意图解析**：拦截非法输入，判断是否需要鉴定（高风险/剧情影响/软约束）。
    -   **结构化输出**：输出包含 `intent`、`鉴定类型`、`属性名列表`、`参与对象ID` 的 JSON。
    -   **错误自愈**：接入配套校验系统，当 LLM 输出非法字段名或 ID 时，在重试次数内引导 LLM 修正。
2.  **EvolutionAgent 实现**：
    -   接收鉴定结果或直接的自然语言意图。
    -   生成 **推演概要 (ShortSummary)**，判断变化是否对玩家可见。
    -   **不可见变更**：直接写入 `回合因果链 (e7)`，跳过叙事生成。

#### 验收需求
-   DMAgent 输出格式包含严格的 `鉴定对象` 字段且 ID 真实存在。
-   EvolutionAgent 生成的 ShortSummary 包含完整的 `trace_id` 和 `turn_id`。
-   隐蔽行动（如 NPC 偷偷下毒）能够被正确标记为不可见，不触发叙事。

#### 生产待检查物料
-   [ ] DMAgent 与 EvolutionAgent 的 System Prompt 模板。
-   [ ] 非鉴定意图与鉴定意图的分流日志样例。

---

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

---

### Phase 4：叙事闭环与双真值池 (Narrative & Merger)

**目标**：打通叙事生成与合并链路，落实“世界真值”与“叙事真值”的物理隔离。

#### 需要做什么
1.  **NarrativeAgent 实现**：
    -   消费 `ShortSummary` 生成自然语言片段（`narrative_str`）。
    -   **流式输出**：支持 SSE 或 WebSocket 推送叙事内容给前端。
2.  **MergerAgent 实现**：
    -   消费 `回合因果链 (e7)` 和可选 `narrative_str`。
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

---

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

---

### Phase 6：一致性维护与长期记忆接口 (Consistency & Memory)

**目标**：引入定时维护机制，保障世界状态与叙事状态无逻辑冲突，并预留长期记忆扩展槽。

#### 需要做什么
1.  **ConsistencyAgent 实现**：
    -   **定时触发**：每 `description.add_interval` (默认 10) 回合触发。
    -   **描述合并**：将 `description.add` 内容整理写入 `description.public` 并清空缓冲。
    -   **冲突检测**：检查 `location` 真值与叙事描述中的位置信息是否矛盾。
    -   **关键事实提取**：从 `shortLog` 提取内容更新 `keyFacts`。
2.  **Memory 维护**：
    -   实现 `short` 队列的 FIFO 溢出逻辑（保留最近 15 条）。
    -   实现 `shortLog` 队列维护。
    -   **预留接口**：定义 `VectorStore` 抽象基类，用于未来接入向量数据库。

#### 验收需求
-   经过 10 回合后，`description.add` 被自动清空，`description.public` 新增了一条描述。
-   NPC 的 `keyFacts` 列表能正确反映过去 30 回合内的重要事件摘要。

#### 生产待检查物料
-   [ ] 一致性维护流程的回合计数器日志。
-   [ ] `VectorStore` 接口定义文件 (`interface.py`)。

---

### Phase 7：全链路测试、调试工具与部署准备 (Testing & Ops)

**目标**：完善调试可视化、全链路压测、错误注入测试，准备生产环境部署物料。

#### 需要做什么
1.  **调试工具 (Debug UI)**：
    -   开发简单的 CLI 面板或 Web 面板，展示当前 `回合数`、`Trace ID`、`世界快照`、`叙事池` 和 `链路日志 (E1-E7)`。
2.  **错误注入与混沌测试**：
    -   模拟 LLM 超时。
    -   模拟 StatePatch 校验反复失败。
    -   验证回滚后状态完整性。
3.  **文档与部署**：
    -   整理 `.env.example`、Dockerfile、docker-compose.yml。
    -   编写开发者手册：如何新增一个 Agent 或自定义属性。

#### 验收需求
-   系统能够在注入 LLM 故障后自动降级而不崩溃。
-   开发者可以通过 `trace_id` 追踪一笔玩家输入从 `InputSystem` 到 `MergerAgent` 的完整生命周期。

#### 生产待检查物料
-   [ ] 系统架构部署图（Docker 拓扑）。
-   [ ] 全流程 `trace_id` 链路追踪截图（展示从 E1 到 E7 的数据流）。
-   [ ] 最终回滚测试通过的 Junit/Pytest 报告。