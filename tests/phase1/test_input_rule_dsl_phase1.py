import unittest

from src.agent.llm.service import LLMServiceBase
from src.data.model.base import (
    Attribute,
    CharacterEntity,
    Description,
    ExtensionSchemaItem,
    ExtensionSchemaRegistry,
    ItemEntity,
    MapEntity,
    WorldEntityStore,
)
from src.data.model.world_state import WorldState
from src.rule import DslEngine, InputSystem, RuleSystem
from src.utils.world_provider import WorldDataProvider


class _FixedRandom:
    def __init__(self, value: int) -> None:
        self.value = value

    def randint(self, _a: int, _b: int) -> int:
        return self.value


class TestPhase1InputAndRule(unittest.TestCase):
    def setUp(self) -> None:
        self.player_id = "char-player-0000"

        room = MapEntity(
            id="map-cellar-0001",
            name="地窖",
            description=Description(public=["你站在潮湿的地窖里。", "墙边有一扇铁门。"]),
        )
        key = ItemEntity(id="item-key-0008", name="黄铜钥匙", location=self.player_id)
        player = CharacterEntity(
            id=self.player_id,
            name="玩家",
            location=room.id,
            attributes={
                "hp": Attribute(id="hp", name="生命", value=10, max_value=100, min_value=0),
            },
            inventory={key.id: key},
        )

        store = WorldEntityStore(
            maps={room.id: room},
            characters={player.id: player},
            items={key.id: key},
        )

        self.world_state = WorldState()
        self.world_state.reset(store)
        self.rule_system = RuleSystem(world_state=self.world_state)

    def test_meta_look_bypasses_dm(self):
        called = {"dm": False}

        def dm_handler(_envelope):
            called["dm"] = True
            return {"intent": "talk"}

        input_system = InputSystem(rule_system=self.rule_system, dm_handler=dm_handler)
        result = input_system.dispatch(
            raw_input="\\look",
            actor_id=self.player_id,
            turn=1,
            trace_id=1,
            world_version=1,
        )

        self.assertEqual(result.route, "rule_system_meta")
        self.assertFalse(called["dm"])
        self.assertIn("你站在潮湿的地窖里。", result.payload["result"])
        self.assertIn("elapsed_ms", result.payload)

    def test_condition_dsl_expression(self):
        expression = (
            "char-player-0000.attributes.hp.value > 0 "
            "and item-key-0008.location == char-player-0000"
        )
        snapshot = self.world_state.get_snapshot()
        ok = DslEngine().evaluate(expression=expression, snapshot=snapshot, expected_version=snapshot.version)
        self.assertTrue(ok)

    def test_json_extraction_handles_fenced_markdown_payload(self):
        content = """\n这是回复前缀\n```json\n{\n  \"intent\": \"talk\",\n  \"score\": 1\n}\n```\n这是回复后缀\n"""
        parsed = LLMServiceBase._extract_json_object(content)
        self.assertEqual(parsed["intent"], "talk")
        self.assertEqual(parsed["score"], 1)

    def test_state_view_writable_fields_follow_model_metadata_and_registry(self):
        store = self.world_state.get_store_copy()
        room = store.maps["map-cellar-0001"]
        player = store.characters[self.player_id]
        key = store.items["item-key-0008"]
        key.location = room.id

        room.extensions = {
            "quest.stage": "phase1",
            "quest.locked": "hidden",
        }
        player.extensions = {
            "quest.stage": "phase1",
            "quest.locked": "hidden",
        }
        key.extensions = {
            "quest.stage": "phase1",
            "quest.locked": "hidden",
        }
        store.extension_registry = ExtensionSchemaRegistry(
            fields={
                "quest.stage": ExtensionSchemaItem(key="quest.stage", mutable=True, value_type="string"),
                "quest.locked": ExtensionSchemaItem(key="quest.locked", mutable=False, value_type="string"),
            }
        )
        self.world_state.reset(store)

        provider = WorldDataProvider(world_state=self.world_state)
        views = provider.precompute_all_views(current_map_id="map-cellar-0001", turn=1)
        entity_paths = {
            entity.entity_id: {field.field_path for field in entity.writable_fields}
            for entity in views.state_agent_view.entities
        }

        self.assertIn("description.add", entity_paths["map-cellar-0001"])
        self.assertIn("extensions.quest.stage", entity_paths["map-cellar-0001"])
        self.assertNotIn("extensions.quest.locked", entity_paths["map-cellar-0001"])

        self.assertIn("location", entity_paths[self.player_id])
        self.assertIn("attributes.hp.value", entity_paths[self.player_id])
        self.assertIn("extensions.quest.stage", entity_paths[self.player_id])
        self.assertNotIn("extensions.quest.locked", entity_paths[self.player_id])

        self.assertIn("location", entity_paths["item-key-0008"])
        self.assertIn("extensions.quest.stage", entity_paths["item-key-0008"])
        self.assertNotIn("extensions.quest.locked", entity_paths["item-key-0008"])

    def test_coc_check_result_format(self):
        result = self.rule_system.run_coc_check(
            actor_id=self.player_id,
            attribute_value=50,
            attribute_name="hp",
            random_source=_FixedRandom(1),
        )
        self.assertEqual(result.id, self.player_id)
        self.assertEqual(result.name, "玩家")
        self.assertEqual(result.result_type, "大成功")
        self.assertEqual(result.check_type, "num")
        self.assertEqual(result.attribute, "hp")

    def test_against_check_result_format(self):
        guard = CharacterEntity(
            id="char-guard-0001",
            name="守卫",
            location="map-cellar-0001",
            attributes={
                "hp": Attribute(id="hp", name="生命", value=30, max_value=100, min_value=0),
            },
        )
        store = self.world_state.get_store_copy()
        store.characters[guard.id] = guard
        self.world_state.reset(store)

        result = self.rule_system.run_against_check(
            actor_id=self.player_id,
            actor_attribute_name="hp",
            actor_attribute_value=60,
            target_id=guard.id,
            target_attribute_name="hp",
            target_attribute_value=30,
            actor_random_source=_FixedRandom(10),
            target_random_source=_FixedRandom(80),
        )

        self.assertEqual(result.check_type, "against")
        self.assertEqual(result.opposed_id, guard.id)
        self.assertEqual(result.winner_id, self.player_id)
        self.assertEqual(len(result.participants), 2)


if __name__ == "__main__":
    unittest.main()
