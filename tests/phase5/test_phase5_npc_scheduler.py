import unittest
from typing import Any, Dict, Type

from pydantic import BaseModel

from src.config.loader import ConfigLoader
from src.data.model.agent_input import (
    AgentIdentity,
    E4EvolutionLlmView,
    NpcSchedulerAgentInput,
    NpcSchedulerAgentLlmInput,
    NpcSchedulerAgentSystemInput,
    SystemExecutionMeta,
)
from src.data.model.agent_output import NpcSchedulerAgentLlmOutput
from src.data.model.base import Attribute, CharacterEntity, Description, MapConnection, MapEntity, Status, WorldEntityStore
from src.data.model.input.agent_chain_input import E4EvolutionStepResult, NpcSchedulerAgentChainInput
from src.data.model.input.agent_map_intput import NpcSchedulerWorldView
from src.data.model.input.agent_narrative_input import NarrativeInfo
from src.data.model.world_state import WorldState
from src.agent.llm.npc_schedul_agent import NpcSchedulerAgent
from src.engine.turn_orchestrator import TurnOrchestrator
from src.utils.world_provider import WorldDataProvider


class SchedulerFakeLLMService:
    def __init__(self, scheduled_npc_ids, extra_npc_context) -> None:
        self.config = ConfigLoader.load()
        self._scheduled_npc_ids = list(scheduled_npc_ids)
        self._extra_npc_context = dict(extra_npc_context)
        self.last_payload: Dict[str, Any] = {}

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
        self.last_payload = user_payload
        return output_model.model_validate(
            {
                "step_result": {
                    "summary": "phase5 scheduler test",
                    "scheduled_npc_ids": self._scheduled_npc_ids,
                    "extra_npc_context": self._extra_npc_context,
                }
            }
        )


class TestPhase5NpcScheduler(unittest.TestCase):
    def setUp(self) -> None:
        room = MapEntity(
            id="map-room-0001",
            name="房间",
            description=Description(public=["测试房间"]),
        )
        npc_a = CharacterEntity(
            id="char-fast-0001",
            name="快手",
            location=room.id,
            attributes={"dexterity": Attribute(id="dexterity", name="敏捷", value=90, max_value=100, min_value=0)},
            status={"health": Status(id="health", name="生命", value=10, max_value=10, min_value=0)},
        )
        npc_b = CharacterEntity(
            id="char-mid-0002",
            name="中速",
            location=room.id,
            attributes={"dexterity": Attribute(id="dexterity", name="敏捷", value=60, max_value=100, min_value=0)},
            status={"health": Status(id="health", name="生命", value=10, max_value=10, min_value=0)},
        )
        npc_c = CharacterEntity(
            id="char-slow-0003",
            name="慢手",
            location=room.id,
            attributes={"dexterity": Attribute(id="dexterity", name="敏捷", value=30, max_value=100, min_value=0)},
            status={"health": Status(id="health", name="生命", value=10, max_value=10, min_value=0)},
        )
        npc_dead = CharacterEntity(
            id="char-dead-0004",
            name="倒地者",
            location=room.id,
            attributes={"dexterity": Attribute(id="dexterity", name="敏捷", value=99, max_value=100, min_value=0)},
            status={"health": Status(id="health", name="生命", value=0, max_value=10, min_value=0)},
        )
        npc_custom_zero = CharacterEntity(
            id="char-custom_zero-0005",
            name="疲惫者",
            location=room.id,
            attributes={"dexterity": Attribute(id="dexterity", name="敏捷", value=95, max_value=100, min_value=0)},
            status={
                "health": Status(id="health", name="生命", value=10, max_value=10, min_value=0),
                "fatigue": Status(id="fatigue", name="疲劳", value=0, max_value=10, min_value=0),
            },
        )

        self.world = WorldState()
        self.world.reset(
            WorldEntityStore(
                maps={room.id: room},
                characters={
                    npc_a.id: npc_a,
                    npc_b.id: npc_b,
                    npc_c.id: npc_c,
                    npc_dead.id: npc_dead,
                    npc_custom_zero.id: npc_custom_zero,
                },
                items={},
            )
        )

    def _build_input(self) -> NpcSchedulerAgentInput:
        return NpcSchedulerAgentInput(
            identity=AgentIdentity(id="npcscheduler", skill="schedule npc branch"),
            llm_input=NpcSchedulerAgentLlmInput(
                e4=E4EvolutionLlmView(summary="test"),
                world_info=NpcSchedulerWorldView.model_validate(
                    {
                        "current_map": {
                            "map_id": "map-room-0001",
                            "map_name": "房间",
                            "description": {"public": ["测试房间"], "add": []},
                            "connections": [],
                            "characters": [
                                {"id": "char-fast-0001", "name": "蹇墜", "basic_info": "", "description": {"public": [], "add": []}},
                                {"id": "char-mid-0002", "name": "涓€?", "basic_info": "", "description": {"public": [], "add": []}},
                                {"id": "char-slow-0003", "name": "鎱㈡墜", "basic_info": "", "description": {"public": [], "add": []}},
                            ],
                            "items": [],
                        },
                        "adjacent_maps": [],
                        "player_location": "map-room-0001",
                    }
                ),
                narrative_info=NarrativeInfo(),
                allowed_npc_ids=["char-fast-0001", "char-mid-0002", "char-slow-0003"],
            ),
            system_input=NpcSchedulerAgentSystemInput(
                chain_raw=NpcSchedulerAgentChainInput(e4=E4EvolutionStepResult(summary="test")),
                execution=SystemExecutionMeta(turn_id=3, trace_id=3001),
            ),
        )

    def test_scheduler_sorts_by_dexterity_and_applies_action_budget(self):
        service = SchedulerFakeLLMService(
            scheduled_npc_ids=["char-mid-0002", "char-slow-0003", "char-fast-0001"],
            extra_npc_context={
                "char-mid-0002": "mid",
                "char-slow-0003": "slow",
                "char-fast-0001": "fast",
            },
        )
        agent = NpcSchedulerAgent(
            llm_service=service,
            world_state=self.world,
            max_actions_per_turn=2,
            cooldown_turns=1,
        )

        output = agent.run(agent_input=self._build_input())

        self.assertEqual(output.llm_output.step_result.scheduled_npc_ids, ["char-fast-0001", "char-mid-0002"])
        self.assertEqual(list(output.llm_output.step_result.extra_npc_context.keys()), ["char-fast-0001", "char-mid-0002"])
        self.assertIn("narrative_info", service.last_payload)
        self.assertEqual(service.last_payload["allowed_npc_ids"], ["char-fast-0001", "char-mid-0002", "char-slow-0003"])

    def test_scheduler_filters_zero_status_and_cooldown(self):
        service = SchedulerFakeLLMService(
            scheduled_npc_ids=["char-dead-0004", "char-fast-0001", "char-mid-0002"],
            extra_npc_context={
                "char-dead-0004": "dead",
                "char-fast-0001": "fast",
                "char-mid-0002": "mid",
            },
        )
        agent = NpcSchedulerAgent(
            llm_service=service,
            world_state=self.world,
            max_actions_per_turn=3,
            cooldown_turns=1,
        )

        first_input = self._build_input()
        first_input.system_input.execution.turn_id = 10
        first_output = agent.run(agent_input=first_input)
        self.assertEqual(first_output.llm_output.step_result.scheduled_npc_ids, ["char-fast-0001", "char-mid-0002"])

        second_input = self._build_input()
        second_input.system_input.execution.turn_id = 11
        second_output = agent.run(agent_input=second_input)
        self.assertEqual(second_output.llm_output.step_result.scheduled_npc_ids, [])
        self.assertIn("系统过滤后本回合未调度NPC", second_output.llm_output.step_result.summary)

        third_input = self._build_input()
        third_input.system_input.execution.turn_id = 12
        third_output = agent.run(agent_input=third_input)
        self.assertEqual(third_output.llm_output.step_result.scheduled_npc_ids, ["char-fast-0001", "char-mid-0002"])

    def test_scheduler_filters_any_zero_status_not_only_health_sanity(self):
        service = SchedulerFakeLLMService(
            scheduled_npc_ids=["char-custom_zero-0005", "char-fast-0001", "char-mid-0002"],
            extra_npc_context={
                "char-custom_zero-0005": "custom",
                "char-fast-0001": "fast",
                "char-mid-0002": "mid",
            },
        )
        agent = NpcSchedulerAgent(
            llm_service=service,
            world_state=self.world,
            max_actions_per_turn=3,
            cooldown_turns=0,
        )

        run_input = self._build_input()
        run_input.system_input.execution.turn_id = 20
        output = agent.run(agent_input=run_input)

        self.assertNotIn("char-custom_zero-0005", output.llm_output.step_result.scheduled_npc_ids)
        self.assertEqual(output.llm_output.step_result.scheduled_npc_ids, ["char-fast-0001", "char-mid-0002"])

    def test_scheduler_filters_ids_outside_allowed_list(self):
        service = SchedulerFakeLLMService(
            scheduled_npc_ids=["char-fast-0001", "char-dead-0004"],
            extra_npc_context={
                "char-fast-0001": "fast",
                "char-dead-0004": "dead",
            },
        )
        agent = NpcSchedulerAgent(
            llm_service=service,
            world_state=self.world,
            max_actions_per_turn=3,
            cooldown_turns=0,
        )

        output = agent.run(agent_input=self._build_input())

        self.assertEqual(output.llm_output.step_result.scheduled_npc_ids, ["char-fast-0001"])
        self.assertNotIn("char-dead-0004", output.llm_output.step_result.extra_npc_context)

    def test_scheduler_view_groups_current_adjacent_and_important_characters_by_map(self):
        current_map = MapEntity(
            id="map-current-0001",
            name="当前地图",
            description=Description(public=["当前地图"]),
            connections=[
                MapConnection(
                    id="conn-current-to-adjacent-0001",
                    target_map_id="map-adjacent-0002",
                    name="相邻路",
                    direction="east",
                )
            ],
        )
        adjacent_map = MapEntity(
            id="map-adjacent-0002",
            name="相邻地图",
            description=Description(public=["相邻地图"]),
        )
        remote_map = MapEntity(
            id="map-remote-0003",
            name="远处地图",
            description=Description(public=["远处地图"]),
        )
        player = CharacterEntity(id="char-player-0000", name="玩家", location=current_map.id)
        local_npc = CharacterEntity(id="char-local-0001", name="本地 NPC", location=current_map.id)
        adjacent_npc = CharacterEntity(id="char-adjacent-0002", name="相邻 NPC", location=adjacent_map.id)
        remote_important = CharacterEntity(id="char-remote-0003", name="重要 NPC", location=remote_map.id, important=True)
        remote_plain = CharacterEntity(id="char-plain-0004", name="普通 NPC", location=remote_map.id, important=False)

        world = WorldState()
        world.reset(
            WorldEntityStore(
                maps={current_map.id: current_map, adjacent_map.id: adjacent_map, remote_map.id: remote_map},
                characters={
                    player.id: player,
                    local_npc.id: local_npc,
                    adjacent_npc.id: adjacent_npc,
                    remote_important.id: remote_important,
                    remote_plain.id: remote_plain,
                },
                items={},
            )
        )

        view = WorldDataProvider(world).precompute_all_views(current_map_id=current_map.id, turn=1).npc_scheduler_view
        grouped_ids = {
            map_slice.map_id: [character.id for character in map_slice.characters]
            for map_slice in view.available_character_maps
        }
        allowed_ids = TurnOrchestrator._collect_allowed_npc_ids(actor_id=player.id, scheduler_view=view)

        self.assertEqual(list(grouped_ids.keys()), [current_map.id, adjacent_map.id, remote_map.id])
        self.assertEqual(grouped_ids[current_map.id], ["char-player-0000", "char-local-0001"])
        self.assertEqual(grouped_ids[adjacent_map.id], ["char-adjacent-0002"])
        self.assertEqual(grouped_ids[remote_map.id], ["char-remote-0003"])
        self.assertEqual(allowed_ids, ["char-local-0001", "char-adjacent-0002", "char-remote-0003"])


if __name__ == "__main__":
    unittest.main()
