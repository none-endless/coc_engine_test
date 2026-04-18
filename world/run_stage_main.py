from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
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
    MergerAgentLlmOutput,
    NarrativeAgentLlmOutput,
    NpcPerformerAgentLlmOutput,
    NpcSchedulerAgentLlmOutput,
    StateAgentLlmOutput,
)
from src.data.model.base import WorldEntityStore
from src.data.model.input.agent_chain_input import E7CausalityChain
from src.data.model.world_state import WorldState
from src.engine.engine import Engine
from src.utils.agent_io_logger import AgentIoLogger


class StageSceneLLMService:
    """Local smoke-test LLM service used by the stage demo."""

    def __init__(self, io_recorder=None) -> None:
        self.config = ConfigLoader.load(
            cli_overrides={
                "system.max_retry_count": 2,
                "system.retry_timeout_ms": 3000,
                "system.fallback_error": "State branch failed and rolled back to the pre-turn checkpoint.",
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
        if output_model is NpcPerformerAgentLlmOutput:
            result = output_model.model_validate(self._build_performer_output(user_payload))
            self._record_io(agent_name=agent_name, user_payload=user_payload, output_model=output_model, output=result)
            return result
        if output_model is NarrativeAgentLlmOutput:
            result = output_model.model_validate(self._build_narrative_output(summary))
            self._record_io(agent_name=agent_name, user_payload=user_payload, output_model=output_model, output=result)
            return result
        if output_model is MergerAgentLlmOutput:
            result = output_model.model_validate(self._build_merger_output(user_payload))
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
        if "偷" in normalized or "潜行" in normalized or "观察守卫" in normalized:
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
        if "返回值班室" in normalized or "回房间" in normalized or "回值班室" in normalized:
            return {
                "intent_info": {
                    "intent": "move_to_room",
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
            summary = f"turn={turn_id}; trace={trace_id}; 玩家向守卫发起攻击，对抗检定已经完成。"
            visible = True
        elif "调查" in raw_text or "笔记" in raw_text:
            summary = f"turn={turn_id}; trace={trace_id}; 玩家调查了桌上的值班笔记，数值检定已经完成。"
            visible = True
        elif "偷" in raw_text or "潜行" in raw_text:
            summary = f"turn={turn_id}; trace={trace_id}; 玩家试图在不惊动守卫的情况下观察四周。"
            visible = False
        elif "走向走廊" in raw_text or "去走廊" in raw_text or "移动到走廊" in raw_text:
            summary = f"turn={turn_id}; trace={trace_id}; 玩家从 map-room-0001 移动到 map-hall-0002。"
            visible = True
        elif "返回值班室" in raw_text or "回房间" in raw_text or "回值班室" in raw_text:
            summary = f"turn={turn_id}; trace={trace_id}; 玩家从 map-hall-0002 返回 map-room-0001。"
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
        scheduled_npc_ids: List[str] = []
        extra: Dict[str, Optional[str]] = {}

        if any(keyword in summary for keyword in ["攻击", "守卫", "潜行", "观察", "调查"]):
            scheduled_npc_ids.append("char-guard-0001")

        if "攻击" in summary:
            extra["char-guard-0001"] = "守卫被玩家的攻击动作惊动，立即提高警戒。"
        elif "潜行" in summary or "观察" in summary:
            extra["char-guard-0001"] = "守卫隐约感觉周围有异动，开始扫视值班室与门口。"
        elif "调查" in summary:
            extra["char-guard-0001"] = "守卫注意到玩家在翻看桌上的值班笔记。"
        elif "移动到 map-hall-0002" in summary:
            extra["char-guard-0001"] = "守卫看见玩家走向走廊，视线短暂追随着对方。"

        return {
            "step_result": {
                "summary": summary or "本回合没有新的 NPC 调度事件。",
                "scheduled_npc_ids": scheduled_npc_ids,
                "extra_npc_context": extra,
            }
        }

    @staticmethod
    def _build_performer_output(user_payload: Dict[str, Any]) -> Dict[str, Any]:
        world_info = user_payload.get("world_info", {}) if isinstance(user_payload, dict) else {}
        npc_name = str(world_info.get("name", "NPC"))
        npc_id = str(world_info.get("id", ""))
        e4 = user_payload.get("e4", {}) if isinstance(user_payload, dict) else {}
        e1 = user_payload.get("e1", {}) if isinstance(user_payload, dict) else {}
        raw_text = str(e1.get("raw_text", ""))
        extra_context = ""
        if isinstance(e4, dict):
            extra_context = str(e4.get("extra_npc_context", {}).get(npc_id, "") or "")

        intent = "observe"
        action_text = f"{npc_name}保持戒备，继续观察玩家。"
        change_active_goal: Optional[str] = None

        if "攻击" in raw_text:
            intent = "counter_attack"
            action_text = f"{npc_name}迅速后撤半步并抬手反制，准备压制玩家。"
            change_active_goal = "优先制服突然发动攻击的玩家"
        elif "潜行" in raw_text or "观察" in raw_text:
            intent = "search_intruder"
            action_text = f"{npc_name}放轻脚步巡视门口和桌边，试图锁定可疑动静。"
            change_active_goal = "查明值班室周围的异常动静"
        elif "调查" in raw_text or "笔记" in raw_text:
            intent = "question_player"
            action_text = f"{npc_name}上前一步，质问玩家为何翻看值班记录。"
            change_active_goal = "确认玩家翻看笔记的真实意图"
        elif "移动到 map-hall-0002" in raw_text:
            intent = "follow_player"
            action_text = f"{npc_name}站到门边，继续关注玩家在走廊里的去向。"
            change_active_goal = "监视离开值班室的玩家"

        if extra_context and extra_context not in action_text:
            action_text = f"{extra_context} {action_text}".strip()

        return {
            "intent": intent,
            "action_text": action_text,
            "change_basic_goal": None,
            "change_active_goal": change_active_goal,
        }

    @staticmethod
    def _build_narrative_output(summary: str) -> Dict[str, Any]:
        if "移动到 map-hall-0002" in summary:
            text = "你推开门走进走廊，狭长的空间立刻把你的脚步声放大。"
        elif "返回 map-room-0001" in summary:
            text = "你重新回到值班室，桌上的笔记和守卫的视线一起压了过来。"
        elif "攻击" in summary:
            text = "你猛地向守卫逼近，紧绷的空气瞬间被打破。"
        elif "调查" in summary:
            text = "你俯身翻看桌上的值班笔记，试图从凌乱的记录里找出线索。"
        else:
            text = "你暂时按兵不动，默默观察着周围的一切。"
        return {
            "narrative_str": text,
        }

    @staticmethod
    def _build_merger_output(user_payload: Dict[str, Any]) -> Dict[str, Any]:
        narrative_str = str(user_payload.get("narrative_str", "")).strip()
        if narrative_str:
            return {"narrative_str": narrative_str}

        e7 = user_payload.get("e7", {})
        if isinstance(e7, dict):
            e7_text = str(e7.get("narrative_causality", "")).strip()
            if e7_text:
                return {"narrative_str": e7_text}

        return {"narrative_str": "本回合未产生额外可见叙事。"}

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
        if "返回 map-room-0001" in summary:
            changes.append(
                {
                    "op": "MOVE",
                    "target_path": "char-player-0000.location",
                    "value": "map-room-0001",
                }
            )
        return {"changes": changes}


def load_scene(scene_path: Path) -> Dict[str, Any]:
    """Load the scene JSON payload under the world directory."""
    with scene_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def build_llm_service(use_real_llm: bool, config_path: str, io_logger=None) -> Any:
    """Choose the real LLM service or the local demo service."""
    if not use_real_llm:
        return StageSceneLLMService(io_recorder=io_logger)

    config = ConfigLoader.load(config_path=config_path)
    return LLMServiceBase(config=config, io_recorder=io_logger)


def build_engine_from_scene(scene_payload: Dict[str, Any], mode: str, use_real_llm: bool, config_path: str, io_logger=None) -> Engine:
    """Rebuild the world from scene payload and return the engine."""
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
    """Fold evolution, performer, and narrative outputs into the current E7 chain."""
    chain = previous_chain.model_copy(deep=True)

    evolution = result.get("evolution", {}) if isinstance(result, dict) else {}
    evolution_summary = str(evolution.get("summary", "")).strip() if isinstance(evolution, dict) else ""
    if evolution_summary:
        chain.narrative_list.append(
            {
                "source": "evolution",
                "trace_id": str(trace_id),
                "content": evolution_summary,
            }
        )

    performer_outputs = result.get("npcperformer", [])
    if isinstance(performer_outputs, list):
        for performer in performer_outputs:
            if not isinstance(performer, dict):
                continue
            llm_output = performer.get("llm_output", {})
            system_output = performer.get("system_output", {})
            action_text = str(llm_output.get("action_text", "")).strip()
            if not action_text:
                continue
            chain.narrative_list.append(
                {
                    "source": "npcperformer",
                    "trace_id": str(system_output.get("trace_id", trace_id)),
                    "content": action_text,
                }
            )

    narrative = result.get("narrative", {})
    llm_output = narrative.get("llm_output", {}) if isinstance(narrative, dict) else {}
    narrative_str = llm_output.get("narrative_str")
    if narrative_str:
        chain.narrative_list.append(
            {
                "source": "narrative",
                "trace_id": str(trace_id),
                "content": str(narrative_str),
            }
        )
    return chain


def run_full_test_suite(test_targets: List[str], extra_pytest_args: List[str]) -> int:
    """Run the full pytest suite and persist terminal output plus summary."""
    log_dir = Path(__file__).with_name("log")
    log_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    txt_report = log_dir / f"full_test_{timestamp}.log"
    json_report = log_dir / f"full_test_{timestamp}.json"

    cmd = [sys.executable, "-m", "pytest", *test_targets, *extra_pytest_args]
    env = os.environ.copy()
    env["PYTHONPATH"] = "."

    completed = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )

    merged_output = (completed.stdout or "") + ("\n" if completed.stdout and completed.stderr else "") + (completed.stderr or "")
    txt_report.write_text(merged_output, encoding="utf-8")

    summary = {
        "command": cmd,
        "cwd": str(REPO_ROOT),
        "exit_code": completed.returncode,
        "test_targets": test_targets,
        "extra_pytest_args": extra_pytest_args,
        "txt_report": str(txt_report),
    }
    json_report.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== Full Test Result ===")
    print(f"exit_code: {completed.returncode}")
    print(f"text report: {txt_report}")
    print(f"summary report: {json_report}")
    print("output tail:")
    preview = merged_output.strip().splitlines()
    for line in preview[-20:]:
        print(line)

    return int(completed.returncode)


def print_turn_summary(engine: Engine, result: Dict[str, Any], actor_id: str) -> None:
    """Print the key outputs of the current turn."""
    print("\n=== Turn Result ===")
    print(f"route: {result.get('route')}")
    print(f"turn_id: {result.get('turn_id')}, trace_id: {result.get('trace_id')}")

    if result.get("route") == "rule_system_meta":
        payload = result.get("payload", {})
        print(f"meta command: {payload.get('command')}")
        print(payload.get("result", ""))
        return
    if result.get("route") == "dm_direct_reply":
        print(f"dm.reply: {result.get('reply', '')}")
        return

    dm = result.get("dm", {}).get("intent_info", {})
    e3 = result.get("e3", {})
    evolution = result.get("evolution", {})
    print(f"dm.intent: {dm.get('intent')}")
    print(f"dm.routing_hint: {dm.get('routing_hint')}")
    print(f"e3: {json.dumps(e3, ensure_ascii=False)}")
    print(f"evolution.summary: {evolution.get('summary')}")
    print(f"narrative_triggered: {result.get('narrative_triggered')}")

    scheduler = result.get("npcscheduler", {})
    scheduler_llm = scheduler.get("llm_output", {}) if isinstance(scheduler, dict) else {}
    step_result = scheduler_llm.get("step_result", {}) if isinstance(scheduler_llm, dict) else {}
    if step_result:
        print(f"npc_scheduler.summary: {step_result.get('summary')}")
        print(f"npc_scheduler.scheduled_npc_ids: {step_result.get('scheduled_npc_ids')}")

    performer_outputs = result.get("npcperformer", [])
    if isinstance(performer_outputs, list) and performer_outputs:
        for performer in performer_outputs:
            llm_output = performer.get("llm_output", {})
            system_output = performer.get("system_output", {})
            print(
                "npc_performer: "
                f"id={system_output.get('id')}, "
                f"intent={llm_output.get('intent')}, "
                f"action={llm_output.get('action_text')}"
            )
            if llm_output.get("change_active_goal"):
                print(f"npc_performer.goal.active: {llm_output.get('change_active_goal')}")

    if "narrative" in result:
        narrative = result.get("narrative", {}).get("llm_output", {}).get("narrative_str", "")
        print(f"narrative: {narrative}")

    merger_payload = result.get("merger")
    if isinstance(merger_payload, dict):
        merger = merger_payload.get("llm_output", {}).get("narrative_str", "")
        print(f"merger: {merger}")
    elif merger_payload is None and "merger" in result:
        print("merger: <skipped>")

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
    """Print the player's current location, nearby characters, and items."""
    actor = engine.world_state.get_character(actor_id)
    current_map = engine.world_state.get_map(actor.location)
    chars = [x.name for x in engine.world_state.get_characters_at(actor.location)]
    items = [x.name for x in engine.world_state.get_items_at(actor.location)]

    print("\n=== Snapshot ===")
    print(f"player: {actor.name}")
    print(f"location: {current_map.name} ({current_map.id})")
    print("map.description:")
    for line in current_map.description.public:
        print(f"- {line}")
    print(f"map.characters: {', '.join(chars) if chars else 'none'}")
    print(f"map.items: {', '.join(items) if items else 'none'}")


def _normalize_user_input(raw_input_text: str) -> str:
    normalized_input = raw_input_text.strip()
    if normalized_input.startswith("\\\\"):
        normalized_input = "\\" + normalized_input.lstrip("\\")
    return normalized_input


def _run_single_turn(
    *,
    engine: Engine,
    actor_id: str,
    turn_id: int,
    trace_id: int,
    causality_chain: E7CausalityChain,
    raw_input_text: str,
    io_logger: AgentIoLogger,
) -> E7CausalityChain:
    normalized_input = _normalize_user_input(raw_input_text)
    result = engine.run_turn(
        raw_input=normalized_input,
        actor_id=actor_id,
        turn_id=turn_id,
        trace_id=trace_id,
        causality_chain=causality_chain,
    )
    print(f"\n>>> {normalized_input}")
    print_turn_summary(engine=engine, result=result, actor_id=actor_id)
    print(f"log: {io_logger.log_path}")
    return build_causality_chain(causality_chain, result, trace_id)


def run_scripted_session(
    *,
    scene_payload: Dict[str, Any],
    scene_path: Path,
    mode: str,
    use_real_llm: bool,
    config_path: str,
    auto_turns: int,
    script_source: str,
) -> None:
    """Run a fixed number of scripted turns for end-to-end smoke testing."""
    log_dir = scene_path.with_name("log")
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

    scripted_inputs = scene_payload.get(script_source, [])
    if not isinstance(scripted_inputs, list) or not scripted_inputs:
        fallback = scene_payload.get("recommended_inputs", [])
        scripted_inputs = fallback if isinstance(fallback, list) else []
    if not scripted_inputs:
        raise ValueError("scene has no scripted inputs to run")

    print(f"scene: {scene_payload.get('scene_name', 'unnamed scene')}")
    print(f"mode: {mode}")
    print(f"llm_mode: {'real' if use_real_llm else 'fake'}")
    print(f"script_source: {script_source}")
    print(f"auto_turns: {auto_turns}")
    print(f"log: {io_logger.log_path}")
    print_snapshot(engine, actor_id)

    for index in range(auto_turns):
        raw_input_text = str(scripted_inputs[index % len(scripted_inputs)])
        causality_chain = _run_single_turn(
            engine=engine,
            actor_id=actor_id,
            turn_id=turn_id,
            trace_id=trace_id,
            causality_chain=causality_chain,
            raw_input_text=raw_input_text,
            io_logger=io_logger,
        )
        turn_id += 1
        trace_id += 1

    print("\n=== Final Snapshot ===")
    print_snapshot(engine, actor_id)


def run_repl(scene_path: Path, mode: str, use_real_llm: bool, config_path: str, auto_turns: int = 0, script_source: str = "scripted_inputs") -> None:
    """Run the interactive demo, or auto-play a fixed number of turns."""
    scene_payload = load_scene(scene_path)
    if auto_turns > 0:
        run_scripted_session(
            scene_payload=scene_payload,
            scene_path=scene_path,
            mode=mode,
            use_real_llm=use_real_llm,
            config_path=config_path,
            auto_turns=auto_turns,
            script_source=script_source,
        )
        return

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

    print(f"scene: {scene_payload.get('scene_name', 'unnamed scene')}")
    print(f"mode: {mode}")
    print(f"llm_mode: {'real' if use_real_llm else 'fake'}")
    print(f"log: {io_logger.log_path}")
    print(f"actor_id: {actor_id}")
    print("type natural language, or use :help / :snapshot / :reset / :quit")
    tips = scene_payload.get("recommended_inputs", [])
    if isinstance(tips, list) and tips:
        print("recommended_inputs:")
        for item in tips:
            print(f"- {item}")

    print_snapshot(engine, actor_id)

    while True:
        raw_input_text = input("\n>>> ").strip()
        if not raw_input_text:
            continue

        if raw_input_text == ":quit":
            print("repl closed.")
            return
        if raw_input_text == ":help":
            print("type natural language to run the chain; :snapshot shows world state; :reset rebuilds the scene; :quit exits.")
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
            print("scene reset.")
            print_snapshot(engine, actor_id)
            continue

        try:
            causality_chain = _run_single_turn(
                engine=engine,
                actor_id=actor_id,
                turn_id=turn_id,
                trace_id=trace_id,
                causality_chain=causality_chain,
                raw_input_text=raw_input_text,
                io_logger=io_logger,
            )
        except Exception as exc:
            print(f"turn failed: {exc}")
            continue

        turn_id += 1
        trace_id += 1


def parse_args() -> argparse.Namespace:
    """Parse CLI args for the stage runner."""
    parser = argparse.ArgumentParser(description="Phase chain stage runner")
    parser.add_argument(
        "--mode",
        choices=["phase2", "phase3"],
        default="phase3",
        help="run phase2 serial mode or phase3 concurrent mode",
    )
    parser.add_argument(
        "--scene",
        default=str(Path(__file__).with_name("simple_stage_scene.json")),
        help="scene JSON path under world",
    )
    parser.add_argument(
        "--use-real-llm",
        action="store_true",
        help="use the real LLM service configured in config instead of the local fake service",
    )
    parser.add_argument(
        "--config",
        default="config/config.yaml",
        help="config file path used in real LLM mode",
    )
    parser.add_argument(
        "--auto-turns",
        type=int,
        default=0,
        help="run scripted turns automatically; set to 10 for full chain smoke test",
    )
    parser.add_argument(
        "--script-source",
        choices=["scripted_inputs", "recommended_inputs"],
        default="scripted_inputs",
        help="which input list in the scene JSON to use for auto-play",
    )
    parser.add_argument(
        "--full-test",
        action="store_true",
        help="run full pytest and persist reports, then exit",
    )
    parser.add_argument(
        "--test-target",
        nargs="*",
        default=["tests"],
        help="pytest target paths for --full-test; defaults to tests",
    )
    parser.add_argument(
        "--pytest-args",
        nargs="*",
        default=[],
        help="extra args passed to pytest in --full-test mode, for example --pytest-args -q -x",
    )
    args, unknown = parser.parse_known_args()
    if unknown:
        args.pytest_args = list(args.pytest_args) + list(unknown)
    return args


if __name__ == "__main__":
    args = parse_args()
    if bool(args.full_test):
        raise SystemExit(run_full_test_suite(test_targets=list(args.test_target), extra_pytest_args=list(args.pytest_args)))

    run_repl(
        scene_path=Path(args.scene),
        mode=args.mode,
        use_real_llm=bool(args.use_real_llm),
        config_path=str(args.config),
        auto_turns=int(args.auto_turns),
        script_source=str(args.script_source),
    )
