import unittest
from inspect import signature

from pydantic import BaseModel

from src.agent.llm.input_agent import DMAgent
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
                summary = "turn=2 trace=1002 玩家调查了桌上的文件"
            elif "攻击守卫" in raw_text:
                summary = "turn=1 trace=1001 玩家向守卫发起了攻击"
            elif "偷偷给守卫下毒" in raw_text:
                summary = "turn=3 trace=1003 玩家尝试偷偷给守卫下毒"
            return output_model.model_validate(
                {
                    "summary": summary,
                    "visible_to_player": visible,
                }
            )

        raise AssertionError(f"unsupported output model: {output_model}")


class RetryAwareFakeLLMService:
    def __init__(self) -> None:
        self.config = ConfigLoader.load()
        self.dm_call_count = 0
        self.last_dm_payload = None

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
        if output_model is DmAgentLlmOutput:
            self.dm_call_count += 1
            self.last_dm_payload = user_payload
            if self.dm_call_count == 1:
                return output_model.model_validate(
                    {
                        "intent_info": {
                            "intent": "attack",
                            "routing_hint": "against",
                            "attributes": ["力量"],
                            "against_char_id": ["char-player-0000", "char-guard-0001"],
                            "difficulty": None,
                            "dm_reply": None,
                        }
                    }
                )
            return output_model.model_validate(
                {
                    "intent_info": {
                        "intent": "attack",
                        "routing_hint": "against",
                        "attributes": ["fight"],
                        "against_char_id": ["char-player-0000", "char-guard-0001"],
                        "difficulty": None,
                        "dm_reply": None,
                    }
                }
            )

        if output_model is EvolutionAgentLlmOutput:
            return output_model.model_validate(
                {
                    "summary": "玩家向守卫发起了攻击",
                    "visible_to_player": True,
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
                "dexterity": Attribute(id="dexterity", name="敏捷", value=70, max_value=100, min_value=0),
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
                "dexterity": Attribute(id="dexterity", name="敏捷", value=50, max_value=100, min_value=0),
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

        valid_ids = set(self.engine.world_state.get_snapshot().characters.keys())
        for char_id in dm["against_char_id"]:
            self.assertIn(char_id, valid_ids)

    def test_dm_agent_run_signature_only_accepts_agent_input(self):
        params = list(signature(DMAgent.run).parameters.values())
        self.assertEqual([item.name for item in params], ["self", "agent_input"])

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

    def test_dm_memory_is_updated_and_respects_config(self):
        self.engine.run_turn(
            raw_input="我想和守卫聊聊",
            actor_id="char-player-0000",
            turn_id=5,
            trace_id=1005,
        )

        self.assertEqual(self.engine._dm_memory.memory_turns, 5)
        self.assertEqual(len(self.engine._dm_memory.dialogues), 1)
        self.assertEqual(self.engine._dm_memory.dialogues[0].speaker, "dmagent")
        self.assertEqual(self.engine._dm_memory.dialogues[0].content, "这里现在更适合直接由 DM 对你回复。")

    def test_dm_memory_rollover_pushes_old_entries_to_log(self):
        for index in range(1, 9):
            self.engine.run_turn(
                raw_input=f"第{index}次对话",
                actor_id="char-player-0000",
                turn_id=10 + index,
                trace_id=2000 + index,
            )

        self.assertEqual(self.engine._dm_memory.memory_turns, 5)
        self.assertEqual(len(self.engine._dm_memory.dialogues), 5)
        self.assertGreaterEqual(len(self.engine._dm_memory.dialogue_log), 3)

    def test_dm_input_contains_available_attribute_ids_and_valid_characters(self):
        service = RetryAwareFakeLLMService()
        engine = Engine(world_state=self.engine.world_state, mode="phase2", dm_max_retries=2, llm_service=service)
        result = engine.run_turn(
            raw_input="我攻击守卫",
            actor_id="char-player-0000",
            turn_id=20,
            trace_id=2020,
        )

        self.assertEqual(result["route"], "serial_nl")
        self.assertEqual(service.last_dm_payload["available_attributes"][0]["id"], "dexterity")
        self.assertEqual(service.last_dm_payload["available_attributes"][0]["name"], "敏捷")
        self.assertIn("char-player-0000", [item["id"] for item in service.last_dm_payload["valid_characters"]])
        self.assertIn("char-guard-0001", [item["id"] for item in service.last_dm_payload["valid_characters"]])
        self.assertNotIn("narrative_info", service.last_dm_payload)
        self.assertNotIn("dialogue_log", service.last_dm_payload.get("agent_memory", {}))
        self.assertEqual(result["dm"]["intent_info"]["attributes"], ["fight"])


if __name__ == "__main__":
    unittest.main()
