# 世界信息差异化供给设计方案

## 策略选择：预计算切片

在每个回合开始前预计算所有 Agent 的视图，优点是实时性好。

## 文件结构

```
src/data/
├── model/
│   ├── base.py           # 实体类型定义（MapEntity, ItemEntity, CharacterEntity 等）
│   ├── agent_input.py    # Agent 输入视图类型定义
│   └── world_provider.py # 世界数据供给器
```

## Agent 视图类型设计

### 1. DM/Evolution View - 描述信息仅
- 地图信息：仅 Description.public+add
- 角色信息：仅 Description.public+add
- 物品信息：仅 Description.public+add

### 2. StateAgent View - 可写字段
- 完整 MapEntity
- 标注哪些字段可写
- extensions 中的可写命名空间

### 3. NpcScheduler View - 地图切片
- 当前地图切片
- 相邻地图切片（MapSlice）
- 包含 connections, characters, items 简要信息

### 4. Npc View - NPC 不完整信息
- 去除敏感信息
- 只包含 public+add description

### 5. Narrative/Merger View - 地图切片（无角色详情）
- MapSlice 格式
- 只包含地图和物品，不包含角色详情

## WorldDataProvider 接口

```python
class WorldDataProvider:
    def precompute_all_views(self, current_map_id: str, turn: int) -> TurnViews:
        """预计算所有 Agent 视图"""
        
    def get_dm_view(self, map_id: str) -> DMWorldView
    def get_state_agent_view(self, map_id: str) -> StateAgentWorldView
    def get_npc_scheduler_view(self, map_id: str) -> NpcSchedulerWorldView
    def get_npc_view(self, npc_id: str) -> NpcWorldView
```

## 数据流

1. 回合开始
2. 调用 precompute_all_views()
3. 构建 TurnViews 容器
4. 各 Agent 从 TurnViews 获取自己的视图
