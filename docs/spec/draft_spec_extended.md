# LLM驱动的文字冒险游戏 - 扩展规范

> 本文档为 `draft_spec.md` 的扩展补充，包含并发控制、错误处理、持久化、DSL、配置管理等规范



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
