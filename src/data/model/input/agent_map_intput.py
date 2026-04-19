"""
Agent 输入视图类型
为不同 Agent 提供差异化世界信息视图

策略：预计算切片
- 在每个回合开始前预计算所有 Agent 的视图
- 保存在 turn_context 中供各 Agent 使用
"""

from datetime import datetime
from typing import List, Dict, Optional, Any, Union, Literal, TYPE_CHECKING
from pydantic import BaseModel, Field, ConfigDict, field_validator
from enum import Enum, Flag, auto
from ..base import DescriptionAddItem

# 避免循环导入，仅在类型检查时导入
if TYPE_CHECKING:
    from ..base import (
        Description,
        DescriptionAddItem,
        MapEntity,
        ItemEntity,
        CharacterEntity,
        MemoryForNpc,
        Goal,
        Attribute,
        Status,
    )


# ============================================================
# 描述视图（包含 public + hint + add）
# ============================================================

class DescriptionViewForAgent(BaseModel):
    """
    描述视图（用于 Agent/DM/Evolution）
    包含 public, hint 和 add，用于向 Agent 提供完整描述信息
    hint 用于提示 Agent，但不对玩家直接暴露
    """
    public: List[str] = Field(default_factory=list, description="公共描述，已稳定的默认可见信息")
    hint: str = Field(default="", description="只读提示信息，用于引导 Agent，不对玩家暴露")
    add: List[DescriptionAddItem] = Field(default_factory=list, description="暂存的变化描述，等待一致性维护后合并入 public")


class DescriptionViewForNpc(BaseModel):
    """
    描述视图（用于 NPC）
    包含 public 和 add，但不包含 hint（NPC 无法获得 hint 信息）
    """
    public: List[str] = Field(default_factory=list, description="公共描述，已稳定的默认可见信息")
    add: List[DescriptionAddItem] = Field(default_factory=list, description="暂存的变化描述，等待一致性维护后合并入 public")


# ============================================================
# 1. DM/Evolution View - 描述信息（public + add）
# ============================================================

class EntityDescriptionSummary(BaseModel):
    """实体描述摘要（用于 DM/Evolution，包含 hint）"""
    entity_id: str = Field(description="实体 ID")
    entity_name: str = Field(description="实体名称")
    description: "DescriptionViewForAgent" = Field(description="描述信息（含 hint）")


class BaseMapView(BaseModel):
    """地图基础字段，供各类 world view 复用。"""

    map_id: str = Field(description="地图 ID")
    map_name: str = Field(description="地图名称")


class DMWorldView(BaseMapView):
    """
    DM/Evolution 世界视图
    仅包含玩家所处地图的描述信息（public + add）
    """
    map_description: "DescriptionViewForAgent" = Field(description="地图描述")
    characters: Dict[str, "EntityDescriptionSummary"] = Field(default_factory=dict, description="角色 ID -> 描述摘要")
    items: Dict[str, "EntityDescriptionSummary"] = Field(default_factory=dict, description="物品 ID -> 描述摘要（仅限在地图上的物品）")


# ============================================================
# 2. StateAgent View - 可写字段（按实体分类）
# ============================================================

class WritableFieldInfo(BaseModel):
    """可写字段信息"""
    field_path: str = Field(description="字段路径，如  'description.add',status.healthy,status.san,inventory")
    field_name: str = Field(default="", description="字段展示名")
    current_value: Any = Field(description="当前值")
    value_type: str = Field(description="值类型，如 'number', 'string', 'boolean', 'list'")
    description: str = Field(description="字段用途说明")


class EntityWritableView(BaseModel):
    """
    实体可写字段视图
    按实体组织，清晰标注每个字段属于哪个实体
    """
    entity_id: str = Field(description="实体 ID")
    entity_type: str = Field(default="", description="实体类型，如 map/character/item")
    entity_name: str = Field(description="实体名称")
    description_summary: str = Field(default="", description="实体摘要描述")
    writable_fields: List["WritableFieldInfo"] = Field(default_factory=list, description="可写字段列表")


class StateAgentWorldView(BaseMapView):
    """
    StateAgent 世界视图
    包含互动对象所处地图的所有可写字段，按实体分类组织

    示例：当玩家说"脚趾被砸到了，减少生命值"时，
    Agent 可以通过 player_entity.writable_fields 中找到 healthy
    """
    entities: List["EntityWritableView"] = Field(default_factory=list, description="所有可写实体列表")


# ============================================================
# 3. NpcScheduler View - 地图切片
# ============================================================

class CharBrief(BaseModel):
    """角色简要信息（用于 NpcScheduler/NPC，无 hint）"""
    id: str = Field(description="角色 ID")
    name: str = Field(description="角色名称")
    basic_info: str = Field(default="", description="角色基本信息")
    description: "DescriptionViewForNpc" = Field(description="描述信息（public + add，无 hint）")


class ItemBrief(BaseModel):
    """物品简要信息（用于 NpcScheduler/NPC，无 hint）"""
    id: str = Field(description="物品 ID")
    name: str = Field(description="物品名称")
    description: "DescriptionViewForNpc" = Field(description="描述信息（public + add，无 hint）")


class ConnectionBrief(BaseModel):
    """连接简要信息"""
    id: str = Field(description="连接 ID")
    name: str = Field(description="连接名称")
    direction: str = Field(description="方向")
    target_map_id: Optional[str] = Field(default=None, description="目标地图 ID（未知时为 null）")
    is_locked: bool = Field(default=False, description="是否锁定")
    condition: Optional[str] = Field(default=None, description="连接条件表达式")


class MapSlice(BaseMapView):
    """
    地图切片（用于 NpcScheduler/narrative，无 hint）
    注意：不包含 NPC 的 memory/goal 等个人信息
    """
    description: "DescriptionViewForNpc" = Field(description="描述信息（public + add，无 hint）")
    connections: List["ConnectionBrief"] = Field(default_factory=list, description="连接列表")
    characters: List["CharBrief"] = Field(default_factory=list, description="角色列表（不含 memory/goal）")
    items: List["ItemBrief"] = Field(default_factory=list, description="物品列表（仅限在地图上的物品）")


class NpcSchedulerWorldView(BaseModel):
    """
    NpcScheduler 世界视图
    包含玩家所处地图 + 相邻地图的切片
    """
    current_map: "MapSlice" = Field(description="当前地图切片")
    adjacent_maps: List["MapSlice"] = Field(default_factory=list, description="相邻地图切片列表")
    player_location: str = Field(description="玩家当前位置")


# ============================================================
# 4. Npc View - NPC 切片（无个人信息）
# ============================================================

class NpcWorldView(BaseModel):
    """
    Npc 世界视图
    NPC 不完整信息（无 memory/goal 等个人信息，无 hint）
    个人信息和调度信息由 AgentMemory 和 NpcScheduler 分别提供
    """
    id: str = Field(default="", description="角色 ID")
    name: str = Field(default="", description="角色名称")
    basic_info: str = Field(default="", description="角色基本信息")
    location: str = Field(default="", description="角色当前位置")
    description: "DescriptionViewForNpc" = Field(description="描述信息（public + add，无 hint）")


# ============================================================
# 5. Narrative/Merger View - 地图切片（无角色）
# ============================================================

class NarrativeWorldView(BaseModel):
    """
    Narrative/Merger 世界视图
    地图切片（不含 NPC 详情）
    """
    map_slice: "MapSlice" = Field(description="地图切片")


# ============================================================
# 回合视图容器
# ============================================================

class TurnViews(BaseModel):
    """
    回合视图容器
    包含本回合所有 Agent 的预计算视图
    """
    turn_id: int = Field(description="当前回合数")
    dm_view: "DMWorldView" = Field(description="DM 世界视图")
    state_agent_view: "StateAgentWorldView" = Field(description="StateAgent 世界视图")
    npc_scheduler_view: "NpcSchedulerWorldView" = Field(description="NpcScheduler 世界视图")
    narrative_view: "NarrativeWorldView" = Field(description="Narrative 世界视图")


