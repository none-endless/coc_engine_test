import os
import tempfile
import unittest
from pathlib import Path

from src.config.loader import ConfigLoader
from src.data.model.base import CharacterEntity, MapEntity, WorldEntityStore
from src.data.model.entity_id import EntityIdGenerator, EntityIdRegistry, validate_entity_id
from src.data.model.world_state import WorldState


class TestPhase0Foundation(unittest.TestCase):
    def test_config_blank_api_key_is_normalized_to_empty_string(self):
        yaml_text = """
llm:
    api_key:
""".strip()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = Path(tmpdir) / "config.yaml"
            config_file.write_text(yaml_text, encoding="utf-8")

            config = ConfigLoader.load(config_path=str(config_file))
            self.assertEqual(config.llm.api_key, "")

    def test_invalid_entity_id_rejected(self):
        with self.assertRaises(ValueError):
            validate_entity_id("char#bedroom")

    def test_entity_id_generator_and_registry(self):
        registry = EntityIdRegistry()
        generator = EntityIdGenerator(registry)

        player_id = generator.generate_player_id()
        first = generator.generate("char", "Bedroom Guard")
        second = generator.generate("char", "Bedroom Guard")

        self.assertEqual(player_id, "char-player-0000")
        self.assertEqual(first, "char-bedroom_guard-0000")
        self.assertEqual(second, "char-bedroom_guard-0001")
        self.assertRegex(first, r"^char-bedroom_guard-\d{4}$")
        self.assertRegex(second, r"^char-bedroom_guard-\d{4}$")
        self.assertNotEqual(first, second)
        self.assertTrue(registry.is_registered(player_id))
        self.assertTrue(registry.is_registered(first))
        self.assertTrue(registry.is_registered(second))

    def test_config_precedence_cli_over_env_over_file_over_default(self):
        yaml_text = """
llm:
    model: gpt-4
    temperature: 0.5
    max_tokens: 1800
    timeout: 25
    api_base: https://api.openai.com/v1
system:
    max_retry_count: 4
    retry_timeout_ms: 4500
    fallback_error: custom fallback
    snapshot_interval: 8
agent:
    dm:
        memory_turns: 6
    npc:
        memory_turns: 16
        shortlog_turns: 31
        max_actions_per_turn: 4
        cooldown_turns: 2
    narrative:
        recent_turns: 6
description:
    add_interval: 12
""".strip()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = Path(tmpdir) / "config.yaml"
            config_file.write_text(yaml_text, encoding="utf-8")

            env = dict(os.environ)
            env["ER_SYSTEM__MAX_RETRY_COUNT"] = "5"
            env["ER_AGENT__NPC__MAX_ACTIONS_PER_TURN"] = "2"

            config = ConfigLoader.load(
                config_path=str(config_file),
                env=env,
                cli_overrides={"system.max_retry_count": 6},
            )

            self.assertEqual(config.system.max_retry_count, 6)
            self.assertEqual(config.agent.npc.max_actions_per_turn, 2)
            self.assertEqual(config.description.add_interval, 12)

    def test_world_state_auto_index_and_tamper_safe(self):
        map_1 = MapEntity(id="map-bedroom-0001", name="Bedroom")
        map_2 = MapEntity(id="map-hall-0001", name="Hall")
        char = CharacterEntity(id="char-player-0000", name="Player", location=map_1.id)

        store = WorldEntityStore(
            maps={map_1.id: map_1, map_2.id: map_2},
            characters={char.id: char},
            items={},
        )

        state = WorldState()
        state.reset(store)

        snapshot_before = state.get_snapshot()
        self.assertIn(char.id, snapshot_before.maps[map_1.id].char_index)

        state.update_character_location(char.id, map_2.id)
        snapshot_after = state.get_snapshot()

        self.assertNotIn(char.id, snapshot_after.maps[map_1.id].char_index)
        self.assertIn(char.id, snapshot_after.maps[map_2.id].char_index)

        # Tampering with returned map object should not affect real world indexes.
        leaked_copy = state.get_map(map_2.id)
        leaked_copy.char_index.append("char-hacker-9999")

        snapshot_final = state.get_snapshot()
        self.assertNotIn("char-hacker-9999", snapshot_final.maps[map_2.id].char_index)

    def test_world_snapshot_typed_access_and_payload_compatibility(self):
        room = MapEntity(id="map-room-0001", name="Room")
        player = CharacterEntity(id="char-player-0000", name="Player", location=room.id)
        state = WorldState()
        state.reset(WorldEntityStore(maps={room.id: room}, characters={player.id: player}, items={}))

        snapshot = state.get_snapshot()
        self.assertEqual(snapshot.version, state.get_version())
        self.assertIn(room.id, snapshot.maps)
        self.assertIn(player.id, snapshot.characters)

        payload = snapshot.to_payload()
        self.assertEqual(payload["version"], snapshot.version)
        self.assertIn(room.id, payload["maps"])
        self.assertIn(player.id, payload["characters"])


if __name__ == "__main__":
    unittest.main()
