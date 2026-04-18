from __future__ import annotations

from typing import Iterable

from src.data.model.base import CharacterEntity
from src.data.model.world_state import WorldState


DEXTERITY_ATTRIBUTE_IDS = {"dexterity", "敏捷"}


class EngineBootstrapError(ValueError):
    """引擎启动前置校验失败。"""


def validate_required_dexterity(world_state: WorldState) -> None:
    """校验全部角色是否具备敏捷属性，缺失时禁止进入系统。"""
    snapshot = world_state.get_snapshot()
    missing_character_ids = []

    for char_id in sorted(snapshot.get("characters", {}).keys()):
        character = world_state.get_character(char_id)
        if not _has_dexterity_attribute(character):
            missing_character_ids.append(f"{character.id}({character.name})")

    if not missing_character_ids:
        return

    missing_text = ", ".join(missing_character_ids)
    raise EngineBootstrapError(
        "系统启动失败：当前游戏设计缺少必需的`敏捷(dexterity)`属性。"
        f"请先为以下角色补充`敏捷`属性后再进入系统：{missing_text}"
    )


def _has_dexterity_attribute(character: CharacterEntity) -> bool:
    """同时兼容属性 id 与展示名中的敏捷标识。"""
    for attr_id, attr in character.attributes.items():
        if _matches_dexterity_keys([attr_id, attr.id, attr.name]):
            return True
    return False


def _matches_dexterity_keys(values: Iterable[str]) -> bool:
    for value in values:
        normalized = str(value).strip().lower()
        if normalized in DEXTERITY_ATTRIBUTE_IDS:
            return True
    return False
