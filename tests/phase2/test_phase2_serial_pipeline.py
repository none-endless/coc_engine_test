import unittest

from src.data.model.base import Attribute, CharacterEntity, Description, MapEntity, WorldEntityStore
from src.data.model.world_state import WorldState
from src.engine.engine import Phase2Engine


class TestPhase2SerialPipeline(unittest.TestCase):
    def setUp(self) -> None:
        room = MapEntity(
            id="map-lab-0001",
            name="实验室",
            description=Description(public=["你在一个安静的实验室里。"]),
        )
        player = CharacterEntity(
            id="char-player-0000",
            name="玩家",
            location=room.id,
            attributes={
                "fight": Attribute(id="fight", name="格斗", value=60, max_value=100, min_value=0),
                "investigation": Attribute(id="investigation", name="侦查", value=55, max_value=100, min_value=0),
                "stealth": Attribute(id="stealth", name="潜行", value=45, max_value=100, min_value=0),
            },
        )
        guard = CharacterEntity(
            id="char-guard-0001",
            name="守卫",
            location=room.id,
            attributes={
                "fight": Attribute(id="fight", name="格斗", value=50, max_value=100, min_value=0),
            },
        )

        world = WorldState()
        world.reset(
            WorldEntityStore(
                maps={room.id: room},
                characters={player.id: player, guard.id: guard},
                items={},
            )
        )
        self.engine = Phase2Engine(world_state=world, dm_max_retries=2)

    def test_dm_output_has_valid_check_target_ids(self):
        result = self.engine.run_turn(
            raw_input="我攻击守卫",
            actor_id="char-player-0000",
            turn_id=1,
            trace_id=1001,
        )

        dm = result["dm"]["intent_info"]
        self.assertEqual(dm["routing_hint"], "against")
        self.assertGreaterEqual(len(dm["against_char_id"]), 2)

        valid_ids = set(self.engine.world_state.get_snapshot()["characters"].keys())
        for char_id in dm["against_char_id"]:
            self.assertIn(char_id, valid_ids)

    def test_evolution_summary_contains_turn_and_trace(self):
        result = self.engine.run_turn(
            raw_input="我调查桌上的文件",
            actor_id="char-player-0000",
            turn_id=2,
            trace_id=1002,
        )

        summary = result["evolution"]["summary"]
        self.assertIn("turn=2", summary)
        self.assertIn("trace=1002", summary)
        self.assertEqual(result["evolution"]["turn_id"], 2)
        self.assertEqual(result["evolution"]["trace_id"], 1002)

    def test_hidden_action_marked_invisible(self):
        result = self.engine.run_turn(
            raw_input="我偷偷给守卫下毒",
            actor_id="char-player-0000",
            turn_id=3,
            trace_id=1003,
        )

        evolution = result["evolution"]
        self.assertFalse(evolution["visible_to_player"])
        self.assertTrue(evolution["should_skip_narrative"])
        self.assertFalse(result["narrative_triggered"])
        self.assertGreaterEqual(len(evolution["e7"]["narrative_list"]), 1)


if __name__ == "__main__":
    unittest.main()
