# LLM驱动的文字冒险游戏 - 扩展规范

> 本文档为 `draft_spec.md` 的扩展补充，包含并发控制、错误处理、持久化、DSL、配置管理等规范

---

## 1. 并发控制规范

简化设计，避免过度工程化

1. **并发范围**：仅蓝色虚线标注的分支（short_summary生成后触发npc_scheduler与stateChange并发）
2. **线程安全**：使用Python `asyncio.Lock` 保护共享状态池的写入操作
3. **读取策略**：读取世界信息时使用快照，不阻塞并发读取
4. **写入策略**：写入时获取锁，完成后释放，确保状态变更的原子性

---

## 2. 状态写入与错误处理规范

### 2.1 state_change_agent 可写入的范围及类型

| 可写入实体 | 可写入字段类型 | 说明 |
|-----------|---------------|------|
| 人物/物品/地图 | `status`, `attribute` | 数值或枚举类 |
| 人物/物品 | `location` | 位置（唯一真值） |
| 任意实体列表字段 | `charDist`, `itemDist`, `public`, `add` 等 | 列表 |

### 2.2 三大操作符

| 操作符 | 用途 | 适用类型 |
|--------|------|----------|
| `ADD` | 向列表字段添加元素 | list 类型 |
| `UPDATE` | 更新数值或枚举类字段 | number, enum 类型 |
| `MOVE` | 变更位置字段 | 唯一真值 类型（如location） |

### 2.3 操作符语法（简化）

```python
# ADD 语法
ADD [实体ID].[列表字段] = [元素1, 元素2, ...]

# UPDATE 语法
UPDATE [实体ID].[数值/枚举字段] = [新值]

# MOVE 语法
MOVE [实体ID].location = [目标位置ID]
```

**示例**：
```
ADD char-guard-0001.charDist = [char-player-0000]
UPDATE char-player-0000.health = 80
MOVE item-key-0000.location = char-player-0000
```

### 2.4 持久化层校验流程

1. `state_change_agent` 生成配套更改列表，提交给持久化层
2. 持久化层逐一检查更改列表：
   - 检查ID是否存在
   - 检查字段类型是否匹配
   - 检查数值是否在有效范围内
3. 若校验失败：返回错误类型，让 `state_change_agent` 重新生成
4. 若校验通过：执行写入，更新状态池

### 2.5 错误类型定义

| 错误类型 | 说明 |
|---------|------|
| `ENTITY_NOT_FOUND` | 实体ID不存在 |
| `FIELD_TYPE_MISMATCH` | 字段类型不匹配 |
| `VALUE_OUT_OF_RANGE` | 数值超出有效范围 |
| `DUPLICATE_ENTRY` | 列表中添加重复元素 |
| `INVALID_TARGET` | 移动目标位置无效 |

### 2.6 容错与回滚机制

1. **重试机制**：提交失败后自动重试，可配置最大重试次数（默认3次）
2. **超时处理**：单次操作超时则终止，进入回滚流程
3. **回滚触发**：超过重试次数 / 超时 / 调用失败 → 执行状态回滚
4. **回滚内容**：将世界状态恢复到本次输入执行前的快照
5. **交互终止**：回滚完成后，终止此次交互，提示玩家重新输入
6. **错误日志**：记录错误类型、错误次数、时间戳到系统日志

### 2.7 可配置项

| 配置项 | 默认值 | 说明 |
|-------|-------|------|
| `max_retry_count` | 3 | 最大重试次数 |
| `retry_timeout_ms` | 5000 | 单次重试超时（毫秒） |
| `fallback_error` | "系统繁忙，请稍后重试" | 降级兜底输出 |

---

## 3. 持久化策略

### 3.1 数据库选型

- **SQLite**：轻量级、零配置、适合本地文件存储

### 3.2 表结构设计

```sql
-- 实体表（人物、物品、地图共用）
CREATE TABLE entities (
    id TEXT PRIMARY KEY,        -- 实体ID（唯一标识）
    type TEXT NOT NULL,         -- 实体类型：char/item/map
    data TEXT NOT NULL,         -- JSON格式存储完整实体数据
    updated_at INTEGER NOT NULL -- 更新时间戳
);

-- 系统日志表
CREATE TABLE error_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    turn INTEGER NOT NULL,      -- 回合数
    error_type TEXT NOT NULL,   -- 错误类型
    error_msg TEXT,             -- 错误信息
    timestamp INTEGER NOT NULL  -- 时间戳
);

-- 快照表（用于回滚）
CREATE TABLE snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    turn INTEGER NOT NULL,      -- 回合数
    data TEXT NOT NULL,         -- JSON格式世界状态快照
    timestamp INTEGER NOT NULL
);
```

### 3.3 持久化时机

1. **回合结束**：自动将当前世界状态写入实体表
2. **回滚触发**：保存当前快照后再执行回滚
3. **手动保存**：支持玩家主动存档（可选功能）

---

## 4. DSL 条件系统规范（简化版）

### 4.1 用途

目前仅用于结局判定

### 4.2 条件表达式语法

```
[实体ID].[属性] [操作符] [值]
```

### 4.3 支持的操作符

| 操作符 | 说明 |
|--------|------|
| `==` | 等于 |
| `!=` | 不等于 |
| `>` | 大于 |
| `<` | 小于 |
| `>=` | 大于等于 |
| `<=` | 小于等于 |
| `in` | 在列表中 |
| `not in` | 不在列表中 |

### 4.4 复合条件

使用 `and`, `or`, `not` 连接多个条件

### 4.5 示例

```
char-player-0000.health > 0 and item-key-0000.location in [char-player-0000, map-room-0001]
```

---

## 5. 配置管理规范

### 5.1 配置来源优先级

命令行参数 > 环境变量 > 配置文件 > 默认值

### 5.2 配置文件格式（YAML）

```yaml
# config.yaml

# LLM 配置
llm:
  model: "gpt-4"                    # 模型名称
  temperature: 0.7                  # 生成温度
  max_tokens: 2000                  # 最大token数
  timeout: 30                      # 超时时间（秒）
  api_base: "https://api.openai.com/v1"  # API地址

# 系统配置
system:
  max_retry_count: 3                # 状态变更最大重试次数
  retry_timeout_ms: 5000            # 重试超时（毫秒）
  fallback_error: "系统繁忙，请稍后重试"
  snapshot_interval: 10             # 快照保存间隔（回合数）

# Agent 配置
agent:
  dm:
    memory_turns: 5                 # 对话记忆保留回合数
  npc:
    memory_turns: 15                # NPC短期记忆保留回合数
    shortlog_turns: 30              # NPC日志保留回合数
  narrative:
    recent_turns: 5                 # 叙事最近保留回合数
```

### 5.3 魔法数字配置项

| 配置项 | 默认值 | 说明 |
|-------|-------|------|
| `system.max_retry_count` | 3 | 重试次数 |
| `system.retry_timeout_ms` | 5000 | 超时毫秒 |
| `agent.npc.memory_turns` | 15 | NPC短期记忆回合数 |
| `agent.npc.shortlog_turns` | 30 | NPC日志回合数 |
| `agent.dm.memory_turns` | 5 | DM对话记忆回合数 |
| `agent.narrative.recent_turns` | 5 | 叙事最近回合数 |
| `description.add_interval` | 10 | 描述变更合并间隔回合数 |

### 5.4 配置表单

提供 `config.schema.yaml` 用于0代码配置验证：

```yaml
# config.schema.yaml
type: object
properties:
  llm:
    type: object
    properties:
      model: { type: string, enum: ["gpt-4", "gpt-3.5-turbo", "claude-3"] }
      temperature: { type: number, minimum: 0, maximum: 2 }
      max_tokens: { type: integer, minimum: 100, maximum: 8000 }
  system:
    type: object
    properties:
      max_retry_count: { type: integer, minimum: 1, maximum: 10 }
      retry_timeout_ms: { type: integer, minimum: 1000, maximum: 30000 }
```
