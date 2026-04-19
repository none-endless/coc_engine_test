from __future__ import annotations

from typing import Iterable, Optional, Set

from src.config.constants import DEFAULT_DEXTERITY_ATTRIBUTE_KEYS
from src.data.model.base import CharacterEntity
from src.data.model.world_state import WorldState


class EngineBootstrapError(ValueError):
    """引擎启动前置校验失败。"""


def validate_required_dexterity(
    world_state: WorldState,
    dexterity_attribute_keys: Optional[Iterable[str]] = None,
) -> None:
    """校验全部角色是否具备敏捷属性，缺失时禁止进入系统。"""
    snapshot = world_state.get_snapshot()
    normalized_keys = _normalize_attribute_keys(dexterity_attribute_keys)
    missing_character_ids = []

    for char_id in sorted(snapshot.characters.keys()):
        character = world_state.get_character(char_id)
        if not _has_dexterity_attribute(character, normalized_keys):
            missing_character_ids.append(f"{character.id}({character.name})")

    if not missing_character_ids:
        return

    missing_text = ", ".join(missing_character_ids)
    raise EngineBootstrapError(
        "系统启动失败：当前游戏设计缺少必需的`敏捷(dexterity)`属性。"
        f"请先为以下角色补充`敏捷`属性后再进入系统：{missing_text}"
    )


def _has_dexterity_attribute(character: CharacterEntity, dexterity_keys: Set[str]) -> bool:
    """同时兼容属性 id 与展示名中的敏捷标识。"""
    for attr_id, attr in character.attributes.items():
        if _matches_dexterity_keys([attr_id, attr.id, attr.name], dexterity_keys):
            return True
    return False


def _matches_dexterity_keys(values: Iterable[str], dexterity_keys: Set[str]) -> bool:
    for value in values:
        normalized = str(value).strip().lower()
        if normalized in dexterity_keys:
            return True
    return False


def _normalize_attribute_keys(values: Optional[Iterable[str]]) -> Set[str]:
    normalized = {
        str(value).strip().lower()
        for value in (values or DEFAULT_DEXTERITY_ATTRIBUTE_KEYS)
        if str(value).strip()
    }
    return normalized or set(DEFAULT_DEXTERITY_ATTRIBUTE_KEYS)
