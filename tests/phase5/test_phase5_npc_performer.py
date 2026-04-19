import unittest
from typing import Any, Dict, Type

from pydantic import BaseModel

from src.config.loader import ConfigLoader
from src.data.model.agent_output import (
    DmAgentLlmOutput,
    EvolutionAgentLlmOutput,
    MergerAgentLlmOutput,
    NarrativeAgentLlmOutput,
    NpcPerformerAgentLlmOutput,
    NpcSchedulerAgentLlmOutput,
    StateAgentLlmOutput,
)
from src.data.model.base import Attribute, CharacterEntity, Description, Goal, MapEntity, Status, WorldEntityStore
from src.data.model.world_state import WorldState
from src.engine.engine import Engine


class PerformerPipelineFakeLLMService:
    def __init__(self, performer_payload: Dict[str, Any] | None = None) -> None:
        self.config = ConfigLoader.load(
            cli_overrides={
                "system.max_retry_count": 1,
                "agent.npc.max_actions_per_turn": 1,
            }
        )
        self._performer_payload = performer_payload or {
            "intent": "description",
            "action_text": "守卫握紧武器，环顾四周，准备检查声响来源。",
            "routing_hint": None,
            "attributes": [],
            "against_char_id": [],
            "difficulty": None,
            "change_basic_goal": None,
            "change_active_goal": "调查可疑声响",
        }
        self.payloads: Dict[str, Dict[str, Any]] = {}

    def call_llm_json(
        self,
        *,
        agent_name: str,
        system_prompt: str,
        user_payload: Dict[str, Any],
        output_model: Type[BaseModel],
        retry_budget: int,
        validation_feedback: Any = None,
    ) -> BaseModel:
        self.payloads[agent_name] = user_payload
        if output_model is DmAgentLlmOutput:
            return output_model.model_validate(
                {
                    "intent_info": {
                        "intent": "wait",
                        "routing_hint": None,
                        "attributes": [],
                        "against_char_id": [],
                        "difficulty": None,
                        "dm_reply": None,
                    }
                }
            )
        if output_model is EvolutionAgentLlmOutput:
            return output_model.model_validate(
                {
                    "summary": "玩家停在原地，守卫注意到周围出现了可疑动静。",
                    "visible_to_player": True,
                }
            )
        if output_model is NpcSchedulerAgentLlmOutput:
            return output_model.model_validate(
                {
                    "step_result": {
                        "summary": "守卫被调度，需要提高警惕。",
                        "scheduled_npc_ids": ["char-guard-0001"],
                        "extra_npc_context": {"char-guard-0001": "你刚听见附近传来可疑声响。"},
                    }
                }
            )
        if output_model is NpcPerformerAgentLlmOutput:
            return output_model.model_validate(self._performer_payload)
        if output_model is NarrativeAgentLlmOutput:
            return output_model.model_validate({"narrative_str": "你暂时没有继续行动。"})
        if output_model is MergerAgentLlmOutput:
            return output_model.model_validate({"narrative_str": "本回合局势暂时僵持。"})
        if output_model is StateAgentLlmOutput:
            return output_model.model_validate({"changes": []})
        raise AssertionError(f"unsupported output model: {output_model}")


class TestPhase5NpcPerformer(unittest.TestCase):
    def setUp(self) -> None:
        room = MapEntity(
            id="map-room-0001",
            name="值班室",
            description=Description(public=["狭小的值班室"]),
        )
        player = CharacterEntity(
            id="char-player-0000",
            name="玩家",
            location=room.id,
            attributes={
                "dexterity": Attribute(id="dexterity", name="敏捷", value=70, max_value=100, min_value=0),
                "fight": Attribute(id="fight", name="格斗", value=60, max_value=100, min_value=0),
            },
            status={"health": Status(id="health", name="生命", value=10, max_value=10, min_value=0)},
        )
        guard = CharacterEntity(
            id="char-guard-0001",
            name="守卫",
            location=room.id,
            attributes={"dexterity": Attribute(id="dexterity", name="敏捷", value=55, max_value=100, min_value=0)},
            status={"health": Status(id="health", name="生命", value=10, max_value=10, min_value=0)},
            goal=Goal(base_goal="守卫值班室", active_goal="保持警惕"),
        )
        helper = CharacterEntity(
            id="char-helper-0002",
            name="副守卫",
            location=room.id,
            attributes={"dexterity": Attribute(id="dexterity", name="敏捷", value=45, max_value=100, min_value=0)},
            status={"health": Status(id="health", name="生命", value=10, max_value=10, min_value=0)},
            goal=Goal(base_goal="协助值班", active_goal="待命"),
        )
        self.world = WorldState()
        self.world.reset(
            WorldEntityStore(
                maps={room.id: room},
                characters={player.id: player, guard.id: guard, helper.id: helper},
                items={},
            )
        )

    def test_engine_runs_performer_and_updates_goal_and_memory(self):
        engine = Engine(world_state=self.world, mode="phase3", llm_service=PerformerPipelineFakeLLMService())

        result = engine.run_turn(
            raw_input="我停在原地观察",
            actor_id="char-player-0000",
            turn_id=6,
            trace_id=6006,
        )

        self.assertEqual(len(result["npcperformer"]), 1)
        performer = result["npcperformer"][0]
        self.assertEqual(performer["system_output"]["id"], "char-guard-0001")
        self.assertEqual(performer["llm_output"]["intent"], "description")
        self.assertEqual(performer["llm_output"]["change_active_goal"], "调查可疑声响")
        self.assertEqual(performer["llm_output"]["routing_hint"], None)

        self.assertEqual(len(result["npc_performer_chain"]), 1)
        chain_item = result["npc_performer_chain"][0]
        self.assertEqual(chain_item["npc_id"], "char-guard-0001")
        self.assertIsNone(chain_item["check"])
        self.assertTrue(chain_item["evolution_summary"])
        self.assertTrue(chain_item["e7"]["narrative_list"])
        merger_causality = engine.dm_agent.llm_service.payloads["merger"]["e7"]["narrative_causality"]
        self.assertGreaterEqual(merger_causality.count("'source': 'evolution'"), 2)
        self.assertNotIn("narrative_info", engine.dm_agent.llm_service.payloads["evolution"])
        self.assertNotIn("narrative_info", engine.dm_agent.llm_service.payloads["npc_scheduler"])
        self.assertNotIn("narrative_info", engine.dm_agent.llm_service.payloads["narrative"])
        self.assertNotIn("narrative_info", engine.dm_agent.llm_service.payloads["merger"])

        performer_payload = engine.dm_agent.llm_service.payloads["npc_performer"]
        available_attr_ids = {item["id"] for item in performer_payload["available_attributes"]}
        self.assertIn("dexterity", available_attr_ids)
        self.assertIn("fight", available_attr_ids)
        valid_character_ids = {item["id"] for item in performer_payload["valid_characters"]}
        self.assertIn("char-player-0000", valid_character_ids)
        self.assertIn("char-guard-0001", valid_character_ids)
        self.assertIn("char-helper-0002", valid_character_ids)
        self.assertNotIn("log", performer_payload.get("agent_memory", {}))
        self.assertNotIn("short_log", performer_payload.get("agent_memory", {}))
        self.assertNotIn("long_term_memory", performer_payload.get("agent_memory", {}))

        updated_guard = self.world.get_character("char-guard-0001")
        self.assertEqual(updated_guard.goal.active_goal, "调查可疑声响")
        self.assertIn("保持警惕", updated_guard.goal.goal_history)
        self.assertTrue(updated_guard.memory.current_event)
        self.assertIn("可疑声响", updated_guard.memory.current_event)
        self.assertEqual(updated_guard.memory.short[-1], updated_guard.memory.current_event)
        self.assertEqual(updated_guard.memory.short_log[-1].turn, 6)
        self.assertEqual(updated_guard.memory.log[-1].turn, 6)

    def test_npc_performer_can_trigger_numeric_check_and_evolution(self):
        engine = Engine(
            world_state=self.world,
            mode="phase3",
            llm_service=PerformerPipelineFakeLLMService(
                performer_payload={
                    "intent": "interaction",
                    "action_text": "守卫试图快速夺下玩家手中的物品。",
                    "routing_hint": "num",
                    "attributes": ["dexterity"],
                    "against_char_id": [],
                    "difficulty": None,
                    "change_basic_goal": None,
                    "change_active_goal": "控制局面",
                }
            ),
        )

        result = engine.run_turn(
            raw_input="我站着不动",
            actor_id="char-player-0000",
            turn_id=7,
            trace_id=7007,
        )

        self.assertEqual(len(result["npc_performer_chain"]), 1)
        chain_item = result["npc_performer_chain"][0]
        self.assertEqual(chain_item["npc_id"], "char-guard-0001")
        self.assertIsNotNone(chain_item["check"])
        self.assertEqual(chain_item["check"]["check_type"], "num")
        self.assertEqual(chain_item["check"]["id"], "char-guard-0001")
        self.assertTrue(chain_item["evolution_summary"])
        merger_payload = engine.dm_agent.llm_service.payloads["merger"]
        self.assertIn("npc_check", merger_payload["e7"]["narrative_causality"])
        self.assertIn("char-guard-0001", merger_payload["e7"]["narrative_causality"])

    def test_second_npc_reads_latest_world_after_first_npc_state_commit(self):
        class SerialNpcLLMService(PerformerPipelineFakeLLMService):
            def __init__(self) -> None:
                super().__init__()
                self.config = ConfigLoader.load(
                    cli_overrides={
                        "system.max_retry_count": 1,
                        "agent.npc.max_actions_per_turn": 2,
                    }
                )
                self._state_call_count = 0
                self.second_npc_saw_first_commit = False

            def call_llm_json(
                self,
                *,
                agent_name: str,
                system_prompt: str,
                user_payload: Dict[str, Any],
                output_model: Type[BaseModel],
                retry_budget: int,
                validation_feedback: Any = None,
            ) -> BaseModel:
                if output_model is NpcSchedulerAgentLlmOutput:
                    return output_model.model_validate(
                        {
                            "step_result": {
                                "summary": "调度双 NPC",
                                "scheduled_npc_ids": ["char-guard-0001", "char-helper-0002"],
                                "extra_npc_context": {
                                    "char-guard-0001": "先处理现场",
                                    "char-helper-0002": "随后复核",
                                },
                            }
                        }
                    )

                if output_model is NpcPerformerAgentLlmOutput:
                    npc_id = str(user_payload.get("world_info", {}).get("id", ""))
                    return output_model.model_validate(
                        {
                            "intent": "description",
                            "action_text": f"{npc_id} 执行动作。",
                            "routing_hint": None,
                            "attributes": [],
                            "against_char_id": [],
                            "difficulty": None,
                            "change_basic_goal": None,
                            "change_active_goal": "继续处理",
                        }
                    )

                if output_model is StateAgentLlmOutput:
                    self._state_call_count += 1
                    if self._state_call_count == 1:
                        return output_model.model_validate({"changes": []})
                    if self._state_call_count == 2:
                        return output_model.model_validate(
                            {
                                "changes": [
                                    {
                                        "op": "ADD",
                                        "target_path": "map-room-0001.description.add",
                                        "value": ["第一位NPC已提交状态变化"],
                                    }
                                ]
                            }
                        )

                    entities = user_payload.get("world_info", {}).get("entities", [])
                    for entity in entities:
                        if entity.get("entity_id") != "map-room-0001":
                            continue
                        for field in entity.get("writable_fields", []):
                            if field.get("field_path") != "description.add":
                                continue
                            current_value = field.get("current_value", [])
                            if any(
                                isinstance(item, dict) and item.get("content") == "第一位NPC已提交状态变化"
                                for item in current_value
                            ):
                                self.second_npc_saw_first_commit = True
                    return output_model.model_validate({"changes": []})

                return super().call_llm_json(
                    agent_name=agent_name,
                    system_prompt=system_prompt,
                    user_payload=user_payload,
                    output_model=output_model,
                    retry_budget=retry_budget,
                    validation_feedback=validation_feedback,
                )

        service = SerialNpcLLMService()
        engine = Engine(world_state=self.world, mode="phase3", llm_service=service)

        result = engine.run_turn(
            raw_input="我保持观察",
            actor_id="char-player-0000",
            turn_id=9,
            trace_id=9009,
        )

        self.assertFalse(result["terminated"])
        self.assertEqual([item["system_output"]["id"] for item in result["npcperformer"]], ["char-guard-0001", "char-helper-0002"])
        self.assertTrue(service.second_npc_saw_first_commit)

    def test_npc_state_rolls_back_when_side_effects_fail(self):
        class SideEffectsFailureLLMService(PerformerPipelineFakeLLMService):
            def __init__(self) -> None:
                super().__init__()
                self._state_call_count = 0

            def call_llm_json(
                self,
                *,
                agent_name: str,
                system_prompt: str,
                user_payload: Dict[str, Any],
                output_model: Type[BaseModel],
                retry_budget: int,
                validation_feedback: Any = None,
            ) -> BaseModel:
                if output_model is StateAgentLlmOutput:
                    self._state_call_count += 1
                    if self._state_call_count == 1:
                        return output_model.model_validate({"changes": []})
                    return output_model.model_validate(
                        {
                            "changes": [
                                {
                                    "op": "ADD",
                                    "target_path": "map-room-0001.description.add",
                                    "value": ["应被回滚的状态补丁"],
                                }
                            ]
                        }
                    )

                return super().call_llm_json(
                    agent_name=agent_name,
                    system_prompt=system_prompt,
                    user_payload=user_payload,
                    output_model=output_model,
                    retry_budget=retry_budget,
                    validation_feedback=validation_feedback,
                )

        service = SideEffectsFailureLLMService()
        engine = Engine(world_state=self.world, mode="phase3", llm_service=service)

        def _raise_side_effect_failure(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("side effects failed")

        engine.npc_performer_agent.apply_side_effects = _raise_side_effect_failure

        result = engine.run_turn(
            raw_input="我继续等待",
            actor_id="char-player-0000",
            turn_id=10,
            trace_id=9010,
        )

        self.assertTrue(result["terminated"])
        self.assertEqual(result["fallback_error"]["code"], "NPC_SIDE_EFFECTS_FAILED")
        self.assertEqual(len(result["npcperformer"]), 1)

        room = self.world.get_map("map-room-0001")
        self.assertFalse(any(item.content == "应被回滚的状态补丁" for item in room.description.add))


if __name__ == "__main__":
    unittest.main()
