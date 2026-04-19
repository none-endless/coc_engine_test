from __future__ import annotations

import copy
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from .base import CharacterEntity, ItemEntity, MapEntity, WorldEntityStore


class WorldSnapshot(BaseModel):
    """Typed world snapshot shared across read-only consumers."""

    version: int = Field(default=0)
    snapshot_at: str = Field(default="")
    maps: Dict[str, MapEntity] = Field(default_factory=dict)
    characters: Dict[str, CharacterEntity] = Field(default_factory=dict)
    items: Dict[str, ItemEntity] = Field(default_factory=dict)

    def to_payload(self) -> Dict[str, Any]:
        """Serialize snapshot for storage and JSON-based processors."""
        return self.model_dump(mode="json")

    def __getitem__(self, key: str) -> Any:
        return self.to_payload()[key]

    def get(self, key: str, default: Optional[Any] = None) -> Any:
        return self.to_payload().get(key, default)


class WorldState:
    """Singleton world-state container with derived indexes and snapshots."""

    _instance: Optional["WorldState"] = None
    _instance_lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, initial_store: Optional[WorldEntityStore] = None) -> None:
        if getattr(self, "_initialized", False):
            return

        self._write_lock = threading.RLock()
        self._store = (initial_store.model_copy(deep=True) if initial_store else WorldEntityStore())
        self._version = 0
        self._snapshot_cache = WorldSnapshot()
        self._initialized = True

        with self._write_lock:
            self._derive_indexes_locked()
            self._refresh_snapshot_locked()

    def reset(self, store: Optional[WorldEntityStore] = None) -> None:
        """Reset singleton internals, mainly used by tests/bootstrap."""
        with self._write_lock:
            self._store = store.model_copy(deep=True) if store else WorldEntityStore()
            self._version = 0
            self._derive_indexes_locked()
            self._refresh_snapshot_locked()

    def register_map(self, map_entity: MapEntity) -> None:
        with self._write_lock:
            self._store.maps[map_entity.id] = map_entity.model_copy(deep=True)
            self._version += 1
            self._derive_indexes_locked()
            self._refresh_snapshot_locked()

    def register_character(self, character: CharacterEntity) -> None:
        with self._write_lock:
            self._store.characters[character.id] = character.model_copy(deep=True)
            self._version += 1
            self._derive_indexes_locked()
            self._refresh_snapshot_locked()

    def register_item(self, item: ItemEntity) -> None:
        with self._write_lock:
            self._store.items[item.id] = item.model_copy(deep=True)
            self._version += 1
            self._derive_indexes_locked()
            self._refresh_snapshot_locked()

    def update_character_location(self, char_id: str, new_map_id: str) -> None:
        with self._write_lock:
            if new_map_id not in self._store.maps:
                raise KeyError(f"unknown map id: {new_map_id}")
            character = self._store.characters[char_id]
            character.location = new_map_id
            self._version += 1
            self._derive_indexes_locked()
            self._refresh_snapshot_locked()

    def update_item_location(self, item_id: str, new_location: str) -> None:
        with self._write_lock:
            item = self._store.items[item_id]
            item.location = new_location
            self._version += 1
            self._derive_indexes_locked()
            self._refresh_snapshot_locked()

    def get_map(self, map_id: str) -> MapEntity:
        return self._store.maps[map_id].model_copy(deep=True)

    def get_character(self, char_id: str) -> CharacterEntity:
        return self._store.characters[char_id].model_copy(deep=True)

    def get_item(self, item_id: str) -> ItemEntity:
        return self._store.items[item_id].model_copy(deep=True)

    def get_characters_at(self, map_id: str) -> List[CharacterEntity]:
        return [
            char.model_copy(deep=True)
            for char in self._store.characters.values()
            if char.location == map_id
        ]

    def get_items_at(self, map_id: str) -> List[ItemEntity]:
        return [
            item.model_copy(deep=True)
            for item in self._store.items.values()
            if item.location == map_id
        ]

    def get_adjacent_map_ids(self, map_id: str) -> List[str]:
        map_entity = self._store.maps[map_id]
        result: List[str] = []
        for conn in map_entity.connections:
            target = getattr(conn, "target_map_id", None)
            if target:
                result.append(target)
                continue
            if conn.id in self._store.maps and conn.id != map_id:
                result.append(conn.id)
        return result

    def get_snapshot(self) -> WorldSnapshot:
        """Return an immutable-by-copy snapshot for lock-free readers."""
        return self._snapshot_cache.model_copy(deep=True)

    def get_version(self) -> int:
        """Return current world version."""
        with self._write_lock:
            return self._version

    def get_store_copy(self) -> WorldEntityStore:
        """Return deep-copied world entity store for transactional writes."""
        with self._write_lock:
            return self._store.model_copy(deep=True)

    def capture_checkpoint(self) -> Dict[str, Any]:
        """Capture rollback checkpoint for current turn."""
        with self._write_lock:
            return {
                "version": self._version,
                "store": self._store.model_dump(mode="json"),
            }

    def restore_checkpoint(self, checkpoint: Dict[str, Any]) -> None:
        """Restore world state from checkpoint."""
        with self._write_lock:
            store_payload = checkpoint.get("store", {})
            self._store = WorldEntityStore.model_validate(store_payload)
            self._version = int(checkpoint.get("version", 0))
            self._derive_indexes_locked()
            self._refresh_snapshot_locked()

    def commit_store(self, store: WorldEntityStore, expected_version: Optional[int] = None) -> int:
        """Commit one transactional store update and return new world version."""
        with self._write_lock:
            if expected_version is not None and expected_version != self._version:
                raise ValueError(f"snapshot version mismatch: expected {expected_version}, got {self._version}")
            self._store = store.model_copy(deep=True)
            self._version += 1
            self._derive_indexes_locked()
            self._refresh_snapshot_locked()
            return self._version

    def _derive_indexes_locked(self) -> None:
        char_index: Dict[str, List[str]] = {map_id: [] for map_id in self._store.maps.keys()}
        item_index: Dict[str, List[str]] = {map_id: [] for map_id in self._store.maps.keys()}

        for char in self._store.characters.values():
            if char.location in char_index:
                char_index[char.location].append(char.id)

        for item in self._store.items.values():
            if item.location in item_index:
                item_index[item.location].append(item.id)

        for map_id, map_entity in self._store.maps.items():
            map_entity.char_index = sorted(char_index[map_id])
            map_entity.item_index = sorted(item_index[map_id])

    def _refresh_snapshot_locked(self) -> None:
        self._snapshot_cache = WorldSnapshot(
            version=self._version,
            snapshot_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            maps={
                map_id: map_entity.model_copy(deep=True)
                for map_id, map_entity in self._store.maps.items()
            },
            characters={
                char_id: char.model_copy(deep=True)
                for char_id, char in self._store.characters.items()
            },
            items={
                item_id: item.model_copy(deep=True)
                for item_id, item in self._store.items.items()
            },
        )
