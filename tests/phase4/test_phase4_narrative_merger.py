import unittest
from typing import Any, Dict, Type

from pydantic import BaseModel

from src.config.loader import ConfigLoader
from src.data.model.agent_output import (
    DmAgentLlmOutput,
    EvolutionAgentLlmOutput,
    MergerAgentLlmOutput,
    NarrativeAgentLlmOutput,
    NpcSchedulerAgentLlmOutput,
    StateAgentLlmOutput,
)
from src.data.model.base import Attribute, CharacterEntity, Description, MapEntity, WorldEntityStore
from src.data.model.world_state import WorldState
from src.engine.engine import Engine


class Phase4FakeLLMService:
    """用于 Phase4 闭环验证的假 LLM 服务。"""

    def __init__(self) -> None:
        self.config = ConfigLoader.load(
            cli_overrides={
                "agent.narrative.recent_turns": 5,
                "system.max_retry_count": 1,
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
                    "summary": "char-player-0000 离开房间，进入走廊",
                    "visible_to_player": True,
                }
            )
        if output_model is NarrativeAgentLlmOutput:
            return output_model.model_validate(
                {
                    "narrative_str": "他推门走出房间，走廊里的冷风立刻扑了上来。",
                    "narrative_draft": None,
                }
            )
        if output_model is MergerAgentLlmOutput:
            return output_model.model_validate(
                {
                    "narrative_str": "他离开房间，走入了走廊。",
                }
            )
        if output_model is NpcSchedulerAgentLlmOutput:
            return output_model.model_validate(
                {"step_result": {"summary": "本回合无 NPC 动作", "extra_npc_context": {}}}
            )
        if output_model is StateAgentLlmOutput:
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


class TestPhase4NarrativeMerger(unittest.TestCase):
    def setUp(self) -> None:
        room = MapEntity(
            id="map-room-0001",
            name="房间",
            description=Description(public=["一间狭小的房间。"]),
        )
        hall = MapEntity(
            id="map-hall-0002",
            name="走廊",
            description=Description(public=["一条狭长的走廊。"]),
        )
        player = CharacterEntity(
            id="char-player-0000",
            name="玩家",
            location=room.id,
            attributes={
                "health": Attribute(id="health", name="生命", value=10, max_value=10, min_value=0),
            },
        )
        self.world = WorldState()
        self.world.reset(
            WorldEntityStore(
                maps={room.id: room, hall.id: hall},
                characters={player.id: player},
                items={},
            )
        )

    def test_merger_commits_compact_narrative_into_recent_pool(self):
        engine = Engine(world_state=self.world, mode="phase3", llm_service=Phase4FakeLLMService())
        result = engine.run_turn(
            raw_input="我走向走廊",
            actor_id="char-player-0000",
            turn_id=4,
            trace_id=4004,
        )

        self.assertEqual(result["narrative"]["llm_output"]["narrative_str"], "他推门走出房间，走廊里的冷风立刻扑了上来。")
        self.assertEqual(result["merger"]["llm_output"]["narrative_str"], "他离开房间，走入了走廊。")
        self.assertEqual(result["narrative"]["llm_output"]["narrative_draft"]["status"], "committed")
        self.assertEqual(result["narrative_info"]["recent"][-1]["content"], "他离开房间，走入了走廊。")
        self.assertEqual(self.world.get_character("char-player-0000").location, "map-hall-0002")

    def test_invisible_evolution_does_not_generate_narrative_or_merger(self):
        class InvisibleLLMService(Phase4FakeLLMService):
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
                if output_model is EvolutionAgentLlmOutput:
                    return output_model.model_validate(
                        {
                            "summary": "远处有什么东西轻轻移动了一下",
                            "visible_to_player": False,
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

        engine = Engine(world_state=self.world, mode="phase3", llm_service=InvisibleLLMService())
        result = engine.run_turn(
            raw_input="我在原地等待",
            actor_id="char-player-0000",
            turn_id=5,
            trace_id=5005,
        )

        self.assertFalse(result["narrative_triggered"])
        self.assertEqual(result["narrative"]["llm_output"]["narrative_str"], "")
        self.assertIsNone(result["narrative"]["llm_output"]["narrative_draft"])
        self.assertIsNone(result["merger"])
        self.assertEqual(result["narrative_info"]["recent"], [])


if __name__ == "__main__":
    unittest.main()
