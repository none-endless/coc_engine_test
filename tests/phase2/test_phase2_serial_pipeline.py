import unittest

from pydantic import BaseModel

from src.config.loader import ConfigLoader
from src.data.model.agent_output import DmAgentLlmOutput, EvolutionAgentLlmOutput
from src.data.model.base import Attribute, CharacterEntity, Description, MapEntity, WorldEntityStore
from src.data.model.world_state import WorldState
from src.engine.engine import Engine


class FakeLLMService:
    def __init__(self) -> None:
        self.config = ConfigLoader.load()

    def call_llm_json(
        self,
        *,
        agent_name,
        system_prompt,
        user_payload,
        output_model,
        retry_budget,
        validation_feedback=None,
    ) -> BaseModel:
        raw_text = user_payload.get("e1", {}).get("raw_text") or user_payload.get("raw_text", "")

        if output_model is DmAgentLlmOutput:
            if "攻击守卫" in raw_text:
                payload = {
                    "intent_info": {
                        "intent": "attack",
                        "routing_hint": "against",
                        "attributes": ["fight"],
                        "against_char_id": ["char-player-0000", "char-guard-0001"],
                        "difficulty": None,
                        "dm_reply": None,
                    }
                }
            elif "调查桌上的文件" in raw_text:
                payload = {
                    "intent_info": {
                        "intent": "investigate",
                        "routing_hint": "num",
                        "attributes": ["investigation"],
                        "against_char_id": ["char-player-0000"],
                        "difficulty": None,
                        "dm_reply": None,
                    }
                }
            elif "偷偷给守卫下毒" in raw_text:
                payload = {
                    "intent_info": {
                        "intent": "poison",
                        "routing_hint": "num",
                        "attributes": ["stealth"],
                        "against_char_id": ["char-player-0000"],
                        "difficulty": None,
                        "dm_reply": None,
                    }
                }
            else:
                payload = {
                    "intent_info": {
                        "intent": "talk",
                        "routing_hint": None,
                        "attributes": [],
                        "against_char_id": [],
                        "difficulty": None,
                        "dm_reply": "这里现在更适合直接由 DM 对你回复。",
                    }
                }
            return output_model.model_validate(payload)

        if output_model is EvolutionAgentLlmOutput:
            visible = "偷偷给守卫下毒" not in raw_text
            summary = "玩家执行了行动"
            if "调查桌上的文件" in raw_text:
                summary = "玩家调查了桌上的文件"
            elif "攻击守卫" in raw_text:
                summary = "玩家向守卫发起了攻击"
            elif "偷偷给守卫下毒" in raw_text:
                summary = "玩家尝试偷偷给守卫下毒"
            return output_model.model_validate(
                {
                    "summary": summary,
                    "visible_to_player": visible,
                }
            )

        raise AssertionError(f"unsupported output model: {output_model}")


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
        self.engine = Engine(world_state=world, mode="phase2", dm_max_retries=2, llm_service=FakeLLMService())

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

    def test_dm_reply_short_circuits_serial_pipeline(self):
        result = self.engine.run_turn(
            raw_input="我想和守卫聊聊",
            actor_id="char-player-0000",
            turn_id=4,
            trace_id=1004,
        )

        self.assertEqual(result["route"], "dm_direct_reply")
        self.assertEqual(result["reply"], "这里现在更适合直接由 DM 对你回复。")
        self.assertFalse(result["narrative_triggered"])
        self.assertNotIn("evolution", result)


if __name__ == "__main__":
    unittest.main()
