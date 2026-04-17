# StatePatch DSL 错误类型与返回码

| 返回码 | 含义 | 典型触发场景 |
|---|---|---|
| `ENTITY_NOT_FOUND` | 目标实体不存在 | `target_path` 的实体 ID 不在世界状态中 |
| `FIELD_NOT_FOUND` | 字段路径不存在 | 写入路径拼写错误、索引越界 |
| `FIELD_NOT_MUTABLE` | 字段不可写 | 直接写 `description.public`、`char_index`、`item_index` |
| `FIELD_TYPE_MISMATCH` | 字段类型不匹配 | 对数值字段使用 `SET`、对非列表字段 `ADD` |
| `VALUE_OUT_OF_RANGE` | 数值越界 | `attributes.*.value` 超过 `max_value` 或小于 `min_value` |
| `DUPLICATE_ENTRY` | 列表追加重复元素 | `ADD` 追加已存在元素 |
| `ENTRY_NOT_FOUND` | 列表删除目标不存在 | `REMOVE` 删除不存在元素 |
| `INVALID_TARGET` | MOVE 目标非法 | 角色移动到不存在地图、物品移动到不存在实体 |
| `ASSERT_FAILED` | 前置断言失败 | `ASSERT` 表达式为 false 或 DSL 解析失败 |
| `STATE_PATCH_RETRY_EXHAUSTED` | 重试耗尽触发降级 | 超过 `max_retry_count` 或超时后执行回滚 |
