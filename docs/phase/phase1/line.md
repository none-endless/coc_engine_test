
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

