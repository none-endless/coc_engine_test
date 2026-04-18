import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Dict, Type

from pydantic import BaseModel

from src.config.loader import ConfigLoader
from src.data.model.agent_output import (
    ConsistencyAgentLlmOutput,
    DmAgentLlmOutput,
    EvolutionAgentLlmOutput,
    MergerAgentLlmOutput,
    NarrativeAgentLlmOutput,
    NpcSchedulerAgentLlmOutput,
    StateAgentLlmOutput,
)
from src.data.model.base import (
    Attribute,
    CharacterEntity,
    Description,
    MapEntity,
    MemoryForNpc,
    ShortLogItem,
    Status,
    WorldEntityStore,
)
from src.data.model.input.agent_narrative_input import NarrativeEntry
from src.data.model.world_state import WorldState
from src.engine.engine import Engine


class Phase6FakeLLMService:
    """用于 phase6 闭环测试的假 LLM 服务。"""

    def __init__(self, *, consistency_payload: Dict[str, Any] | None = None, add_interval: int = 2) -> None:
        self.config = ConfigLoader.load(
            cli_overrides={
                "system.max_retry_count": 1,
                "description.add_interval": add_interval,
                "agent.narrative.recent_turns": 5,
            }
        )
        self.consistency_payload = consistency_payload or {
            "summary_items": [{"kind": "narration", "value": "本回合无新增叙事。"}],
            "can_proceed": True,
            "system_message": "",
        }

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
                        "intent": "observe",
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
                    "summary": "玩家保持警惕，环境里出现了新的异常痕迹。",
                    "visible_to_player": True,
                }
            )
        if output_model is NarrativeAgentLlmOutput:
            return output_model.model_validate({"narrative_str": "你注意到房间里多了一道新留下的擦痕。"})
        if output_model is MergerAgentLlmOutput:
            return output_model.model_validate({"narrative_str": "房间里出现了新的擦痕，局势变得更可疑。"})
        if output_model is NpcSchedulerAgentLlmOutput:
            return output_model.model_validate(
                {"step_result": {"summary": "本回合无 NPC 动作", "scheduled_npc_ids": [], "extra_npc_context": {}}}
            )
        if output_model is StateAgentLlmOutput:
            return output_model.model_validate(
                {
                    "changes": [
                        {
                            "op": "ADD",
                            "target_path": "map-room-0001.description.add",
                            "value": ["墙面上多了一道新擦痕"],
                        }
                    ]
                }
            )
        if output_model is ConsistencyAgentLlmOutput:
            return output_model.model_validate(self.consistency_payload)
        raise AssertionError(f"unsupported output model: {output_model}")


class TestPhase6ConsistencyAgent(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.world_db_path = str(Path(self.temp_dir.name) / "world.sqlite3")
        self.narrative_db_path = str(Path(self.temp_dir.name) / "narrative.sqlite3")

        room = MapEntity(
            id="map-room-0001",
            name="房间",
            description=Description(public=["一间安静的房间。"]),
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
            attributes={"dexterity": Attribute(id="dexterity", name="敏捷", value=60, max_value=100, min_value=0)},
            status={"health": Status(id="health", name="生命", value=10, max_value=10, min_value=0)},
        )
        guard = CharacterEntity(
            id="char-guard-0001",
            name="守卫",
            location=room.id,
            attributes={"dexterity": Attribute(id="dexterity", name="敏捷", value=50, max_value=100, min_value=0)},
            status={"health": Status(id="health", name="生命", value=10, max_value=10, min_value=0)},
            memory=MemoryForNpc(
                short_log=[
                    ShortLogItem(turn=1, event="守卫听到房间内的异常声响"),
                    ShortLogItem(turn=2, event="守卫开始调查墙边的新痕迹"),
                ]
            ),
        )
        self.world = WorldState()
        self.world.reset(
            WorldEntityStore(
                maps={room.id: room, hall.id: hall},
                characters={player.id: player, guard.id: guard},
                items={},
            )
        )

    def _build_engine(self, *, consistency_payload: Dict[str, Any] | None = None) -> Engine:
        service = Phase6FakeLLMService(consistency_payload=consistency_payload, add_interval=2)
        service.config = ConfigLoader.load(
            cli_overrides={
                "system.max_retry_count": 1,
                "description.add_interval": 2,
                "description.merge_threshold": 0,
                "agent.narrative.recent_turns": 5,
                "agent.npc.shortlog_merge_threshold": 1,
                "storage.world.sqlite_path": self.world_db_path,
                "storage.narrative.sqlite_path": self.narrative_db_path,
            }
        )
        service.consistency_payload = consistency_payload or service.consistency_payload
        return Engine(world_state=self.world, mode="phase3", llm_service=service)

    def test_consistency_trigger_merges_description_and_maintains_memory(self):
        engine = self._build_engine(
            consistency_payload={
                "summary_items": [
                    {
                        "kind": "narration",
                        "value": "房间里出现了新的擦痕，守卫开始调查。局势进入紧张阶段。",
                    },
                    {
                        "kind": "description",
                        "value": "墙面上留下了新擦痕，房间不再如最初那般整洁。",
                    },
                    {
                        "kind": "key_facts",
                        "value": "守卫确认房间出现异常擦痕并已启动调查。",
                    },
                ],
                "can_proceed": True,
                "system_message": "",
            }
        )
        engine._narrative_info.recent = [
            NarrativeEntry(turn=0, content="你觉得房间很安静。"),
            NarrativeEntry(turn=1, content="空气里隐约有灰尘被扰动。"),
        ]

        result = engine.run_turn(
            raw_input="我观察房间",
            actor_id="char-player-0000",
            turn_id=2,
            trace_id=2002,
        )

        updated_room = self.world.get_map("map-room-0001")
        self.assertEqual(updated_room.description.public, ["墙面上留下了新擦痕，房间不再如最初那般整洁。"])
        self.assertEqual(updated_room.description.add, [])
        self.assertIsNotNone(result["consistency"])
        self.assertTrue(result["consistency"]["ok"])
        self.assertEqual(len(result["narrative_info"]["recent"]), 1)
        updated_guard = self.world.get_character("char-guard-0001")
        self.assertEqual(updated_guard.memory.key_facts, ["守卫确认房间出现异常擦痕并已启动调查。"])
        self.assertEqual(updated_guard.memory.short_log, [])
        self.assertEqual(result["narrative_info"]["recent"][-1]["content"], "房间里出现了新的擦痕，守卫开始调查。局势进入紧张阶段。")

    def test_consistency_can_block_and_stop_following_turns(self):
        engine = self._build_engine(
            consistency_payload={
                "summary_items": [
                    {
                        "kind": "narration",
                        "value": "当前快照出现冲突，已暂停后续流程。",
                    }
                ],
                "can_proceed": False,
                "system_message": "一致性冲突，需人工检查。",
            }
        )

        blocked_turn = engine.run_turn(
            raw_input="我观察房间",
            actor_id="char-player-0000",
            turn_id=2,
            trace_id=2202,
        )

        self.assertIsNotNone(blocked_turn["consistency"])
        self.assertTrue(blocked_turn["consistency"]["blocked"])
        self.assertEqual(blocked_turn["fallback_error"]["code"], "CONSISTENCY_BLOCKED")
        self.assertTrue(blocked_turn["terminated"])

        next_turn = engine.run_turn(
            raw_input="我继续前进",
            actor_id="char-player-0000",
            turn_id=3,
            trace_id=2203,
        )

        self.assertEqual(next_turn["route"], "consistency_blocked")
        self.assertEqual(next_turn["message"], "一致性冲突，需人工检查。")
        self.assertTrue(next_turn["terminated"])

    def test_consistency_skips_when_not_on_trigger_turn(self):
        engine = self._build_engine()

        result = engine.run_turn(
            raw_input="我观察房间",
            actor_id="char-player-0000",
            turn_id=1,
            trace_id=2301,
        )

        self.assertIsNone(result["consistency"])


if __name__ == "__main__":
    unittest.main()
