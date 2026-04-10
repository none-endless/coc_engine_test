import builtins
from datetime import datetime
from typing import List, Dict, Optional, Any, Union, Literal
from pydantic import BaseModel, Field, ConfigDict, field_validator
from enum import Enum


# ============================================================
# 基础类型定义
# ============================================================

class DescriptionAddItem(BaseModel):
    """描述系统 add 数组中的单个条目"""
    turn: int = Field(default=0, description="回合数")
    content: str = Field(default="", description="内容")


class Description(BaseModel):
    """
    描述系统

    规则：
    1. public 仅允许系统层写入，是实体默认可见信息。
    2. hint 为只读字段，用于提示 Agent，但不对玩家直接暴露。
    3. add 用于暂存最近变化，由一致性流程按配置合并进入 public。
    """
    public: List[str] = Field(default_factory=list, description="实体默认可见信息，仅允许系统层写入")
    hint: str = Field(default="", description="只读字段，用于提示 Agent，但不对玩家直接暴露")
    add: List["DescriptionAddItem"] = Field(default_factory=list, description="暂存最近变化，由一致性流程按配置合并进入 public")


# ============================================================
# Memory 相关类型定义
# ============================================================

class MemoryLogItem(BaseModel):
    """记忆日志条目"""
    turn: int = Field(default=0, description="回合数")
    content: str = Field(default="", description="内容")
    timestamp: int = Field(default=0, description="时间戳")


class LongTermMemoryItem(BaseModel):
    """长期记忆条目"""
    vector_id: str = Field(default="", description="向量 ID")
    summary: str = Field(default="", description="摘要")
    turn_ref: str = Field(default="", description="关联回合引用")


class ShortLogItem(BaseModel):
    """短期日志条目"""
    turn: int = Field(default=0, description="回合数")
    event: str = Field(default="", description="事件")


class MemoryForNpc(BaseModel):
    """
    角色 NPC 所拥有的记忆

    说明：
    - log 仅用于调试与回溯，不直接进入 LLM 上下文。
    - long_term_memory 暂保留接口。
    - short、short_log、key_facts 由配置控制保留回合数与提炼策略。
    """
    log: List["MemoryLogItem"] = Field(default_factory=list, description="记忆日志，仅用于调试与回溯，不直接进入 LLM 上下文")
    long_term_memory: List["LongTermMemoryItem"] = Field(default_factory=list, description="长期记忆，暂保留接口")
    current_event: Optional[str] = Field(default=None, description="当前事件")
    short: List[str] = Field(default_factory=list, description="短期记忆摘要，由配置控制")
    short_log: List["ShortLogItem"] = Field(default_factory=list, description="短期日志，由配置控制")
    key_facts: List[str] = Field(default_factory=list, description="关键事实，由配置控制")


# ============================================================
# Goal 相关类型定义
# ============================================================

class Goal(BaseModel):
    """
    目标模型

    规则：
    1. base_goal 由初始设定提供。
    2. active_goal 可由 NPC 自主更新。
    3. goal_history 保留历史目标；对 NPC 上下文默认仅展示最近 3 项。
    """
    base_goal: str = Field(default="", description="基础目标，由初始设定提供")
    active_goal: str = Field(default="", description="当前活跃目标，可由 NPC 自主更新")
    goal_history: List[str] = Field(default_factory=list, description="历史目标，对 NPC 上下文默认仅展示最近 3 项")


# ============================================================
# 属性与状态类型定义
# ============================================================

class Attribute(BaseModel):
    """
    属性模型
    约定：CharacterEntity.attributes 采用以字段 ID 为 key 的映射结构，便于 DSL 通过 attributes.health.value 直接访问。
    """
    id: str = Field(default="", description="属性 ID")
    name: str = Field(default="", description="属性名称")
    value: int = Field(default=0, description="当前值")
    max_value: int = Field(default=100, description="最大值")
    min_value: int = Field(default=0, description="最小值")
    description: str = Field(default="", description="属性描述")


class Status(BaseModel):
    """
    状态模型
    约定：CharacterEntity.status 采用以字段 ID 为 key 的映射结构，便于 DSL 通过 status.sanity.value 直接访问。
    """
    id: str = Field(default="", description="状态 ID")
    name: str = Field(default="", description="状态名称")
    value: int = Field(default=0, description="当前值")
    max_value: int = Field(default=100, description="最大值")
    min_value: int = Field(default=0, description="最小值")
    description: str = Field(default="", description="状态描述")


# ============================================================
# 地图实体类型定义
# ============================================================

class MapParent(BaseModel):
    """地图父节点"""
    id: str = Field(default="", description="父节点 ID")
    name: str = Field(default="", description="父节点名称")


class MapChild(BaseModel):
    """地图子节点"""
    id: str = Field(default="", description="子节点 ID")
    name: str = Field(default="", description="子节点名称")


class MapConnection(BaseModel):
    """地图连接"""
    id: str = Field(default="", description="连接 ID")
    name: str = Field(default="", description="连接名称")
    direction: str = Field(default="", description="方向")
    description: str = Field(default="", description="连接描述")
    is_locked: bool = Field(default=False, description="是否锁定")
    condition: Optional[str] = Field(default=None, description="解锁条件")


class MapEntity(BaseModel):
    """
    地图实体

    说明：
    - char_index / item_index 为系统派生索引，不是位置真值。
    - 地图可通过 extensions 扩展谜题、机关、标签等字段。
    - 采用命名空间，如 quest.stage、combat.tags。
    """
    id: str = Field(default="", description="地图 ID")
    name: str = Field(default="", description="地图名称")
    description: "Description" = Field(default_factory=Description, description="地图描述")
    parent: Optional["MapParent"] = Field(default=None, description="父节点")
    children: List["MapChild"] = Field(default_factory=list, description="子节点列表")
    connections: List["MapConnection"] = Field(default_factory=list, description="连接列表")
    char_index: List[str] = Field(default_factory=list, description="角色索引，系统派生")
    item_index: List[str] = Field(default_factory=list, description="物品索引，系统派生")
    extensions: Dict[str, Any] = Field(default_factory=dict, description="扩展字段，采用命名空间如 quest.stage")


# ============================================================
# 物品实体类型定义
# ============================================================

class ItemEntity(BaseModel):
    """
    物品实体
    - 采用命名空间扩展字段。
    """
    id: str = Field(default="", description="物品 ID")
    name: str = Field(default="", description="物品名称")
    description: "Description" = Field(default_factory=Description, description="物品描述")
    location: str = Field(default="", description="当前位置")
    is_portable: bool = Field(default=True, description="是否可携带")
    extensions: Dict[str, Any] = Field(default_factory=dict, description="扩展字段")


# ============================================================
# 角色实体类型定义
# ============================================================

class CharacterEntity(BaseModel):
    """
    角色实体

    约定：attributes 与 status 采用以字段 ID 为 key 的映射结构，便于 DSL 通过 attributes.health.value、status.sanity.value 直接访问。
    - Agent 只能写被标记为 mutable 的扩展字段。
    """
    id: str = Field(default="", description="角色 ID")
    name: str = Field(default="", description="角色名称")
    basic_info: str = Field(default="", description="基本信息")
    description: "Description" = Field(default_factory=Description, description="角色描述")
    location: str = Field(default="", description="当前位置")
    status: Dict[str, "Status"] = Field(default_factory=dict, description="状态映射，key 为状态 ID")
    attributes: Dict[str, "Attribute"] = Field(default_factory=dict, description="属性映射，key 为属性 ID")
    inventory: Dict[str, "ItemEntity"] = Field(default_factory=dict, description="拥有的物品,key为id")
    memory: "MemoryForNpc" = Field(default_factory=MemoryForNpc, description="角色记忆")
    goal: "Goal" = Field(default_factory=Goal, description="角色目标")
    extensions: Dict[str, Any] = Field(default_factory=dict, description="扩展字段，采用命名空间，必须在 schema registry 中声明类型、默认值、是否可写")

