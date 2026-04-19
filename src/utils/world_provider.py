"""
世界数据供给器
采用预计算切片策略
"""

from typing import List, Dict, Optional, Any, TYPE_CHECKING
from ..data.model.input.agent_map_intput import (
    DescriptionViewForAgent,
    DescriptionViewForNpc,
    DMWorldView,
    StateAgentWorldView,
    NpcSchedulerWorldView,
    NpcWorldView,
    NarrativeWorldView,
    TurnViews,
    MapSlice,
    CharBrief,
    ItemBrief,
    ConnectionBrief,
    EntityDescriptionSummary,
    EntityWritableView,
    WritableFieldInfo,
)

# 避免循环导入，仅在类型检查时导入
if TYPE_CHECKING:
    from ..data.model.base import (
        MapEntity,
        ItemEntity,
        CharacterEntity,
        Description,
        DescriptionAddItem,
    )


class WorldDataProvider:
    """
    世界数据供给器

    策略：预计算切片
    - 在每个回合开始前预计算所有 Agent 的视图
    - 保存在 turn_context 中供各 Agent 使用
    """

    def __init__(self, world_state: "WorldStateInterface"):
        """
        初始化数据供给器

        Args:
            world_state: 世界状态存储，需要实现 WorldStateInterface 接口
        """
        self.world_state = world_state

    def precompute_all_views(self, current_map_id: str, turn: int) -> TurnViews:
        """
        预计算所有 Agent 视图
        在回合开始时调用

        Args:
            current_map_id: 玩家当前所在地图 ID
            turn: 当前回合数

        Returns:
            TurnViews: 包含所有 Agent 视图的容器
        """
        return TurnViews(
            turn_id=turn,
            dm_view=self._get_dm_view(current_map_id),
            state_agent_view=self._get_state_agent_view(current_map_id),
            npc_scheduler_view=self._get_npc_scheduler_view(current_map_id),
            narrative_view=self._get_narrative_view(current_map_id),
        )

    def _get_visible_entities_at_map(self, map_id: str):
        """
        获取当前地图可见的角色和物品。

        规则：
        - 角色 location 必须等于 map_id。
        - 物品 location 必须等于 map_id（在角色身上的物品不可见）。
        """
        characters = [
            char for char in self.world_state.get_characters_at(map_id)
            if char.location == map_id
        ]
        items = [
            item for item in self.world_state.get_items_at(map_id)
            if item.location == map_id
        ]
        return characters, items

    def _resolve_connection_target_map_id(self, current_map_id: str, conn: Any) -> Optional[str]:
        """
        解析连接的目标地图 ID。

        说明：
        - 不再将 condition 映射为 target_map_id。
        - 优先使用连接对象上显式 target_map_id 字段。
        - 若不存在，则仅在 conn.id 属于相邻地图 ID 时使用 conn.id。
        """
        explicit_target = getattr(conn, "target_map_id", None)
        if explicit_target:
            return explicit_target

        adjacent_ids = set(self.world_state.get_adjacent_map_ids(current_map_id))
        if conn.id in adjacent_ids:
            return conn.id

        return None

    def _build_description_view_for_agent(self, description: "Description") -> DescriptionViewForAgent:
        """
        构建描述视图（用于 Agent/DM/Evolution，包含 hint）

        Args:
            description: 原始描述对象

        Returns:
            DescriptionViewForAgent: 包含 public, hint 和 add 的描述视图
        """
        return DescriptionViewForAgent(
            public=description.public,
            hint=description.hint,
            add=description.add  # 直接使用 base.DescriptionAddItem
        )

    def _build_description_view_for_npc(self, description: "Description") -> DescriptionViewForNpc:
        """
        构建描述视图（用于 NPC，不包含 hint）

        Args:
            description: 原始描述对象

        Returns:
            DescriptionViewForNpc: 包含 public 和 add 的描述视图
        """
        return DescriptionViewForNpc(
            public=description.public,
            add=description.add  # 直接使用 base.DescriptionAddItem
        )

    def _get_dm_view(self, map_id: str) -> DMWorldView:
        """
        DM View: 描述信息（public + add）

        Args:
            map_id: 地图 ID

        Returns:
            DMWorldView: 包含地图、角色、物品的描述信息
        """
        map_entity = self.world_state.get_map(map_id)
        characters, items = self._get_visible_entities_at_map(map_id)

        return DMWorldView(
            map_id=map_entity.id,
            map_name=map_entity.name,
            map_description=self._build_description_view_for_agent(map_entity.description),
            characters={
                char.id: EntityDescriptionSummary(
                    entity_id=char.id,
                    entity_name=char.name,
                    description=self._build_description_view_for_agent(char.description)
                )
                for char in characters
            },
            items={
                item.id: EntityDescriptionSummary(
                    entity_id=item.id,
                    entity_name=item.name,
                    description=self._build_description_view_for_agent(item.description)
                )
                for item in items
            }
        )

    def _get_state_agent_view(self, map_id: str) -> StateAgentWorldView:
        """
        StateAgent View: 可写字段按实体分类组织

        重要：清晰标注每个字段属于哪个实体，方便 Agent 理解和修改

        Args:
            map_id: 地图 ID

        Returns:
            StateAgentWorldView: 按实体分类的可写字段视图
        """
        map_entity = self.world_state.get_map(map_id)
        entities = []
        extension_registry = self.world_state.get_store_copy().extension_registry

        def _is_mutable_extension(key: str) -> bool:
            if extension_registry is None:
                return True
            spec = extension_registry.fields.get(key)
            if spec is None:
                return False
            return bool(spec.mutable)

        map_writable_paths = set(getattr(type(map_entity), "WRITABLE_PATHS", ()))
        move_target_ids = [
            target_id
            for target_id in (
                self._resolve_connection_target_map_id(map_id, conn)
                for conn in map_entity.connections
            )
            if target_id
        ]
        if map_id not in move_target_ids:
            move_target_ids.insert(0, map_id)
        move_targets_hint = "、".join(move_target_ids)

        # 1. 地图实体
        map_writable_fields: List[WritableFieldInfo] = []
        if "description.add" in map_writable_paths:
            map_writable_fields.append(
                WritableFieldInfo(
                    field_path="description.add",
                    field_name="暂存描述",
                    current_value=[{"turn": a.turn, "content": a.content} for a in map_entity.description.add],
                    value_type="list",
                    description="暂存的描述变化，等待合并入 public"
                )
            )

        if "connections[*].is_locked" in map_writable_paths:
            for i, conn in enumerate(map_entity.connections):
                map_writable_fields.append(
                    WritableFieldInfo(
                        field_path=f"connections[{i}].is_locked",
                        field_name=f"连接「{conn.name}」锁定状态",
                        current_value=conn.is_locked,
                        value_type="boolean",
                        description=f"方向 {conn.direction}，描述：{conn.description}"
                    )
                )

        if "extensions.*" in map_writable_paths:
            for key, value in sorted(map_entity.extensions.items()):
                if not _is_mutable_extension(key):
                    continue
                map_writable_fields.append(
                    WritableFieldInfo(
                        field_path=f"extensions.{key}",
                        field_name=f"扩展字段 {key}",
                        current_value=value,
                        value_type=self._infer_value_type(value),
                        description="schema registry 标记为 mutable 的扩展字段"
                    )
                )

        entities.append(EntityWritableView(
            entity_id=map_entity.id,
            entity_type="map",
            entity_name=map_entity.name,
            description_summary="当前所在地图",
            writable_fields=map_writable_fields
        ))

        # 2. 角色实体（只获取在当前地图上的）
        characters, items = self._get_visible_entities_at_map(map_id)
        for char in characters:
            char_writable_paths = set(getattr(type(char), "WRITABLE_PATHS", ()))
            char_writable_fields: List[WritableFieldInfo] = []

            if "location" in char_writable_paths:
                char_writable_fields.append(
                    WritableFieldInfo(
                        field_path="location",
                        field_name="位置",
                        current_value=char.location,
                        value_type="string",
                        description=f"角色当前所在位置；可选目标地图ID：{move_targets_hint}"
                    )
                )

            if "description.add" in char_writable_paths:
                char_writable_fields.append(
                    WritableFieldInfo(
                        field_path="description.add",
                        field_name="暂存描述",
                        current_value=[{"turn": a.turn, "content": a.content} for a in char.description.add],
                        value_type="list",
                        description="暂存的描述变化"
                    )
                )

            if "attributes.*.value" in char_writable_paths:
                for attr_id, attr in char.attributes.items():
                    char_writable_fields.append(
                        WritableFieldInfo(
                            field_path=f"attributes.{attr_id}.value",
                            field_name=f"{attr.name}",
                            current_value=attr.value,
                            value_type="number",
                            description=attr.description
                        )
                    )

            if "status.*.value" in char_writable_paths:
                for status_id, status in char.status.items():
                    char_writable_fields.append(
                        WritableFieldInfo(
                            field_path=f"status.{status_id}.value",
                            field_name=f"{status.name}",
                            current_value=status.value,
                            value_type="number",
                            description=status.description
                        )
                    )

            if "extensions.*" in char_writable_paths:
                for key, value in sorted(char.extensions.items()):
                    if not _is_mutable_extension(key):
                        continue
                    char_writable_fields.append(
                        WritableFieldInfo(
                            field_path=f"extensions.{key}",
                            field_name=f"扩展字段 {key}",
                            current_value=value,
                            value_type=self._infer_value_type(value),
                            description="schema registry 标记为 mutable 的扩展字段"
                        )
                    )

            entities.append(EntityWritableView(
                entity_id=char.id,
                entity_type="character",
                entity_name=char.name,
                description_summary="地图上的角色",
                writable_fields=char_writable_fields
            ))

        # 3. 物品实体（只获取在当前地图上的，物品在人物身上时不可见）
        for item in items:
            item_writable_paths = set(getattr(type(item), "WRITABLE_PATHS", ()))
            item_writable_fields: List[WritableFieldInfo] = []
            if "location" in item_writable_paths:
                item_writable_fields.append(
                    WritableFieldInfo(
                        field_path="location",
                        field_name="位置",
                        current_value=item.location,
                        value_type="string",
                        description=f"物品当前所在位置；可选地图ID：{move_targets_hint}"
                    )
                )
            if "description.add" in item_writable_paths:
                item_writable_fields.append(
                    WritableFieldInfo(
                        field_path="description.add",
                        field_name="暂存描述",
                        current_value=[{"turn": a.turn, "content": a.content} for a in item.description.add],
                        value_type="list",
                        description="暂存的描述变化"
                    )
                )
            if "extensions.*" in item_writable_paths:
                for key, value in sorted(item.extensions.items()):
                    if not _is_mutable_extension(key):
                        continue
                    item_writable_fields.append(
                        WritableFieldInfo(
                            field_path=f"extensions.{key}",
                            field_name=f"扩展字段 {key}",
                            current_value=value,
                            value_type=self._infer_value_type(value),
                            description="schema registry 标记为 mutable 的扩展字段"
                        )
                    )

            entities.append(EntityWritableView(
                entity_id=item.id,
                entity_type="item",
                entity_name=item.name,
                description_summary="地图上的物品",
                writable_fields=item_writable_fields
            ))

        return StateAgentWorldView(
            map_id=map_entity.id,
            map_name=map_entity.name,
            entities=entities
        )

    @staticmethod
    def _infer_value_type(value: Any) -> str:
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, (int, float)):
            return "number"
        if isinstance(value, list):
            return "list"
        if isinstance(value, dict):
            return "object"
        return "string"

    def _get_npc_scheduler_view(self, map_id: str) -> NpcSchedulerWorldView:
        """
        NpcScheduler View: 当前地图 + 相邻地图切片

        注意：不包含 NPC 的 memory/goal 等个人信息

        Args:
            map_id: 地图 ID

        Returns:
            NpcSchedulerWorldView: 包含当前地图和相邻地图切片
        """
        current_map = self._build_map_slice(map_id)

        # 获取相邻地图
        adjacent_map_ids = self.world_state.get_adjacent_map_ids(map_id)
        adjacent_maps = [
            self._build_map_slice(adj_map_id)
            for adj_map_id in adjacent_map_ids
        ]

        return NpcSchedulerWorldView(
            current_map=current_map,
            adjacent_maps=adjacent_maps,
            player_location=map_id
        )

    def _get_narrative_view(self, map_id: str) -> NarrativeWorldView:
        """
        Narrative View: 地图切片（无角色详情）

        Args:
            map_id: 地图 ID

        Returns:
            NarrativeWorldView: 地图切片
        """
        return NarrativeWorldView(
            map_slice=self._build_map_slice(map_id, include_char_details=False)
        )

    def _build_map_slice(
        self,
        map_id: str,
        include_char_details: bool = True
    ) -> MapSlice:
        """
        构建地图切片

        注意：
        - 只包含在当前地图上的角色和物品
        - 物品在人物身上时不可见

        Args:
            map_id: 地图 ID
            include_char_details: 是否包含角色详细信息

        Returns:
            MapSlice: 地图切片
        """
        map_entity = self.world_state.get_map(map_id)
        characters, items = self._get_visible_entities_at_map(map_id)

        # 构建连接简要信息
        connections = [
            ConnectionBrief(
                id=conn.id,
                name=conn.name,
                direction=conn.direction,
                target_map_id=self._resolve_connection_target_map_id(map_id, conn),
                is_locked=conn.is_locked,
                condition=conn.condition,
            )
            for conn in map_entity.connections
        ]

        # 构建角色简要信息
        char_list = []
        if include_char_details:
            char_list = [
                CharBrief(
                    id=char.id,
                    name=char.name,
                    basic_info=char.basic_info,
                    description=self._build_description_view_for_npc(char.description)
                )
                for char in characters
            ]

        # 构建物品简要信息
        item_list = [
            ItemBrief(
                id=item.id,
                name=item.name,
                description=self._build_description_view_for_npc(item.description)
            )
            for item in items
        ]

        return MapSlice(
            map_id=map_entity.id,
            map_name=map_entity.name,
            description=self._build_description_view_for_npc(map_entity.description),
            connections=connections,
            characters=char_list,
            items=item_list
        )

    def get_npc_view(
        self,
        npc_id: str,
        include_memory: bool = False,
        include_goal: bool = False
    ) -> NpcWorldView:
        """
        Npc View: NPC 不完整信息

        注意：不包含 memory/goal 等个人信息，这些由 AgentMemory 提供

        Args:
            npc_id: NPC ID
            include_memory: 是否包含记忆（通常不需要，由 AgentMemory 提供）
            include_goal: 是否包含目标（通常不需要，由 AgentMemory 提供）

        Returns:
            NpcWorldView: NPC 视图
        """
        char = self.world_state.get_character(npc_id)

        return NpcWorldView(
            id=char.id,
            name=char.name,
            basic_info=char.basic_info,
            description=self._build_description_view_for_npc(char.description),
            location=char.location
            # memory 和 goal 不在此处加载，由 AgentMemory 和 NpcScheduler 分别提供
        )


# ============================================================
# WorldState 接口定义（供外部实现）
# ============================================================

class WorldStateInterface:
    """
    世界状态存储接口
    需要外部实现具体逻辑
    """

    def get_map(self, map_id: str) -> "MapEntity":
        """获取地图实体"""
        raise NotImplementedError

    def get_character(self, char_id: str) -> "CharacterEntity":
        """获取角色实体"""
        raise NotImplementedError

    def get_characters_at(self, map_id: str) -> List["CharacterEntity"]:
        """获取指定地图的所有角色（包括 NPC 和玩家）"""
        raise NotImplementedError

    def get_item(self, item_id: str) -> "ItemEntity":
        """获取物品实体"""
        raise NotImplementedError

    def get_items_at(self, map_id: str) -> List["ItemEntity"]:
        """获取指定地图的所有物品（包括在地图上的和人物身上的）"""
        raise NotImplementedError

    def get_adjacent_map_ids(self, map_id: str) -> List[str]:
        """获取指定地图的相邻地图 ID 列表"""
        raise NotImplementedError
