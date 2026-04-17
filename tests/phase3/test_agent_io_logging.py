import json
import tempfile
import unittest
from pathlib import Path

from pydantic import BaseModel

from src.config.loader import ConfigLoader
from src.data.model.agent_output import DmAgentLlmOutput, EvolutionAgentLlmOutput, NarrativeAgentLlmOutput, NpcSchedulerAgentLlmOutput, StateAgentLlmOutput
from src.data.model.base import Attribute, CharacterEntity, Description, MapEntity, WorldEntityStore
from src.data.model.world_state import WorldState
from src.engine.engine import Engine
from src.utils.agent_io_logger import AgentIoLogger


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
                    "summary": f"玩家执行了行动: {raw_text}",
                    "visible_to_player": True,
                }
            )

        if output_model is NpcSchedulerAgentLlmOutput:
            return output_model.model_validate(
                {
                    "step_result": {
                        "summary": "无 NPC 激活",
                        "extra_npc_context": {},
                    }
                }
            )

        if output_model is NarrativeAgentLlmOutput:
            return output_model.model_validate(
                {
                    "narrative_str": "你迈步离开房间，走廊的冷风迎面而来。",
                    "narrative_draft": None,
                }
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


class TestAgentIoLogging(unittest.TestCase):
    def test_phase3_writes_agent_io_log(self):
        room = MapEntity(
            id="map-room-0001",
            name="房间",
            description=Description(public=["一间狭小的房间"]),
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

        with tempfile.TemporaryDirectory() as temp_dir:
            log_dir = Path(temp_dir) / "log"
            logger = AgentIoLogger(log_dir)
            engine = Engine(world_state=world, mode="phase3", llm_service=FakeLLMService(), io_logger=logger)
            engine.run_turn(
                raw_input="我走向走廊",
                actor_id="char-player-0000",
                turn_id=7,
                trace_id=7001,
            )

            log_path = log_dir / "agent_io.jsonl"
            self.assertTrue(log_path.exists())

            records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            agent_names = {record.get("agent_name") for record in records if record.get("kind") == "agent_io"}
            self.assertTrue({"dmagent", "evolution", "npc_scheduler", "narrative", "state_change"}.issubset(agent_names))
            turn_kinds = [record for record in records if record.get("kind") == "turn_result"]
            self.assertTrue(turn_kinds)


if __name__ == "__main__":
    unittest.main()