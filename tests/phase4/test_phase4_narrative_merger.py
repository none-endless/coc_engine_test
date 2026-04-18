import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
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
from src.storage.sqlite_world_snapshot_repository import SqliteWorldSnapshotRepository


class Phase4FakeLLMService:
    """用于 Phase4 闭环验证的假 LLM 服务。"""

    def __init__(self, cli_overrides: Dict[str, Any] | None = None) -> None:
        overrides = {
            "agent.narrative.recent_turns": 5,
            "system.max_retry_count": 1,
        }
        overrides.update(cli_overrides or {})
        self.config = ConfigLoader.load(
            cli_overrides=overrides
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
                {"step_result": {"summary": "本回合无 NPC 动作", "scheduled_npc_ids": [], "extra_npc_context": {}}}
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
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.world_db_path = str(Path(self.temp_dir.name) / "world_snapshots.sqlite3")
        self.narrative_db_path = str(Path(self.temp_dir.name) / "narrative_truth.sqlite3")
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
                "dexterity": Attribute(id="dexterity", name="敏捷", value=60, max_value=100, min_value=0),
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

    def _build_service(self) -> Phase4FakeLLMService:
        return Phase4FakeLLMService(
            cli_overrides={
                "storage.world.sqlite_path": self.world_db_path,
                "storage.narrative.sqlite_path": self.narrative_db_path,
            }
        )

    def test_merger_commits_compact_narrative_into_recent_pool(self):
        engine = Engine(world_state=self.world, mode="phase3", llm_service=self._build_service())
        result = engine.run_turn(
            raw_input="我走向走廊",
            actor_id="char-player-0000",
            turn_id=4,
            trace_id=4004,
        )

        self.assertEqual(result["narrative"]["llm_output"]["narrative_str"], "他推门走出房间，走廊里的冷风立刻扑了上来。")
        self.assertEqual(result["merger"]["llm_output"]["narrative_str"], "他离开房间，走入了走廊。")
        self.assertEqual(result["narrative_info"]["recent"][-1]["content"], "他离开房间，走入了走廊。")
        self.assertEqual(self.world.get_character("char-player-0000").location, "map-hall-0002")
        self.assertTrue(result["narrative"]["stream_transport"]["sse"])
        self.assertTrue(result["narrative"]["stream_transport"]["websocket"])
        self.assertTrue(Path(self.world_db_path).exists())
        self.assertTrue(Path(self.narrative_db_path).exists())
        latest_snapshot = SqliteWorldSnapshotRepository(self.world_db_path).load_latest_snapshot()
        self.assertIsNotNone(latest_snapshot)
        self.assertEqual(latest_snapshot["version"], self.world.get_snapshot()["version"])

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
                            "summary": "远处有什么东西轻轻移动了一下。",
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

        engine = Engine(
            world_state=self.world,
            mode="phase3",
            llm_service=InvisibleLLMService(
                cli_overrides={
                    "storage.world.sqlite_path": self.world_db_path,
                    "storage.narrative.sqlite_path": self.narrative_db_path,
                }
            ),
        )
        result = engine.run_turn(
            raw_input="我在原地等待",
            actor_id="char-player-0000",
            turn_id=5,
            trace_id=5005,
        )

        self.assertFalse(result["narrative_triggered"])
        self.assertEqual(result["narrative"]["llm_output"]["narrative_str"], "")
        self.assertIsNotNone(result["merger"])
        self.assertEqual(result["narrative_info"]["recent"][-1]["content"], "他离开房间，走入了走廊。")

    def test_engine_restores_narrative_info_from_independent_sqlite_repository(self):
        first_engine = Engine(world_state=self.world, mode="phase3", llm_service=self._build_service())
        first_engine.run_turn(
            raw_input="我走向走廊",
            actor_id="char-player-0000",
            turn_id=4,
            trace_id=4004,
        )

        second_engine = Engine(world_state=self.world, mode="phase3", llm_service=self._build_service())
        self.assertTrue(second_engine._narrative_info.recent)
        self.assertEqual(second_engine._narrative_info.recent[-1].content, "他离开房间，走入了走廊。")


if __name__ == "__main__":
    unittest.main()
