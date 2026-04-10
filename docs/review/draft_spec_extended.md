# LLM驱动的文字冒险游戏 - 扩展规范

> 本文档为 `draft_spec.md` 的实现补充，定义事务、并发、状态写入 DSL、NPC 调度预算、持久化与配置规范。

---

## 1. 回合事务与并发控制

### 1.1 回合事务字段

每次输入必须绑定以下事务字段：

```ts
TurnContext {
  turn_id: string;
  root_event_id: string;
  actor_id: string;
  source: "player" | "npc";
  expected_version: number;
  current_version: number;
  status: "running" | "committed" | "rolled_back" | "failed";
  started_at: number;
  completed_at: number | null;
}
```

字段约束：

- `turn_id`：单回合唯一标识。
- `root_event_id`：本回合入口事件 ID。
- `expected_version`：本回合开始时读取到的世界版本。
- `current_version`：提交成功后的新版本；提交前应等于 `expected_version`。
- `status`：用于回放、监控和恢复。

### 1.2 并发范围

V1 只允许以下一个并发区：

1. `ShortSummary` 完成后，并发生成：
   - `NpcPlan`
   - `StatePatch`
   - `NarrativeDraft`

除此之外：

- 世界真值写入必须串行。
- 一个时刻仅允许一个 `StatePatch` 进入提交临界区。
- `npc_agent` 必须等待当前回合的状态提交完成后再继续动作。

### 1.3 锁与快照策略

1. 读取世界状态时使用只读快照，不阻塞其他读取。
2. 提交 `StatePatch` 时使用单个 `asyncio.Lock` 保护提交临界区。
3. 快照必须记录 `world_version`，保证重试与回滚基于同一版本。
4. 所有并发分支都必须带上自己的 `turn_id` 与 `world_version`，禁止消费无版本信息的对象。

### 1.4 幂等与版本规则

为避免重试导致重复提交，`StatePatch` 必须带以下字段：

```ts
PatchMeta {
  patch_id: string;
  turn_id: string;
  expected_version: number;
  retry_seq: number;
}
```

规则：

1. 同一个 `patch_id` 只能提交一次。
2. 若 `expected_version` 与当前世界版本不一致，则拒绝提交，返回 `VERSION_CONFLICT`。
3. 重试时允许复用 `turn_id`，但必须更新 `patch_id` 或显式标记为同一 patch 的重放。
4. 持久化层必须记录 patch 提交结果，用于去重与回放。

---

## 2. 状态写入 DSL 规范

### 2.1 可写字段边界

`state_change_agent` 只允许生成作用于 `WorldState` 的补丁，不直接写叙事池和派生索引。

| 字段类别 | 是否允许直接写入 | 说明 |
|----------|------------------|------|
| `location` | 是 | 位置唯一真值 |
| `attributes.*` / `status.*` | 是 | 数值或枚举状态 |
| `description.add` | 是 | 描述增量缓冲 |
| `connections[*].is_locked` | 是 | 地图连接锁状态 |
| schema registry 中声明为 `mutable` 的 `extensions.*` | 是 | 扩展字段 |
| `description.public` | 否 | 由系统合并流程维护 |
| `char_index` / `item_index` | 否 | 由 `location` 自动派生 |
| `memory.log` / `narrative_state.*` | 否 | 分属其他系统 |

### 2.2 基础操作符

V1 支持以下操作符：

| 操作符 | 用途 | 适用类型 |
|--------|------|----------|
| `ADD` | 向列表字段追加元素 | list |
| `REMOVE` | 从列表字段移除元素 | list |
| `SET` | 直接设置字段值 | string / bool / enum / object |
| `UPDATE` | 更新数值字段 | number |
| `MOVE` | 变更 `location` | 唯一真值字段 |
| `ASSERT` | 提交前断言 | 条件表达式 |

### 2.3 语法

```text
ASSERT [条件表达式]
ADD [实体ID].[字段路径] = [值1, 值2, ...]
REMOVE [实体ID].[字段路径] = [值1, 值2, ...]
SET [实体ID].[字段路径] = [新值]
UPDATE [实体ID].[字段路径] = [新值]
MOVE [实体ID].location = [目标实体ID]
```

### 2.4 示例

```text
ASSERT map-cellar-0001.connections[0].is_locked == false
UPDATE char-player-0000.attributes.health.value = 80
MOVE item-room_key-0008.location = char-player-0000
ADD map-cellar-0001.description.add = [{turn: 12, content: "地板上多了被拖拽的痕迹"}]
REMOVE char-bandit-0002.extensions.combat.tags = ["hidden"]
SET map-cellar-0001.connections[0].is_locked = true
```

### 2.5 执行顺序

单个 `StatePatch` 的执行顺序固定为：

1. 解析补丁
2. 执行全部 `ASSERT`
3. 执行 `MOVE`
4. 执行 `SET` / `UPDATE`
5. 执行 `ADD` / `REMOVE`
6. 重新计算派生索引
7. 持久化提交

### 2.6 校验流程

持久化层收到 `StatePatch` 后，按顺序校验：

1. `patch_id` 是否已提交过
2. `turn_id` 是否有效
3. `expected_version` 是否与当前世界版本一致
4. 实体 ID 是否存在
5. 字段路径是否存在且可写
6. 字段类型是否匹配
7. 数值是否越界
8. `location` 目标是否有效
9. `ASSERT` 是否成立

若任一检查失败，返回错误并拒绝提交。

### 2.7 错误类型定义

| 错误类型 | 说明 |
|---------|------|
| `ENTITY_NOT_FOUND` | 实体不存在 |
| `FIELD_NOT_FOUND` | 字段不存在 |
| `FIELD_NOT_MUTABLE` | 字段不可写 |
| `FIELD_TYPE_MISMATCH` | 字段类型不匹配 |
| `VALUE_OUT_OF_RANGE` | 数值超界 |
| `DUPLICATE_ENTRY` | 列表追加重复元素 |
| `ENTRY_NOT_FOUND` | 列表删除目标不存在 |
| `INVALID_TARGET` | `MOVE` 目标无效 |
| `ASSERT_FAILED` | 前置断言失败 |
| `VERSION_CONFLICT` | 版本冲突 |
| `PATCH_ALREADY_APPLIED` | Patch 已提交 |
| `TIMEOUT` | 提交超时 |

### 2.8 重试、降级与回滚

1. 单次提交失败时，允许自动重试。
2. 单次重试超时后，标记本次尝试失败。
3. 超过最大重试次数、发生超时或出现不可恢复错误时，进入回滚流程。
4. 回滚必须恢复到本回合开始前的快照版本。
5. 回滚完成后：
   - 丢弃 `NarrativeDraft`
   - 不写入正式叙事池
   - 输出系统降级提示
   - 终止当前交互，等待玩家重新输入
6. 错误日志需记录：`turn_id`、`patch_id`、错误类型、错误消息、重试次数、时间戳。

---

## 3. NPC 调度预算规范

### 3.1 目标

NPC 闭环必须可控，避免“NPC 触发 NPC”导致单回合无限扩散。

### 3.2 核心预算项

| 配置项 | 默认值 | 说明 |
|-------|-------|------|
| `npc.max_actions_per_turn` | 3 | 单回合最多执行的 NPC 动作数 |
| `npc.max_actions_per_actor` | 1 | 单回合单个 NPC 最多执行次数 |
| `npc.cooldown_turns` | 1 | NPC 连续动作的冷却回合数 |
| `npc.max_cascade_depth` | 2 | NPC 触发 NPC 的最大链式深度 |
| `npc.queue_max_length` | 32 | 调度队列最大长度 |

### 3.3 调度规则

1. `npc_scheduler_agent` 只产生 `NpcPlan`，不直接写世界状态。
2. 每个 `NpcPlan` 必须带上：`turn_id`、`actor_id`、`priority`、`reason`、`cascade_depth`。
3. 若队列超预算，低优先级计划延后到下一回合。
4. 同一 NPC 在冷却期内不得再次入队，除非配置明确允许打断。
5. 若 `cascade_depth` 超限，则直接截断，不再继续扩散。
6. 回合提交失败时，本回合未执行的 `NpcPlan` 不得自动提升优先级，只能重新调度。

---

## 4. 持久化策略

### 4.1 数据库选型

- V1 使用 SQLite。
- 目标是本地单机存档、可回滚、可追溯。

### 4.2 设计原则

1. 核心检索字段结构化存储。
2. 可变扩展部分继续使用 JSON。
3. 每次提交都必须保留版本信息与提交日志。
4. 快照用于回滚与调试，不替代实体表。

### 4.3 建议表结构

```sql
CREATE TABLE entities (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    location TEXT,
    version INTEGER NOT NULL,
    data TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE patch_commits (
    patch_id TEXT PRIMARY KEY,
    turn_id TEXT NOT NULL,
    expected_version INTEGER NOT NULL,
    result_version INTEGER,
    status TEXT NOT NULL,
    error_code TEXT,
    payload TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE TABLE turn_events (
    event_id TEXT PRIMARY KEY,
    turn_id TEXT NOT NULL,
    producer TEXT NOT NULL,
    event_type TEXT NOT NULL,
    world_version INTEGER NOT NULL,
    payload TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE TABLE snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    turn_id TEXT NOT NULL,
    world_version INTEGER NOT NULL,
    data TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE TABLE error_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    turn_id TEXT NOT NULL,
    patch_id TEXT,
    error_type TEXT NOT NULL,
    error_msg TEXT,
    retry_count INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);
```

### 4.4 建议索引

```sql
CREATE INDEX idx_entities_location ON entities(location);
CREATE INDEX idx_patch_commits_turn_id ON patch_commits(turn_id);
CREATE INDEX idx_turn_events_turn_id ON turn_events(turn_id);
CREATE INDEX idx_snapshots_turn_version ON snapshots(turn_id, world_version);
CREATE INDEX idx_error_logs_turn_id ON error_logs(turn_id);
```

### 4.5 持久化时机

1. 回合开始前：保存世界快照。
2. `StatePatch` 提交成功后：写入实体表与 `patch_commits`。
3. 链路对象生成后：写入 `turn_events`。
4. 回滚触发时：读取最近快照并恢复实体表。
5. 手动存档：可选功能，作为快照的高层封装。

---

## 5. 条件 DSL 规范

### 5.1 用途

条件 DSL 当前用于：

1. 结局判定
2. 地图连接或交互条件判定
3. `ASSERT` 前置断言

### 5.2 语法

```text
[实体ID].[字段路径] [操作符] [值]
```

### 5.3 支持操作符

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

### 5.4 复合条件

支持使用 `and`、`or`、`not` 组合多个条件。

### 5.5 示例

```text
char-player-0000.attributes.health.value > 0 and item-room_key-0008.location in [char-player-0000, map-cellar-0001]
```

### 5.6 执行约束

1. 条件求值必须基于同一版本的世界快照。
2. `ASSERT` 与结局判定共享同一解析器。
3. 不允许在条件 DSL 中执行写操作。
4. 不允许引用未注册字段路径。

---

## 6. 配置管理规范

### 6.1 配置来源优先级

命令行参数 > 环境变量 > 配置文件 > 默认值

### 6.2 配置文件格式（YAML）

```yaml
llm:
  provider: "openai"
  model: "gpt-4.1"
  temperature: 0.7
  max_tokens: 2000
  timeout_sec: 30
  api_base: "https://api.openai.com/v1"

system:
  max_retry_count: 3
  retry_timeout_ms: 5000
  fallback_error: "系统繁忙，请稍后重试"
  snapshot_interval_turns: 1
  provisional_narration: true

npc:
  max_actions_per_turn: 3
  max_actions_per_actor: 1
  cooldown_turns: 1
  max_cascade_depth: 2
  queue_max_length: 32

agent:
  dm:
    memory_turns: 5
  npc:
    short_memory_turns: 15
    short_log_turns: 30
  narrative:
    recent_turns: 5

description:
  add_merge_interval_turns: 10
```

### 6.3 魔法数字配置项

| 配置项 | 默认值 | 说明 |
|-------|-------|------|
| `system.max_retry_count` | 3 | 状态提交最大重试次数 |
| `system.retry_timeout_ms` | 5000 | 单次提交超时毫秒 |
| `system.snapshot_interval_turns` | 1 | 快照频率 |
| `npc.max_actions_per_turn` | 3 | 单回合 NPC 总动作上限 |
| `npc.cooldown_turns` | 1 | NPC 冷却回合数 |
| `agent.dm.memory_turns` | 5 | DM 对话记忆保留回合数 |
| `agent.npc.short_memory_turns` | 15 | NPC 短期记忆保留回合数 |
| `agent.npc.short_log_turns` | 30 | NPC 短期日志保留回合数 |
| `agent.narrative.recent_turns` | 5 | 叙事 recent 保留回合数 |
| `description.add_merge_interval_turns` | 10 | 描述增量合并周期 |

### 6.4 配置 Schema 建议

```yaml
# config.schema.yaml

type: object
properties:
  llm:
    type: object
    properties:
      provider:
        type: string
        enum: ["openai", "anthropic", "custom"]
      model:
        type: string
      temperature:
        type: number
        minimum: 0
        maximum: 2
      max_tokens:
        type: integer
        minimum: 100
        maximum: 8000
  system:
    type: object
    properties:
      max_retry_count:
        type: integer
        minimum: 1
        maximum: 10
      retry_timeout_ms:
        type: integer
        minimum: 1000
        maximum: 30000
      provisional_narration:
        type: boolean
  npc:
    type: object
    properties:
      max_actions_per_turn:
        type: integer
        minimum: 1
        maximum: 20
      cooldown_turns:
        type: integer
        minimum: 0
        maximum: 20
```

---

## 7. V1 一致性检查清单

V1 必须满足以下检查：

1. 所有正式叙事都能追溯到成功提交的 `StatePatch`。
2. 任意时刻世界状态都只有一个最新 `world_version`。
3. 派生索引必须能从 `location` 全量重建。
4. 任意一次回滚都能恢复到完整快照。
5. 任意一次重试都不会重复提交同一 patch。
6. 任意一次 NPC 调度都受预算和冷却约束。
