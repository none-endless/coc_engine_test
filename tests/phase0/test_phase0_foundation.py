import os
import tempfile
import unittest
from pathlib import Path

from src.config.loader import ConfigLoader
from src.data.model.base import CharacterEntity, MapEntity, WorldEntityStore
from src.data.model.entity_id import EntityIdGenerator, EntityIdRegistry, validate_entity_id
from src.data.model.world_state import WorldState


class TestPhase0Foundation(unittest.TestCase):
    def test_invalid_entity_id_rejected(self):
        with self.assertRaises(ValueError):
            validate_entity_id("char#bedroom")

    def test_entity_id_generator_and_registry(self):
        registry = EntityIdRegistry()
        generator = EntityIdGenerator(registry)

        first = generator.generate("char", "Bedroom Guard")
        second = generator.generate("char", "Bedroom Guard")

        self.assertRegex(first, r"^char-bedroom_guard-\d{4}$")
        self.assertRegex(second, r"^char-bedroom_guard-\d{4}$")
        self.assertNotEqual(first, second)
        self.assertTrue(registry.is_registered(first))
        self.assertTrue(registry.is_registered(second))

    def test_config_precedence_cli_over_env_over_file_over_default(self):
        yaml_text = """
runtime:
  turn_timeout: 40
  log_level: WARNING
storage:
  memory_log_path: custom/memory.log
  short_log_path: custom/short.log
""".strip()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = Path(tmpdir) / "config.yaml"
            config_file.write_text(yaml_text, encoding="utf-8")

            env = dict(os.environ)
            env["ER_RUNTIME__TURN_TIMEOUT"] = "50"
            env["ER_RUNTIME__LOG_LEVEL"] = "ERROR"

            config = ConfigLoader.load(
                config_path=str(config_file),
                env=env,
                cli_overrides={"runtime.turn_timeout": 60},
            )

            self.assertEqual(config.runtime.turn_timeout, 60)
            self.assertEqual(config.runtime.log_level, "ERROR")
            self.assertEqual(config.storage.memory_log_path, "custom/memory.log")

    def test_world_state_auto_index_and_tamper_safe(self):
        map_1 = MapEntity(id="map-bedroom-0001", name="Bedroom")
        map_2 = MapEntity(id="map-hall-0001", name="Hall")
        char = CharacterEntity(id="char-player-0001", name="Player", location=map_1.id)

        store = WorldEntityStore(
            maps={map_1.id: map_1, map_2.id: map_2},
            characters={char.id: char},
            items={},
        )

        state = WorldState()
        state.reset(store)

        snapshot_before = state.get_snapshot()
        self.assertIn(char.id, snapshot_before["maps"][map_1.id]["char_index"])

        state.update_character_location(char.id, map_2.id)
        snapshot_after = state.get_snapshot()

        self.assertNotIn(char.id, snapshot_after["maps"][map_1.id]["char_index"])
        self.assertIn(char.id, snapshot_after["maps"][map_2.id]["char_index"])

        # Tampering with returned map object should not affect real world indexes.
        leaked_copy = state.get_map(map_2.id)
        leaked_copy.char_index.append("char-hacker-9999")

        snapshot_final = state.get_snapshot()
        self.assertNotIn("char-hacker-9999", snapshot_final["maps"][map_2.id]["char_index"])


if __name__ == "__main__":
    unittest.main()
