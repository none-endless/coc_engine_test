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

