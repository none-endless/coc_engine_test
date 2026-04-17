from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Type

from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.agent.llm.service import LLMServiceBase
from src.config.loader import ConfigLoader
from src.data.model.agent_output import (
    DmAgentLlmOutput,
    EvolutionAgentLlmOutput,
    NarrativeAgentLlmOutput,
    NpcSchedulerAgentLlmOutput,
    StateAgentLlmOutput,
)
from src.data.model.base import WorldEntityStore
from src.data.model.input.agent_chain_input import E7CausalityChain
from src.data.model.world_state import WorldState
from src.engine.engine import Engine
from src.utils.agent_io_logger import AgentIoLogger


class StageSceneLLMService:
    """用于本地链路冒烟的场景专用假 LLM 服务。"""

    def __init__(self, io_recorder=None) -> None:
        self.config = ConfigLoader.load(
            cli_overrides={
                "system.max_retry_count": 2,
                "system.retry_timeout_ms": 3000,
                "system.fallback_error": "状态分支执行失败，已回滚到本回合前。",
            }
        )
        self.io_recorder = io_recorder

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
        del system_prompt, retry_budget, validation_feedback

        raw_text = self._extract_raw_text(user_payload)
        summary = self._extract_summary(user_payload)

        if output_model is DmAgentLlmOutput:
            result = output_model.model_validate(self._build_dm_output(raw_text))
            self._record_io(agent_name=agent_name, user_payload=user_payload, output_model=output_model, output=result)
            return result
        if output_model is EvolutionAgentLlmOutput:
            result = output_model.model_validate(self._build_evolution_output(raw_text, user_payload))
            self._record_io(agent_name=agent_name, user_payload=user_payload, output_model=output_model, output=result)
            return result
        if output_model is NpcSchedulerAgentLlmOutput:
            result = output_model.model_validate(self._build_scheduler_output(summary))
            self._record_io(agent_name=agent_name, user_payload=user_payload, output_model=output_model, output=result)
            return result
        if output_model is NarrativeAgentLlmOutput:
            result = output_model.model_validate(self._build_narrative_output(summary))
            self._record_io(agent_name=agent_name, user_payload=user_payload, output_model=output_model, output=result)
            return result
        if output_model is StateAgentLlmOutput:
            result = output_model.model_validate(self._build_state_output(summary))
            self._record_io(agent_name=agent_name, user_payload=user_payload, output_model=output_model, output=result)
            return result

        raise AssertionError(f"unsupported output model: {output_model}")

    def _record_io(self, *, agent_name: str, user_payload: Dict[str, Any], output_model: Type[BaseModel], output: BaseModel) -> None:
        if self.io_recorder is None:
            return
        self.io_recorder(
            {
                "kind": "llm_call",
                "status": "success",
                "agent_name": agent_name,
                "response_model": output_model.__name__,
                "user_payload": user_payload,
                "parsed_output": output.model_dump(mode="json"),
            }
        )

    @staticmethod
    def _extract_raw_text(user_payload: Dict[str, Any]) -> str:
        e1 = user_payload.get("e1", {})
        if isinstance(e1, dict):
            return str(e1.get("raw_text", ""))
        return str(user_payload.get("raw_text", ""))

    @staticmethod
    def _extract_summary(user_payload: Dict[str, Any]) -> str:
        e4 = user_payload.get("e4", {})
        if isinstance(e4, dict):
            return str(e4.get("summary", ""))
        return ""

    @staticmethod
    def _build_dm_output(raw_text: str) -> Dict[str, Any]:
        normalized = raw_text.strip()
        if "攻击守卫" in normalized:
            return {
                "intent_info": {
                    "intent": "attack_guard",
                    "routing_hint": "against",
                    "attributes": ["fight"],
                    "against_char_id": ["char-player-0000", "char-guard-0001"],
                    "difficulty": None,
                    "dm_reply": None,
                }
            }
        if "调查" in normalized or "笔记" in normalized:
            return {
                "intent_info": {
                    "intent": "investigate_notes",
                    "routing_hint": "num",
                    "attributes": ["investigation"],
                    "against_char_id": ["char-player-0000"],
                    "difficulty": None,
                    "dm_reply": None,
                }
            }
        if "偷偷观察" in normalized or "潜行" in normalized:
            return {
                "intent_info": {
                    "intent": "stealth_observe_guard",
                    "routing_hint": "num",
                    "attributes": ["stealth"],
                    "against_char_id": ["char-player-0000"],
                    "difficulty": "困难",
                    "dm_reply": None,
                }
            }
        if "走向走廊" in normalized or "去走廊" in normalized or "移动到走廊" in normalized:
            return {
                "intent_info": {
                    "intent": "move_to_hall",
                    "routing_hint": None,
                    "attributes": [],
                    "against_char_id": [],
                    "difficulty": None,
                    "dm_reply": None,
                }
            }
        return {
            "intent_info": {
                "intent": "talk_or_observe",
                "routing_hint": None,
                "attributes": [],
                "against_char_id": [],
                "difficulty": None,
                "dm_reply": "你环顾四周，空气里弥漫着紧张的安静。",
            }
        }

    @staticmethod
    def _build_evolution_output(raw_text: str, user_payload: Dict[str, Any]) -> Dict[str, Any]:
        execution = user_payload.get("execution", {})
        turn_id = execution.get("turn_id", 0)
        trace_id = execution.get("trace_id", 0)

        if "攻击守卫" in raw_text:
            summary = f"turn={turn_id}; trace={trace_id}; 玩家向守卫发起攻击，对抗检定已完成。"
            visible = True
        elif "调查" in raw_text or "笔记" in raw_text:
            summary = f"turn={turn_id}; trace={trace_id}; 玩家调查了桌上的值班笔记，数值检定已完成。"
            visible = True
        elif "偷偷观察" in raw_text or "潜行" in raw_text:
            summary = f"turn={turn_id}; trace={trace_id}; 玩家试图在不惊动守卫的情况下观察四周。"
            visible = False
        elif "走向走廊" in raw_text or "去走廊" in raw_text or "移动到走廊" in raw_text:
            summary = f"turn={turn_id}; trace={trace_id}; 玩家从 map-room-0001 移动到 map-hall-0002。"
            visible = True
        else:
            summary = f"turn={turn_id}; trace={trace_id}; 玩家短暂观察了当前环境。"
            visible = True

        return {
            "summary": summary,
            "visible_to_player": visible,
        }

    @staticmethod
    def _build_scheduler_output(summary: str) -> Dict[str, Any]:
        extra: Dict[str, Optional[str]] = {}
        if "攻击" in summary:
            extra["char-guard-0001"] = "守卫被你的动作惊动，开始警戒。"
        return {
            "step_result": {
                "summary": "场景内暂无额外 NPC 自主行动。",
                "extra_npc_context": extra,
            }
        }

    @staticmethod
    def _build_narrative_output(summary: str) -> Dict[str, Any]:
        if "移动到 map-hall-0002" in summary:
            text = "你推开门走进走廊，狭长的空间立刻把你的脚步声放大。"
        elif "攻击" in summary:
            text = "你猛地向守卫逼近，紧绷的空气瞬间被打破。"
        elif "调查" in summary:
            text = "你俯身翻看桌上的值班笔记，试图从凌乱的记录里找出线索。"
        else:
            text = "你暂时按兵不动，默默观察着周围的一切。"
        return {
            "narrative_str": text,
            "narrative_draft": None,
        }

    @staticmethod
    def _build_state_output(summary: str) -> Dict[str, Any]:
        changes: List[Dict[str, Any]] = []
        if "移动到 map-hall-0002" in summary:
            changes.append(
                {
                    "op": "MOVE",
                    "target_path": "char-player-0000.location",
                    "value": "map-hall-0002",
                }
            )
        return {"changes": changes}


def load_scene(scene_path: Path) -> Dict[str, Any]:
    """读取 world 目录下的场景配置。"""
    with scene_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def build_llm_service(use_real_llm: bool, config_path: str, io_logger=None) -> Any:
    """按启动参数选择真实 LLM 或本地假 LLM。"""
    if not use_real_llm:
        return StageSceneLLMService(io_recorder=io_logger)

    config = ConfigLoader.load(config_path=config_path)
    return LLMServiceBase(config=config, io_recorder=io_logger)


def build_engine_from_scene(scene_payload: Dict[str, Any], mode: str, use_real_llm: bool, config_path: str, io_logger=None) -> Engine:
    """根据场景配置重建世界并返回 Engine。"""
    store = WorldEntityStore.model_validate(scene_payload["store"])
    world = WorldState()
    world.reset(store)
    return Engine(
        world_state=world,
        mode=mode,
        llm_service=build_llm_service(use_real_llm=use_real_llm, config_path=config_path, io_logger=io_logger),
        io_logger=io_logger,
        config_path=config_path,
    )


def build_causality_chain(previous_chain: E7CausalityChain, result: Dict[str, Any], trace_id: int) -> E7CausalityChain:
    """把 narrative 分支结果折叠回当前会话的 e7 因果链。"""
    chain = previous_chain.model_copy(deep=True)
    narrative = result.get("narrative", {})
    llm_output = narrative.get("llm_output", {}) if isinstance(narrative, dict) else {}
    narrative_str = llm_output.get("narrative_str")
    if narrative_str:
        chain.narrative_list.append({"trace_id": str(trace_id), "content": str(narrative_str)})
    return chain


def print_turn_summary(engine: Engine, result: Dict[str, Any], actor_id: str) -> None:
    """输出本回合关键链路结果。"""
    print("\n=== 本回合结果 ===")
    print(f"route: {result.get('route')}")
    print(f"turn_id: {result.get('turn_id')}, trace_id: {result.get('trace_id')}")

    if result.get("route") == "rule_system_meta":
        payload = result.get("payload", {})
        print(f"meta command: {payload.get('command')}")
        print(payload.get("result", ""))
        return

    dm = result.get("dm", {}).get("intent_info", {})
    e3 = result.get("e3", {})
    evolution = result.get("evolution", {})
    print(f"dm.intent: {dm.get('intent')}")
    print(f"dm.routing_hint: {dm.get('routing_hint')}")
    print(f"e3: {json.dumps(e3, ensure_ascii=False)}")
    print(f"evolution.summary: {evolution.get('summary')}")
    print(f"narrative_triggered: {result.get('narrative_triggered')}")

    if "narrative" in result:
        narrative = result.get("narrative", {}).get("llm_output", {}).get("narrative_str", "")
        print(f"narrative: {narrative}")

    if "state" in result:
        state = result.get("state", {})
        print(f"state.ok: {state.get('ok')}")
        apply_result = state.get("apply_result")
        if apply_result:
            print(f"state.apply_result: {json.dumps(apply_result, ensure_ascii=False)}")
        if state.get("fallback_error"):
            print(f"state.fallback_error: {json.dumps(state.get('fallback_error'), ensure_ascii=False)}")

    actor = engine.world_state.get_character(actor_id)
    current_map = engine.world_state.get_map(actor.location)
    print(f"actor.location: {actor.location} ({current_map.name})")


def print_snapshot(engine: Engine, actor_id: str) -> None:
    """打印当前玩家位置、同图角色与物品。"""
    actor = engine.world_state.get_character(actor_id)
    current_map = engine.world_state.get_map(actor.location)
    chars = [x.name for x in engine.world_state.get_characters_at(actor.location)]
    items = [x.name for x in engine.world_state.get_items_at(actor.location)]

    print("\n=== 当前快照 ===")
    print(f"玩家: {actor.name}")
    print(f"位置: {current_map.name} ({current_map.id})")
    print("地图描述:")
    for line in current_map.description.public:
        print(f"- {line}")
    print(f"同图角色: {', '.join(chars) if chars else '无'}")
    print(f"同图物品: {', '.join(items) if items else '无'}")


def run_repl(scene_path: Path, mode: str, use_real_llm: bool, config_path: str) -> None:
    """运行一个面向 phase1-3 的简易本地交互主程序。"""
    scene_payload = load_scene(scene_path)
    log_dir = Path(__file__).with_name("log")
    io_logger = AgentIoLogger(log_dir)
    engine = build_engine_from_scene(
        scene_payload=scene_payload,
        mode=mode,
        use_real_llm=use_real_llm,
        config_path=config_path,
        io_logger=io_logger,
    )
    actor_id = str(scene_payload.get("default_actor_id", "char-player-0000"))
    turn_id = int(scene_payload.get("turn_start", 1))
    trace_id = 1000
    causality_chain = E7CausalityChain()

    print(f"场景: {scene_payload.get('scene_name', '未命名场景')}")
    print(f"运行模式: {mode}")
    print(f"LLM模式: {'real' if use_real_llm else 'fake'}")
    print(f"详细日志: {io_logger.log_path}")
    print(f"玩家角色: {actor_id}")
    print("可直接输入自然语言，或使用命令 :help / :snapshot / :reset / :quit")
    tips = scene_payload.get("recommended_inputs", [])
    if isinstance(tips, list) and tips:
        print("推荐输入:")
        for item in tips:
            print(f"- {item}")

    print_snapshot(engine, actor_id)

    while True:
        raw_input_text = input("\n>>> ").strip()
        if not raw_input_text:
            continue

        if raw_input_text == ":quit":
            print("已退出阶段性运行主程序。")
            return
        if raw_input_text == ":help":
            print("输入自然语言即可跑链路；:snapshot 查看快照；:reset 重置场景；:quit 退出。")
            continue
        if raw_input_text == ":snapshot":
            print_snapshot(engine, actor_id)
            continue
        if raw_input_text == ":reset":
            engine = build_engine_from_scene(
                scene_payload=scene_payload,
                mode=mode,
                use_real_llm=use_real_llm,
                config_path=config_path,
                io_logger=io_logger,
            )
            causality_chain = E7CausalityChain()
            turn_id = int(scene_payload.get("turn_start", 1))
            trace_id = 1000
            print("场景已重置。")
            print_snapshot(engine, actor_id)
            continue

        normalized_input = raw_input_text
        if normalized_input.startswith("\\\\"):
            normalized_input = "\\" + normalized_input.lstrip("\\")

        try:
            result = engine.run_turn(
                raw_input=normalized_input,
                actor_id=actor_id,
                turn_id=turn_id,
                trace_id=trace_id,
                causality_chain=causality_chain,
            )
        except Exception as exc:
            print(f"本回合执行失败: {exc}")
            continue

        print_turn_summary(engine=engine, result=result, actor_id=actor_id)
        print(f"详细日志已追加到: {io_logger.log_path}")
        causality_chain = build_causality_chain(causality_chain, result, trace_id)
        turn_id += 1
        trace_id += 1


def parse_args() -> argparse.Namespace:
    """解析启动参数。"""
    parser = argparse.ArgumentParser(description="phase1-3 阶段性链路运行主程序")
    parser.add_argument(
        "--mode",
        choices=["phase2", "phase3"],
        default="phase3",
        help="运行 phase2 串行模式或 phase3 并发模式",
    )
    parser.add_argument(
        "--scene",
        default=str(Path(__file__).with_name("simple_stage_scene.json")),
        help="world 目录下的场景 JSON 路径",
    )
    parser.add_argument(
        "--use-real-llm",
        action="store_true",
        help="使用 config 中配置的真实 LLM 服务，而不是本地 fake service",
    )
    parser.add_argument(
        "--config",
        default="config/config.yaml",
        help="真实 LLM 模式下使用的配置文件路径",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run_repl(
        scene_path=Path(args.scene),
        mode=args.mode,
        use_real_llm=bool(args.use_real_llm),
        config_path=str(args.config),
    )
