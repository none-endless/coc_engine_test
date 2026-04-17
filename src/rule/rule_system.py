from __future__ import annotations

from dataclasses import dataclass
from random import Random
import time
from typing import Any, Dict, Optional

from src.data.model.world_state import WorldState

from .dsl import DslEngine


@dataclass
class CocCheckResult:
    id: str
    name: str
    result_type: str
    roll: int
    target: int


class RuleSystem:
    def __init__(self, world_state: WorldState, dsl_engine: Optional[DslEngine] = None) -> None:
        self.world_state = world_state
        self.dsl_engine = dsl_engine or DslEngine()

    def run_meta_command(self, actor_id: str, command: str) -> Dict[str, Any]:
        started = time.perf_counter()
        normalized = command.strip().lower()

        if normalized == "\\look":
            payload = {"result": self._handle_look(actor_id), "command": "look"}
        elif normalized == "\\inventory":
            payload = {"result": self._handle_inventory(actor_id), "command": "inventory"}
        else:
            raise ValueError(f"unknown meta command: {command}")

        elapsed_ms = (time.perf_counter() - started) * 1000
        payload["elapsed_ms"] = elapsed_ms
        return payload

    def evaluate_assert(self, expression: str, snapshot: Dict[str, Any]) -> bool:
        return self.dsl_engine.evaluate(expression=expression, snapshot=snapshot, expected_version=snapshot.get("version"))

    def run_coc_check(
        self,
        actor_id: str,
        attribute_value: int,
        random_source: Optional[Random] = None,
    ) -> CocCheckResult:#修改建议:使用已有在model定义好的E3result而非使用在这里定义的垃圾
                        #修改建议: 缺少对多种鉴定方式的实现
        char = self.world_state.get_character(actor_id)
        rng = random_source or Random()
        roll = rng.randint(1, 100)

        if roll == 1 or (roll <=5 and attribute_value>=50):
            result_type = "大成功"
        elif roll == 100 or (roll >= 96 and attribute_value < 50):
            result_type = "大失败"
        elif roll <= attribute_value:
            result_type = "成功"
        else:
            result_type = "失败"

        return CocCheckResult(
            id=char.id,
            name=char.name,
            result_type=result_type,
            roll=roll,
            target=attribute_value,
        )

    def _handle_look(self, actor_id: str) -> str:
        actor = self.world_state.get_character(actor_id)
        current_map = self.world_state.get_map(actor.location)
        public_lines = current_map.description.public
        if not public_lines:
            return f"{current_map.name} 没有可见描述。"
        return "\n".join(public_lines)

    def _handle_inventory(self, actor_id: str) -> str:
        actor = self.world_state.get_character(actor_id)
        if not actor.inventory:
            return "你的背包是空的。"
        names = [item.name for item in actor.inventory.values()]
        return "你携带着: " + ", ".join(names)
