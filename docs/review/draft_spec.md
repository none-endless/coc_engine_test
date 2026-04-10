# LLM驱动的文字冒险游戏 - 主规范

> 本文档定义系统主链路、核心对象、世界模型与 Agent 职责。实现细节、事务、持久化与配置规范见 `draft_spec_extended.md`。

---

## 1. 目标与边界

本项目是一套面向文字冒险游戏的多 Agent 叙事引擎，目标是：

1. 同时支持玩家输入与 NPC 自主行为输入。
2. 支持元命令分支与自然语言分支的统一调度。
3. 采用“规则结算 + LLM 生成”的双驱动架构。
4. 将“世界真值”和“叙事真值”分离维护，避免叙事污染客观状态。
5. 提供可回滚、可重试、可持久化的状态更新机制。
6. 保持实现可落地，避免过度工程化。

非目标：

- 暂不要求向量数据库落地实现，仅保留长期记忆扩展接口。
- 暂不支持多人在线并发写世界状态。
- 暂不支持跨存档的复杂分支合并。

---

## 2. 核心设计原则

1. **`location` 是位置真值**：人物与物品的位置只以 `location` 为准；地图上的 `char_index` / `item_index` 为系统派生索引，不允许 Agent 直接写入。
2. **链路对象必须结构化**：Agent 之间传递结构化对象，不直接把自由文本当作系统真值。
3. **每回合必须事务化**：每次输入都包裹为一个回合事务，携带 `turn_id`、`world_version`、`event_id` 等元数据。
4. **叙事先草稿、后提交**：`narrative_agent` 可以并发生成 `NarrativeDraft`，但只有当状态提交成功后，草稿才可进入叙事真值池。
5. **真值池分层维护**：世界状态、叙事状态、链路日志分别维护，不允许混写。
6. **扩展字段必须受约束**：所有 `extensions` 字段都必须挂载在命名空间下，并由 schema registry 约束读写权限。

---

## 3. 实体 ID 系统规范

### 3.1 固定命名格式

```text
[实体类型]-[有意义名称]-[唯一后缀]
```

### 3.2 字段定义与约束

| 字段 | 约束 | 说明 | 示例 |
|------|------|------|------|
| 实体类型 | 固定枚举：`map` / `char` / `item` | 严格区分实体类型 | `map-bedroom-0000` |
| 有意义名称 | 小写英文，使用下划线分隔 | 要求见名知意 | `char-town_guard-0003` |
| 唯一后缀 | 4 位递增数字，全局唯一 | 不允许复用 | `item-room_key-0008` |

### 3.3 补充规则

1. 玩家实体固定 ID：`char-player-0000`。
2. 所有实体 ID 必须在创建时通过全局唯一性校验。
3. 实体删除后 ID 归档，不得复用。
4. 所有模块必须通过实体 ID 索引实体，禁止通过名称或描述做真值索引。

---

## 4. 系统运行流程

![系统流程图](image.png)

### 4.1 回合包裹对象

每次输入都先被封装为 `TurnEnvelope`：

```ts
TurnEnvelope {
  turn_id: string;
  source: "player" | "npc";
  actor_id: string;
  input_text: string;
  input_type: "meta_command" | "natural_language";
  timestamp: number;
  world_version: number;
}
```

### 4.2 输入分流

1. `input_system` 接收玩家输入或 NPC 行为输入。
2. 若输入为底层元命令（如 `\look`），直接进入 `rule_system`，跳过 LLM 主链路。
3. 若输入为自然语言，则进入 `dm_agent`，再进入规则与演化链路。

### 4.3 自然语言主链路

自然语言输入按如下顺序处理：

1. `dm_agent` 解析输入，生成 `Intent`。
2. `rule_system` 基于 `Intent` 与世界快照做规则判定，生成 `RuleFacts`。
3. `evolution_agent` 基于 `RuleFacts` 生成 `EvolutionResult`。
4. `evolution_agent` 并发触发两个任务：
   - 生成 `ShortSummary`
   - 生成 `NarrativeDraft`
5. `ShortSummary` 生成后，再并发触发两个任务：
   - 发送给 `npc_scheduler_agent` 生成 `NpcPlan`
   - 发送给 `state_change_agent` 生成 `StatePatch`
6. `state_change_agent` 提交 `StatePatch`：
   - 成功：更新世界真值池，并允许 `merger_agent` 将 `NarrativeDraft` 合并为正式叙事记录
   - 失败：进入重试 / 回滚 / 降级流程，并丢弃本回合 `NarrativeDraft`
7. 提交成功后，NPC 队列解除阻塞，下一轮自主行为才允许继续执行。

### 4.4 元命令分支

元命令分支由 `rule_system` 直接处理：

- 可读元命令：如 `\look`、`\inventory`
- 可写元命令：如 `\take`、`\use`，若涉及世界状态变更，仍必须转为标准 `StatePatch` 后提交
- 元命令不得绕过事务与版本校验

### 4.5 叙事提交语义

为同时保留并发能力与一致性，采用以下规则：

1. `narrative_agent` 输出的是 `NarrativeDraft`，属于**暂存叙事**。
2. 只有 `state_change_agent` 成功提交后，`merger_agent` 才可将其写入 `NarrativeState.recent`。
3. 若状态提交失败并回滚，则本回合叙事草稿直接丢弃，只返回降级提示。
4. 因此，玩家可看到流式草稿，但系统只认提交后的正式叙事为真值。

---

## 5. 链路对象模型

### 5.1 标准对象定义

| 对象名 | 生产者 | 消费者 | 说明 |
|--------|--------|--------|------|
| `RawInput` | `input_system` | `dm_agent` / `rule_system` | 原始输入事件 |
| `Intent` | `dm_agent` | `rule_system` | 结构化意图、目标、槽位 |
| `RuleFacts` | `rule_system` | `evolution_agent` | 客观规则判定结果 |
| `EvolutionResult` | `evolution_agent` | `short_summary_agent` / `narrative_agent` | 回合推演结果 |
| `ShortSummary` | `short_summary_agent` | `state_change_agent` / `npc_scheduler_agent` | 本回合的短摘要和关键状态变化 |
| `NarrativeDraft` | `narrative_agent` | `merger_agent` | 未提交的叙事草稿 |
| `StatePatch` | `state_change_agent` | persistence layer | 世界状态补丁 |
| `NpcPlan` | `npc_scheduler_agent` | `npc_agent` | NPC 调度决策 |
| `MetaResult` | `rule_system` | output layer | 元命令结果 |

### 5.2 每个链路对象必须包含的公共字段

```ts
BaseEvent {
  event_id: string;
  turn_id: string;
  producer: string;
  created_at: number;
  world_version: number;
}
```

说明：

- `event_id`：链路事件唯一标识。
- `turn_id`：归属回合。
- `world_version`：该对象基于哪个世界版本产生。
- 所有对象均需可追溯到上游事件，便于回放与调试。

---

## 6. 世界模型

### 6.1 描述系统 `Description`

```ts
Description {
  public: string[];
  hint: string;
  add: Array<{
    turn: number;
    content: string;
  }>;
}
```

规则：

1. `public` 仅允许系统层写入，是实体默认可见信息。
2. `hint` 为只读字段，用于提示 Agent，但不对玩家直接暴露。
3. `add` 用于暂存最近变化，由一致性流程按配置合并进入 `public`。

### 6.2 记忆系统 `Memory`（角色专有）

```ts
Memory {
  log: Array<{
    turn: number;
    content: string;
    timestamp: number;
  }>;
  long_term_memory: Array<{
    vector_id: string;
    summary: string;
    turn_ref: string;
  }>;
  current_event: string | null;
  short: string[];
  short_log: Array<{
    turn: number;
    event: string;
  }>;
  key_facts: string[];
}
```

说明：

- `log` 仅用于调试与回溯，不直接进入 LLM 上下文。
- `long_term_memory` 暂保留接口。
- `short`、`short_log`、`key_facts` 由配置控制保留回合数与提炼策略。

### 6.3 目标模型 `Goal`

```ts
Goal {
  base_goal: string;
  active_goal: string;
  goal_history: string[];
}
```

规则：

1. `base_goal` 由初始设定提供。
2. `active_goal` 可由 NPC 自主更新。
3. `goal_history` 保留历史目标；对 NPC 上下文默认仅展示最近 3 项。

### 6.4 地图实体 `MapEntity`

```ts
MapEntity {
  id: string;
  name: string;
  description: Description;
  parent: { id: string; name: string } | null;
  children: Array<{ id: string; name: string }>;
  connections: Array<{
    id: string;
    name: string;
    direction: string;
    description: string;
    is_locked: boolean;
    condition: string | null;
  }>;
  char_index: string[];
  item_index: string[];
  extensions: Record<string, unknown>;
}
```

说明：

- `char_index` / `item_index` 为系统派生索引，不是位置真值。
- 地图可通过 `extensions` 扩展谜题、机关、标签等字段。

### 6.5 物品实体 `ItemEntity`

```ts
ItemEntity {
  id: string;
  name: string;
  description: Description;
  location: string;
  is_portable: boolean;
  extensions: Record<string, unknown>;
}
```

### 6.6 角色实体 `CharacterEntity`

```ts
CharacterEntity {
  id: string;
  name: string;
  basic_info: string;
  description: Description;
  location: string;
  status: Record<string, Status>;
  attributes: Record<string, Attribute>;
  memory: Memory;
  goal: Goal;
  extensions: Record<string, unknown>;
}
```

### 6.7 属性与状态

```ts
Attribute {
  id: string;
  name: string;
  value: number;
  max_value: number;
  min_value: number;
  description: string;
}

Status {
  id: string;
  name: string;
  value: number;
  max_value: number;
  min_value: number;
  description: string;
}
```

约定：`CharacterEntity.attributes` 与 `CharacterEntity.status` 采用以字段 ID 为 key 的映射结构，便于 DSL 通过 `attributes.health.value`、`status.sanity.value` 直接访问。

### 6.8 扩展字段规范

所有实体的扩展字段统一放在 `extensions` 下，并遵守：

1. 采用命名空间，如 `quest.stage`、`combat.tags`。
2. 必须在 schema registry 中声明类型、默认值、是否可写。
3. Agent 只能写被标记为 `mutable` 的扩展字段。

---

## 7. 状态池与上下文分层

### 7.1 世界真值池 `WorldState`

包含：

- 所有实体对象
- 当前 `world_version`
- 派生索引
- 最近一次提交的 `turn_id`

### 7.2 叙事真值池 `NarrativeState`

包含：

- `recent`：最近若干回合的正式叙事摘要
- `archive`：历史叙事归档
- `drafts`：未提交叙事草稿暂存区

### 7.3 链路日志池 `TurnLedger`

包含：

- 各类链路对象
- 回合因果链
- 时间戳与耗时
- 错误与重试记录

### 7.4 视图分层

| 消费方 | 可读上下文 |
|--------|------------|
| `dm_agent` | 世界完整视图 + 最近叙事 |
| `evolution_agent` | 世界完整视图 + `RuleFacts` |
| `state_change_agent` | 世界完整视图 + `ShortSummary` |
| `npc_scheduler_agent` | 世界切片 + `ShortSummary` |
| `narrative_agent` | 世界切片 + `EvolutionResult` |
| `npc_agent` | 自身切片 + 调度结果 + 局部世界切片 |

---

## 8. Agent 职责划分

| Agent | 输入 | 输出 | 职责 |
|------|------|------|------|
| `input_system` | 玩家/NPC 输入 | `RawInput` | 分流与回合封装 |
| `dm_agent` | `RawInput` | `Intent` | 意图解析与语义补全 |
| `rule_system` | `RawInput` / `Intent` | `MetaResult` / `RuleFacts` | 规则判定与合法性校验 |
| `evolution_agent` | `RuleFacts` | `EvolutionResult` | 世界演化推演 |
| `short_summary_agent` | `EvolutionResult` | `ShortSummary` | 回合摘要与关键状态提炼 |
| `narrative_agent` | `EvolutionResult` | `NarrativeDraft` | 叙事草稿生成 |
| `state_change_agent` | `ShortSummary` | `StatePatch` | 世界补丁生成与提交 |
| `merger_agent` | `NarrativeDraft` + commit result | 叙事真值记录 | 正式叙事合并 |
| `npc_scheduler_agent` | `ShortSummary` | `NpcPlan` | NPC 调度 |
| `npc_agent` | `NpcPlan` + 局部世界 | NPC 输入事件 | 执行动作并回注输入 |
| `narrative_consistency_agent` | 世界/叙事真值池 | 校验结果 | 一致性校验与描述合并 |

---

## 9. NPC 行为闭环

1. `npc_scheduler_agent` 只负责生成调度计划，不直接修改世界真值。
2. `npc_agent` 必须在上一个 `StatePatch` 提交成功后才允许继续生成下一条行为输入。
3. NPC 的目标、记忆与短期事件更新必须受预算、冷却和回合深度限制，详细规则见扩展规范。
4. NPC 闭环必须可中断：当回合失败、回滚或预算耗尽时，剩余计划自动延后。

---

## 10. 条件系统（DSL）边界

1. 条件 DSL 当前主要用于：
   - 结局判定
   - 可见性/可达性判定
   - `StatePatch` 中的前置断言
2. 条件表达式统一采用：

```text
[实体ID].[字段路径] [操作符] [值]
```

3. 复合条件支持 `and`、`or`、`not`。
4. 条件 DSL 的详细语法、校验方式与执行时机见扩展规范。

---

## 11. V1 落地约束

V1 必须满足：

1. 单线程运行 + `asyncio` 异步并发。
2. 单存档、单世界真值池。
3. 持久化使用 SQLite。
4. 所有世界写入都必须通过标准 `StatePatch`。
5. 所有正式叙事都必须晚于状态提交。

V1 暂不做：

- 向量记忆检索落地
- 跨会话协同编辑
- 多玩家同时在线
- 高级战斗子系统
