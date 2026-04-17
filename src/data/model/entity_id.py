from __future__ import annotations

import re
import threading
from collections import defaultdict
from typing import DefaultDict, Dict, Set

ENTITY_ID_PATTERN = re.compile(r"^(map|char|item)-[a-z][a-z0-9_]*-\d{4}$")
PLAYER_ENTITY_ID = "char-player-0000"


def validate_entity_id(entity_id: str) -> str:
    if not ENTITY_ID_PATTERN.fullmatch(entity_id):
        raise ValueError("entity id must match [map|char|item]-[name]-[0000]")
    return entity_id


def _normalize_name(name: str) -> str:
    lowered = name.strip().lower()
    normalized = re.sub(r"[^a-z0-9_]+", "_", lowered)
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    if not normalized or not normalized[0].isalpha():
        normalized = f"entity_{normalized}" if normalized else "entity"
    return normalized


class EntityIdRegistry:
    """Global entity-id registry; archived ids cannot be reused."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._active_ids: Set[str] = set()
        self._archived_ids: Set[str] = set()

    def register(self, entity_id: str) -> str:
        validate_entity_id(entity_id)
        with self._lock:
            if entity_id in self._archived_ids:
                raise ValueError(f"entity id already archived: {entity_id}")
            if entity_id in self._active_ids:
                raise ValueError(f"entity id already registered: {entity_id}")
            self._active_ids.add(entity_id)
        return entity_id

    def archive(self, entity_id: str) -> None:
        validate_entity_id(entity_id)
        with self._lock:
            self._active_ids.discard(entity_id)
            self._archived_ids.add(entity_id)

    def is_registered(self, entity_id: str) -> bool:
        with self._lock:
            return entity_id in self._active_ids

    def is_archived(self, entity_id: str) -> bool:
        with self._lock:
            return entity_id in self._archived_ids


class EntityIdGenerator:
    """Generate ids in format: [type]-[meaningful_name]-[unique_suffix]."""

    def __init__(self, registry: EntityIdRegistry) -> None:
        self._registry = registry
        self._lock = threading.RLock()
        self._counters: DefaultDict[str, int] = defaultdict(int)

    def generate_player_id(self) -> str:
        with self._lock:
            if self._registry.is_archived(PLAYER_ENTITY_ID):
                raise ValueError("player id has been archived and cannot be reused")
            if self._registry.is_registered(PLAYER_ENTITY_ID):
                return PLAYER_ENTITY_ID
            self._registry.register(PLAYER_ENTITY_ID)
            return PLAYER_ENTITY_ID

    def generate(self, entity_type: str, name: str) -> str:
        if entity_type not in {"map", "char", "item"}:
            raise ValueError("entity_type must be map/char/item")

        normalized = _normalize_name(name)
        if entity_type == "char" and normalized == "player":
            return self.generate_player_id()

        key = f"{entity_type}-{normalized}"

        with self._lock:
            if key not in self._counters:
                self._counters[key] = 0
            while True:
                suffix = f"{self._counters[key]:04d}"
                entity_id = f"{key}-{suffix}"
                self._counters[key] += 1
                if self._registry.is_archived(entity_id):
                    continue
                if self._registry.is_registered(entity_id):
                    continue
                self._registry.register(entity_id)
                return entity_id


GLOBAL_ENTITY_ID_REGISTRY = EntityIdRegistry()
GLOBAL_ENTITY_ID_GENERATOR = EntityIdGenerator(GLOBAL_ENTITY_ID_REGISTRY)
