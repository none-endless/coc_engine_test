from __future__ import annotations

import time
from random import Random
from typing import Any, Dict, Optional, Tuple

from src.data.model.agent_output import CocCheckParticipant, CocCheckResult
from src.data.model.world_state import WorldSnapshot, WorldState

from .dsl import DslEngine


class RuleSystem:
    """规则系统：处理元命令、断言求值与 CoC 风格检定。"""

    def __init__(self, world_state: WorldState, dsl_engine: Optional[DslEngine] = None) -> None:
        self.world_state = world_state
        self.dsl_engine = dsl_engine or DslEngine()

    def run_meta_command(self, actor_id: str, command: str) -> Dict[str, Any]:
        """执行元命令分支，直接返回规则系统结果。"""
        started = time.perf_counter()
        normalized = command.strip().lower()

        if normalized == "\\look":
            payload = {"result": self._handle_look(actor_id), "command": "look"}
        elif normalized == "\\inventory":
            payload = {"result": self._handle_inventory(actor_id), "command": "inventory"}
        else:
            raise ValueError(f"unknown meta command: {command}")

        payload["elapsed_ms"] = (time.perf_counter() - started) * 1000
        return payload

    def evaluate_assert(self, expression: str, snapshot: WorldSnapshot | Dict[str, Any]) -> bool:
        """在指定快照版本上执行只读条件表达式。"""
        expected_version = snapshot.version if isinstance(snapshot, WorldSnapshot) else snapshot.get("version")
        return self.dsl_engine.evaluate(
            expression=expression,
            snapshot=snapshot,
            expected_version=expected_version,
        )

    def run_coc_check(
        self,
        actor_id: str,
        attribute_value: int,
        attribute_name: str = "",
        difficulty: Optional[str] = None,
        random_source: Optional[Random] = None,
    ) -> CocCheckResult:
        """兼容旧调用的单人数值检定入口。"""
        return self.run_numeric_check(
            actor_id=actor_id,
            attribute_name=attribute_name,
            attribute_value=attribute_value,
            difficulty=difficulty,
            random_source=random_source,
        )

    def run_numeric_check(
        self,
        *,
        actor_id: str,
        attribute_name: str,
        attribute_value: int,
        difficulty: Optional[str] = None,
        random_source: Optional[Random] = None,
    ) -> CocCheckResult:
        """执行单人数值检定。"""
        actor = self.world_state.get_character(actor_id)
        roll = (random_source or Random()).randint(1, 100)
        target = self._resolve_target(attribute_value=attribute_value, difficulty=difficulty)
        result_type, _ = self._judge_roll(
            roll=roll,
            base_target=attribute_value,
            resolved_target=target,
        )

        participant = CocCheckParticipant(
            id=actor.id,
            name=actor.name,
            attribute=attribute_name,
            difficulty=difficulty,
            result_type=result_type,
            roll=roll,
            target=target,
            is_winner=result_type in {"成功", "大成功"},
        )

        return CocCheckResult(
            check_type="num",
            id=actor.id,
            name=actor.name,
            attribute=attribute_name,
            difficulty=difficulty,
            result_type=result_type,
            roll=roll,
            target=target,
            winner_id=actor.id if participant.is_winner else None,
            affected_ids=[actor.id],
            participants=[participant],
        )

    def run_against_check(
        self,
        *,
        actor_id: str,
        actor_attribute_name: str,
        actor_attribute_value: int,
        target_id: str,
        target_attribute_name: str,
        target_attribute_value: int,
        difficulty: Optional[str] = None,
        actor_random_source: Optional[Random] = None,
        target_random_source: Optional[Random] = None,
    ) -> CocCheckResult:
        """执行双人对抗检定。"""
        actor = self.world_state.get_character(actor_id)
        target = self.world_state.get_character(target_id)

        actor_roll = (actor_random_source or Random()).randint(1, 100)
        target_roll = (target_random_source or Random()).randint(1, 100)
        actor_target = self._resolve_target(attribute_value=actor_attribute_value, difficulty=difficulty)
        target_target = self._resolve_target(attribute_value=target_attribute_value, difficulty=difficulty)

        actor_result_type, actor_rank = self._judge_roll(
            roll=actor_roll,
            base_target=actor_attribute_value,
            resolved_target=actor_target,
        )
        target_result_type, target_rank = self._judge_roll(
            roll=target_roll,
            base_target=target_attribute_value,
            resolved_target=target_target,
        )

        actor_wins = self._compare_against(
            left_rank=actor_rank,
            left_roll=actor_roll,
            right_rank=target_rank,
            right_roll=target_roll,
        )
        winner_id = actor.id if actor_wins else target.id

        participants = [
            CocCheckParticipant(
                id=actor.id,
                name=actor.name,
                attribute=actor_attribute_name,
                difficulty=difficulty,
                result_type=actor_result_type,
                roll=actor_roll,
                target=actor_target,
                is_winner=actor_wins,
            ),
            CocCheckParticipant(
                id=target.id,
                name=target.name,
                attribute=target_attribute_name,
                difficulty=difficulty,
                result_type=target_result_type,
                roll=target_roll,
                target=target_target,
                is_winner=not actor_wins,
            ),
        ]

        return CocCheckResult(
            check_type="against",
            id=actor.id,
            name=actor.name,
            attribute=actor_attribute_name,
            difficulty=difficulty,
            result_type=self._resolve_against_outcome(
                actor_result_type=actor_result_type,
                actor_wins=actor_wins,
            ),
            roll=actor_roll,
            target=actor_target,
            opposed_id=target.id,
            opposed_name=target.name,
            winner_id=winner_id,
            affected_ids=[actor.id, target.id],
            participants=participants,
        )

    def _handle_look(self, actor_id: str) -> str:
        """查看当前位置的公共描述。"""
        actor = self.world_state.get_character(actor_id)
        current_map = self.world_state.get_map(actor.location)
        public_lines = current_map.description.public
        if not public_lines:
            return f"{current_map.name} 没有可见描述。"
        return "\n".join(public_lines)

    def _handle_inventory(self, actor_id: str) -> str:
        """查看角色当前背包。"""
        actor = self.world_state.get_character(actor_id)
        if not actor.inventory:
            return "你的背包是空的。"
        names = [item.name for item in actor.inventory.values()]
        return "你携带着: " + ", ".join(names)

    @staticmethod
    def _resolve_target(attribute_value: int, difficulty: Optional[str]) -> int:
        """根据难度映射本次检定目标值。"""
        if difficulty == "困难":
            return max(1, attribute_value // 2)
        if difficulty == "简单":
            return min(99, attribute_value * 2)
        return attribute_value

    @staticmethod
    def _judge_roll(roll: int, base_target: int, resolved_target: int) -> Tuple[str, int]:
        """根据骰点判定结果，并返回可比较等级。"""
        if roll == 1 or (roll <= 5 and base_target >= 50):
            return "大成功", 3
        if roll == 100 or (roll >= 96 and base_target < 50):
            return "大失败", 0
        if roll <= resolved_target:
            return "成功", 2
        return "失败", 1

    @staticmethod
    def _compare_against(left_rank: int, left_roll: int, right_rank: int, right_roll: int) -> bool:
        """比较两方对抗结果，返回主动方是否胜出。"""
        if left_rank != right_rank:
            return left_rank > right_rank
        return left_roll < right_roll

    @staticmethod
    def _resolve_against_outcome(actor_result_type: str, actor_wins: bool) -> str:
        """折叠对抗检定为主动方的最终结果。"""
        if actor_wins:
            return actor_result_type if actor_result_type in {"大成功", "大失败"} else "成功"
        return "大失败" if actor_result_type == "大失败" else "失败"
