# 双真值池 SQLite 结构定义

## WorldInfo

世界真值继续由 `state_change_agent` 驱动，可持久化为：

```sql
CREATE TABLE world_snapshots (
  version INTEGER PRIMARY KEY,
  snapshot_at TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
```

## NarrativeInfo

叙事真值由 `merger_agent` 驱动，建议独立存储：

```sql
CREATE TABLE narrative_recent (
  turn_id INTEGER PRIMARY KEY,
  content TEXT NOT NULL,
  source TEXT NOT NULL,
  committed_at TEXT NOT NULL
);

CREATE TABLE narrative_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  turn_id INTEGER NOT NULL,
  source TEXT NOT NULL,
  content TEXT NOT NULL,
  logged_at TEXT NOT NULL
);
```

## 说明

- `world_snapshots` 与 `narrative_recent/narrative_log` 必须分库或至少分表，不能混写。
- `NarrativeDraft` 只在 `visible_to_player = true` 的可见叙事链路中作为临时对象存在，不进入正式叙事池表。
- `narrative_recent` 保留最近 `agent.narrative.recent_turns` 回合，超出部分滚入 `narrative_log`。
- 写入 `narrative_recent` 的内容是 merger 压缩后的内部摘要，不是玩家看到的逐字叙事原文。
