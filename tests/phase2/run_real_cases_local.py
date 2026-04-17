from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from src.config.loader import ConfigLoader
from src.agent.llm.service import LLMServiceBase
from src.data.model.base import Attribute, CharacterEntity, Description, MapEntity, WorldEntityStore
from src.data.model.world_state import WorldState
from src.engine.engine import Phase2Engine


def _build_engine() -> Phase2Engine:
    cfg = ConfigLoader.load(config_path="config/config.yaml")
    # Keep real call but avoid oversized output and timeout too low.
    cfg.llm.max_tokens = min(int(cfg.llm.max_tokens), 1024)
    cfg.llm.timeout = max(int(cfg.llm.timeout), 120)

    service = LLMServiceBase(config=cfg)

    room = MapEntity(
        id="map-lab-0001",
        name="实验室",
        description=Description(public=["你在一个安静的实验室里。", "守卫正看着你。"]),
    )
    player = CharacterEntity(
        id="char-player-0000",
        name="玩家",
        location=room.id,
        attributes={
            "fight": Attribute(id="fight", name="格斗", value=60, max_value=100, min_value=0),
            "investigation": Attribute(id="investigation", name="侦查", value=55, max_value=100, min_value=0),
            "stealth": Attribute(id="stealth", name="潜行", value=45, max_value=100, min_value=0),
        },
    )
    guard = CharacterEntity(
        id="char-guard-0001",
        name="守卫",
        location=room.id,
        attributes={"fight": Attribute(id="fight", name="格斗", value=50, max_value=100, min_value=0)},
    )

    world = WorldState()
    world.reset(
        WorldEntityStore(
            maps={room.id: room},
            characters={player.id: player, guard.id: guard},
            items={},
        )
    )

    return Phase2Engine(world_state=world, dm_max_retries=2, llm_service=service)


def _run_case(engine: Phase2Engine, index: int, text: str) -> Dict[str, Any]:
    turn_id = index
    trace_id = 9100 + index
    print(f"[case-{index}] input={text}")

    try:
        result = engine.run_turn(
            raw_input=text,
            actor_id="char-player-0000",
            turn_id=turn_id,
            trace_id=trace_id,
        )
        print(f"[case-{index}] route={result.get('route')}")
        print(f"[case-{index}] narrative_triggered={result.get('narrative_triggered')}")

        dm_info = result.get("dm", {}).get("llm_output", {}).get("intent_info", {})
        print(f"[case-{index}] dm.intent={dm_info.get('intent')}")
        print(f"[case-{index}] dm.routing_hint={dm_info.get('routing_hint')}")

        evo = result.get("evolution", {})
        print(f"[case-{index}] evo.visible_to_player={evo.get('visible_to_player')}")
        print(f"[case-{index}] evo.summary={evo.get('summary')}")

        return {
            "case": index,
            "input": text,
            "ok": True,
            "result": result,
        }
    except Exception as exc:
        print(f"[case-{index}] ERROR={type(exc).__name__}: {exc}")
        return {
            "case": index,
            "input": text,
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


def main() -> None:
    print("=== phase2 real local run start ===")
    engine = _build_engine()

    cases = [
        "我和守卫聊聊最近的情况",
        "我调查桌上的文件",
        "我攻击守卫",
    ]

    results: List[Dict[str, Any]] = []
    for i, text in enumerate(cases, start=1):
        results.append(_run_case(engine, i, text))

    out = {
        "run_at": datetime.now().isoformat(),
        "config": {
            "model": engine.dm_agent.llm_service.config.llm.model,
            "api_base": engine.dm_agent.llm_service.config.llm.api_base,
            "enable_reasoning": engine.dm_agent.llm_service.config.llm.enable_reasoning,
            "max_tokens": engine.dm_agent.llm_service.config.llm.max_tokens,
            "timeout": engine.dm_agent.llm_service.config.llm.timeout,
        },
        "cases": results,
    }

    out_path = Path("docs/phase/phase2/donelist/real_cases_local_run.json")
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    lines: List[str] = []
    lines.append("# Phase2 本地真实三用例联调日志")
    lines.append("")
    lines.append(f"run_at: {out['run_at']}")
    lines.append(f"model: {out['config']['model']}")
    lines.append(f"api_base: {out['config']['api_base']}")
    lines.append(f"enable_reasoning: {out['config']['enable_reasoning']}")
    lines.append(f"max_tokens: {out['config']['max_tokens']}")
    lines.append(f"timeout: {out['config']['timeout']}")
    lines.append("")

    for item in results:
        lines.append(f"## case-{item['case']}")
        lines.append(f"input: {item['input']}")
        lines.append(f"ok: {item['ok']}")
        if item["ok"]:
            result = item["result"]
            dm_info = result.get("dm", {}).get("llm_output", {}).get("intent_info", {})
            evo = result.get("evolution", {})
            lines.append(f"route: {result.get('route')}")
            lines.append(f"dm.intent: {dm_info.get('intent')}")
            lines.append(f"dm.routing_hint: {dm_info.get('routing_hint')}")
            lines.append(f"narrative_triggered: {result.get('narrative_triggered')}")
            lines.append(f"evolution.visible_to_player: {evo.get('visible_to_player')}")
            lines.append(f"evolution.summary: {evo.get('summary')}")
        else:
            lines.append(f"error: {item['error']}")
        lines.append("")

    log_path = Path("docs/phase/phase2/donelist/real_cases_local_run.log")
    log_path.write_text("\n".join(lines), encoding="utf-8")

    print(f"[done] wrote {out_path}")
    print(f"[done] wrote {log_path}")
    print("=== phase2 real local run end ===")


if __name__ == "__main__":
    main()
