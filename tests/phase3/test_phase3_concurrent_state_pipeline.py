import copy
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
    PatchMeta,
    StateAgentLlmOutput,
    StateAgentOutput,
)
from src.data.model.base import (
    Attribute,
    CharacterEntity,
    Description,
    DescriptionAddItem,
    ExtensionSchemaItem,
    ExtensionSchemaRegistry,
    MapConnection,
    MapEntity,
    WorldEntityStore,
)
from src.data.model.world_state import WorldState
from src.engine.bootstrap_validation import EngineBootstrapError
from src.engine.engine import Engine
from src.rule.state_patch import ERROR_FIELD_NOT_MUTABLE, StatePatchError, StatePatchRuntime


class FakeLLMService:
    def __init__(self, always_invalid_state_patch: bool = False) -> None:
        self.always_invalid_state_patch = always_invalid_state_patch
        self.config = ConfigLoader.load(
            cli_overrides={
                "system.max_retry_count": 2,
                "system.retry_timeout_ms": 5000,
                "system.fallback_error": "系统繁忙，请稍后重试",
            }
        )

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
        if output_model is DmAgentLlmOutput:
            return output_model.model_validate(
                {
                    "intent_info": {
                        "intent": "move",
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
                    "summary": "char-player-0000 从 map-room-0001 移动到 map-hall-0002",
                    "visible_to_player": True,
                }
            )

        if output_model is NpcSchedulerAgentLlmOutput:
            return output_model.model_validate(
                {
                    "step_result": {
                        "summary": "本回合无 NPC 激活",
                        "scheduled_npc_ids": [],
                        "extra_npc_context": {},
                    }
                }
            )

        if output_model is NpcPerformerAgentLlmOutput:
            return output_model.model_validate(
                {
                    "intent": "description",
                    "action_text": "守卫握紧武器，继续保持警惕。",
                    "routing_hint": None,
                    "attributes": [],
                    "against_char_id": [],
                    "difficulty": None,
                    "change_basic_goal": None,
                    "change_active_goal": "继续警戒",
                }
            )

        if output_model is NarrativeAgentLlmOutput:
            return output_model.model_validate(
                {
                    "narrative_str": "你推门离开房间，走廊里的冷风迎面扑来。",
                }
            )

        if output_model is MergerAgentLlmOutput:
            return output_model.model_validate(
                {
                    "narrative_str": "你离开房间，走入了走廊。",
                }
            )

        if output_model is StateAgentLlmOutput:
            if self.always_invalid_state_patch:
                return output_model.model_validate(
                    {
                        "changes": [
                            {
                                "op": "SET",
                                "target_path": "char-player-0000.description.public",
                                "value": ["非法直接写 public"],
                            }
                        ]
                    }
                )
            return output_model.model_validate(
                {
                    "changes": [
                        {
                            "op": "MOVE",
                            "target_path": "char-player-0000.location",
                            "value": "map-hall-0002",
                        }
                    ]
                }
            )

        raise AssertionError(f"unsupported output model: {output_model}")


class TestPhase3ConcurrentStatePipeline(unittest.TestCase):
    def setUp(self) -> None:
        room = MapEntity(
            id="map-room-0001",
            name="房间",
            description=Description(public=["一间狭小的房间"]),
            connections=[
                MapConnection(
                    id="conn-east-0001",
                    name="东侧木门",
                    direction="east",
                    description="通往走廊",
                    is_locked=False,
                )
            ],
        )
        hall = MapEntity(
            id="map-hall-0002",
            name="走廊",
            description=Description(public=["狭长阴冷的走廊"]),
        )
        player = CharacterEntity(
            id="char-player-0000",
            name="玩家",
            location=room.id,
            attributes={
                "dexterity": Attribute(id="dexterity", name="敏捷", value=70, max_value=100, min_value=0),
                "health": Attribute(id="health", name="生命", value=10, max_value=10, min_value=0),
            },
        )

        world = WorldState()
        world.reset(
            WorldEntityStore(
                maps={room.id: room, hall.id: hall},
                characters={player.id: player},
                items={},
            )
        )
        self.world = world

    def test_parallel_branches_share_same_turn_id(self):
        engine = Engine(world_state=self.world, mode="phase3", llm_service=FakeLLMService())
        result = engine.run_turn(
            raw_input="我走向走廊",
            actor_id="char-player-0000",
            turn_id=7,
            trace_id=7001,
        )

        self.assertEqual(result["route"], "phase3_concurrent_nl")
        self.assertEqual(result["npcscheduler"]["system_output"]["turn_id"], 7)
        self.assertEqual(result["narrative"]["system_output"]["turn_id"], 7)
        self.assertEqual(result["state"]["patch"]["system_output"]["patch_meta"]["turn_id"], 7)
        self.assertEqual(result["merger"]["system_output"]["turn_id"], 7)

        branches = {x["branch"] for x in result["parallel_timeline"]}
        self.assertEqual(branches, {"npc_scheduler", "state", "narrative", "merger"})
        self.assertEqual(result["narrative"]["llm_output"]["narrative_str"], "你推门离开房间，走廊里的冷风迎面扑来。")
        self.assertTrue(result["narrative"]["stream_events"])
        self.assertEqual(result["merger"]["llm_output"]["narrative_str"], "你离开房间，走入了走廊。")

    def test_set_description_public_and_char_index_are_blocked(self):
        runtime = StatePatchRuntime(world_state=self.world)
        expected_version = int(self.world.get_snapshot()["version"])

        patch_public = StateAgentOutput.model_validate(
            {
                "llm_output": {
                    "changes": [
                        {
                            "op": "SET",
                            "target_path": "char-player-0000.description.public",
                            "value": ["非法"],
                        }
                    ]
                },
                "system_output": {
                    "patch_meta": {
                        "trace_id": 1,
                        "turn_id": 1,
                        "retry_seq": 0,
                        "patch_id": "p1",
                        "expected_version": expected_version,
                    }
                },
            }
        )

        with self.assertRaises(StatePatchError) as public_err:
            runtime.apply_patch(patch_output=patch_public)
        self.assertEqual(public_err.exception.code, ERROR_FIELD_NOT_MUTABLE)

        patch_index = StateAgentOutput(
            llm_output=StateAgentLlmOutput.model_validate(
                {
                    "changes": [
                        {
                            "op": "SET",
                            "target_path": "map-room-0001.char_index",
                            "value": ["char-player-0000"],
                        }
                    ]
                }
            ),
            system_output={"patch_meta": PatchMeta(trace_id=2, turn_id=1, retry_seq=0, patch_id="p2", expected_version=expected_version)},
        )

        with self.assertRaises(StatePatchError) as idx_err:
            runtime.apply_patch(patch_output=patch_index)
        self.assertEqual(idx_err.exception.code, ERROR_FIELD_NOT_MUTABLE)

    def test_extension_registry_mutable_is_enforced(self):
        world = WorldState()
        room = MapEntity(
            id="map-room-0001",
            name="房间",
            description=Description(public=["一间狭小的房间"]),
            extensions={"quest.stage": "locked"},
        )
        player = CharacterEntity(
            id="char-player-0000",
            name="玩家",
            location=room.id,
            attributes={"dexterity": Attribute(id="dexterity", name="敏捷", value=60, max_value=100, min_value=0)},
        )
        world.reset(
            WorldEntityStore(
                maps={room.id: room},
                characters={player.id: player},
                items={},
                extension_registry=ExtensionSchemaRegistry(
                    fields={
                        "quest.stage": ExtensionSchemaItem(
                            key="quest.stage",
                            mutable=False,
                            value_type="string",
                        )
                    }
                ),
            )
        )
        runtime = StatePatchRuntime(world_state=world)
        expected_version = int(world.get_snapshot()["version"])

        patch = StateAgentOutput.model_validate(
            {
                "llm_output": {
                    "changes": [
                        {
                            "op": "SET",
                            "target_path": "map-room-0001.extensions.quest.stage",
                            "value": "opened",
                        }
                    ]
                },
                "system_output": {
                    "patch_meta": {
                        "trace_id": 3,
                        "turn_id": 1,
                        "retry_seq": 0,
                        "patch_id": "p3",
                        "expected_version": expected_version,
                    }
                },
            }
        )

        with self.assertRaises(StatePatchError) as ext_err:
            runtime.apply_patch(patch_output=patch)
        self.assertEqual(ext_err.exception.code, ERROR_FIELD_NOT_MUTABLE)

    def test_three_failures_trigger_rollback_and_fallback(self):
        engine = Engine(world_state=self.world, mode="phase3", llm_service=FakeLLMService(always_invalid_state_patch=True))
        before = copy.deepcopy(self.world.get_snapshot())

        result = engine.run_turn(
            raw_input="我走向走廊",
            actor_id="char-player-0000",
            turn_id=8,
            trace_id=8001,
        )

        after = copy.deepcopy(self.world.get_snapshot())
        self.assertFalse(result["state"]["ok"])
        self.assertEqual(result["state"]["retry_count"], 2)
        self.assertEqual(result["state"]["fallback_error"]["code"], "STATE_PATCH_RETRY_EXHAUSTED")
        self.assertEqual(result["state"]["fallback_error"]["message"], "系统繁忙，请稍后重试")
        self.assertTrue(result["state"]["fallback_error"]["rollback_applied"])
        self.assertTrue(result["terminated"])
        self.assertFalse(result["narrative_triggered"])
        self.assertIsNone(result["merger"])
        self.assertEqual(before["version"], after["version"])
        self.assertEqual(before["maps"], after["maps"])
        self.assertEqual(before["characters"], after["characters"])
        self.assertEqual(before["items"], after["items"])

    def test_state_rollback_skips_npc_performer_side_effects(self):
        room = MapEntity(
            id="map-room-0001",
            name="房间",
            description=Description(public=["一间狭小的房间"]),
        )
        player = CharacterEntity(
            id="char-player-0000",
            name="玩家",
            location=room.id,
            attributes={
                "dexterity": Attribute(id="dexterity", name="敏捷", value=70, max_value=100, min_value=0),
                "health": Attribute(id="health", name="生命", value=10, max_value=10, min_value=0),
            },
        )
        guard = CharacterEntity(
            id="char-guard-0001",
            name="守卫",
            location=room.id,
            attributes={
                "dexterity": Attribute(id="dexterity", name="敏捷", value=55, max_value=100, min_value=0),
                "health": Attribute(id="health", name="生命", value=10, max_value=10, min_value=0),
            },
        )
        self.world.reset(
            WorldEntityStore(
                maps={room.id: room},
                characters={player.id: player, guard.id: guard},
                items={},
            )
        )

        class RollbackNpcLLMService(FakeLLMService):
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
                                "summary": "守卫被调度，需要检查周围动静。",
                                "scheduled_npc_ids": ["char-guard-0001"],
                                "extra_npc_context": {"char-guard-0001": "你听见附近有异常声响。"},
                            }
                        }
                    )
                if output_model is NpcPerformerAgentLlmOutput:
                    return output_model.model_validate(
                        {
                            "intent": "description",
                            "action_text": "守卫立刻靠近门口，准备应对威胁。",
                            "routing_hint": None,
                            "attributes": [],
                            "against_char_id": [],
                            "difficulty": None,
                            "change_basic_goal": None,
                            "change_active_goal": "调查门口异常",
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

        engine = Engine(
            world_state=self.world,
            mode="phase3",
            llm_service=RollbackNpcLLMService(always_invalid_state_patch=True),
        )
        result = engine.run_turn(
            raw_input="我走向门口",
            actor_id="char-player-0000",
            turn_id=13,
            trace_id=13001,
        )

        updated_guard = self.world.get_character("char-guard-0001")
        self.assertTrue(result["terminated"])
        self.assertEqual(result["npcperformer"], [])
        self.assertEqual(result["npc_performer_chain"], [])
        self.assertEqual(updated_guard.goal.active_goal, "")
        self.assertIsNone(updated_guard.memory.current_event)
        self.assertEqual(updated_guard.memory.short, [])
        self.assertEqual(updated_guard.memory.short_log, [])
        self.assertEqual(updated_guard.memory.log, [])

    def test_description_add_string_is_coerced_by_system(self):
        runtime = StatePatchRuntime(world_state=self.world)
        expected_version = int(self.world.get_snapshot()["version"])
        patch = StateAgentOutput.model_validate(
            {
                "llm_output": {
                    "changes": [
                        {
                            "op": "ADD",
                            "target_path": "char-player-0000.description.add",
                            "value": ["你听见门外传来细碎脚步声。"],
                        }
                    ]
                },
                "system_output": {
                    "patch_meta": {
                        "trace_id": 9,
                        "turn_id": 12,
                        "retry_seq": 0,
                        "patch_id": "p9",
                        "expected_version": expected_version,
                    }
                },
            }
        )

        runtime.apply_patch(patch_output=patch)
        updated = self.world.get_character("char-player-0000")
        self.assertEqual(
            updated.description.add[0],
            DescriptionAddItem(turn=12, content="你听见门外传来细碎脚步声。"),
        )

    def test_dm_reply_short_circuits_concurrent_pipeline(self):
        class DirectReplyLLMService(FakeLLMService):
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
                if output_model is DmAgentLlmOutput:
                    return output_model.model_validate(
                        {
                            "intent_info": {
                                "intent": "talk",
                                "routing_hint": None,
                                "attributes": [],
                                "against_char_id": [],
                                "difficulty": None,
                                "dm_reply": "守卫皱了皱眉，示意你先别靠近。",
                            }
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

        engine = Engine(world_state=self.world, mode="phase3", llm_service=DirectReplyLLMService())
        result = engine.run_turn(
            raw_input="我和守卫搭话",
            actor_id="char-player-0000",
            turn_id=9,
            trace_id=9001,
        )

        self.assertEqual(result["route"], "dm_direct_reply")
        self.assertEqual(result["reply"], "守卫皱了皱眉，示意你先别靠近。")
        self.assertFalse(result["narrative_triggered"])
        self.assertNotIn("state", result)

    def test_narrative_recent_is_capped_and_state_branch_cannot_read_drafts(self):
        class CaptureLLMService(FakeLLMService):
            def __init__(self) -> None:
                super().__init__()
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
                return super().call_llm_json(
                    agent_name=agent_name,
                    system_prompt=system_prompt,
                    user_payload=user_payload,
                    output_model=output_model,
                    retry_budget=retry_budget,
                    validation_feedback=validation_feedback,
                )

        service = CaptureLLMService()
        engine = Engine(world_state=self.world, mode="phase3", llm_service=service)

        for turn_id in range(1, 7):
            engine.run_turn(
                raw_input="我走向走廊",
                actor_id="char-player-0000",
                turn_id=turn_id,
                trace_id=1000 + turn_id,
            )

        self.assertNotIn("narrative_info", service.payloads["state_change"])
        self.assertIn("e7", service.payloads["merger"])
        self.assertEqual(len(engine._narrative_info.recent), 5)
        self.assertGreaterEqual(len(engine._narrative_info.narrative_log), 2)

    def test_missing_dexterity_blocks_engine_bootstrap(self):
        room = MapEntity(
            id="map-room-0001",
            name="房间",
            description=Description(public=["空房间"]),
        )
        player = CharacterEntity(
            id="char-player-0000",
            name="玩家",
            location=room.id,
            attributes={"health": Attribute(id="health", name="生命", value=10, max_value=10, min_value=0)},
        )
        world = WorldState()
        world.reset(
            WorldEntityStore(
                maps={room.id: room},
                characters={player.id: player},
                items={},
            )
        )

        with self.assertRaises(EngineBootstrapError) as exc:
            Engine(world_state=world, mode="phase3", llm_service=FakeLLMService())

        self.assertIn("敏捷", str(exc.exception))


if __name__ == "__main__":
    unittest.main()
