from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from src.agent.llm.service import LLMServiceBase
from src.config.loader import ConfigLoader
from src.data.model.base import CharacterEntity, ItemEntity, MapEntity, WorldEntityStore
from src.data.model.input.agent_chain_input import E7CausalityChain
from src.data.model.world_state import WorldState
from src.engine.bootstrap_validation import EngineBootstrapError
from src.engine.engine import Engine
from src.rule.rule_system import RuleSystem
from src.utils.agent_io_logger import AgentIoLogger


REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_WORLD_DIR = REPO_ROOT / "world" / "world1"
DEFAULT_CONFIG_PATH = "config/config.yaml"


@dataclass
class EndingRule:
	"""单条结局规则。"""

	ending_id: str
	condition: str
	text: str


@dataclass
class WorldBundle:
	"""按目录分类加载后的世界配置集合。"""

	scene_name: str
	actor_id: str
	turn_start: int
	turn_limit: Optional[int]
	turn_limit_text: Optional[str]
	maps: Dict[str, Any]
	characters: Dict[str, Any]
	items: Dict[str, Any]
	endings: List[EndingRule]


def _load_json_file(path: Path) -> Any:
	with path.open("r", encoding="utf-8") as handle:
		return json.load(handle)


def _collect_entity_dict(directory: Path, entity_kind: str) -> Dict[str, Any]:
	"""从目录收集 map/character/item，兼容多种 JSON 组织方式。"""
	if not directory.exists():
		return {}

	merged: Dict[str, Any] = {}
	for file_path in sorted(directory.glob("*.json")):
		payload = _load_json_file(file_path)

		if isinstance(payload, dict):
			if entity_kind in payload and isinstance(payload[entity_kind], dict):
				merged.update(payload[entity_kind])
				continue
			if "entities" in payload and isinstance(payload["entities"], dict):
				merged.update(payload["entities"])
				continue
			# 单实体字典
			if "id" in payload and isinstance(payload["id"], str):
				merged[payload["id"]] = payload
				continue
			# 直接就是 id->entity 映射
			if all(isinstance(v, dict) for v in payload.values()):
				merged.update(payload)
				continue

		if isinstance(payload, list):
			for entity in payload:
				if not isinstance(entity, dict) or "id" not in entity:
					raise ValueError(f"invalid {entity_kind} entry in {file_path}")
				merged[str(entity["id"])] = entity
			continue

		raise ValueError(f"unsupported {entity_kind} file format: {file_path}")

	return merged


def _collect_endings(directory: Path) -> List[EndingRule]:
	"""从 end 目录收集结局规则，条件使用 DSL 表达式。"""
	if not directory.exists():
		return []

	endings: List[EndingRule] = []
	for file_path in sorted(directory.glob("*.json")):
		payload = _load_json_file(file_path)
		entries: Sequence[Any]
		if isinstance(payload, dict) and isinstance(payload.get("endings"), list):
			entries = payload["endings"]
		elif isinstance(payload, list):
			entries = payload
		else:
			raise ValueError(f"unsupported endings file format: {file_path}")

		for index, item in enumerate(entries):
			if not isinstance(item, dict):
				raise ValueError(f"invalid ending entry in {file_path}: #{index}")
			condition = str(item.get("condition", "")).strip()
			if not condition:
				raise ValueError(f"ending condition is required: {file_path}#{index}")
			ending_text = str(item.get("text") or item.get("message") or "").strip()
			if not ending_text:
				raise ValueError(f"ending text/message is required: {file_path}#{index}")
			ending_id = str(item.get("id") or f"ending-{file_path.stem}-{index}")
			endings.append(EndingRule(ending_id=ending_id, condition=condition, text=ending_text))
	return endings


def _load_world_bundle(world_dir: Path) -> WorldBundle:
	"""按 world1 分类目录加载世界：map/charactor/item/end。"""
	meta_path = world_dir / "world.json"
	metadata: Dict[str, Any] = _load_json_file(meta_path) if meta_path.exists() else {}

	maps = _collect_entity_dict(world_dir / "map", "maps")
	characters = _collect_entity_dict(world_dir / "charactor", "characters")
	items = _collect_entity_dict(world_dir / "item", "items")
	endings = _collect_endings(world_dir / "end")

	if not maps:
		raise ValueError(f"no maps found under {world_dir / 'map'}")
	if not characters:
		raise ValueError(f"no characters found under {world_dir / 'charactor'}")

	actor_id = str(metadata.get("default_actor_id", "char-player-0000"))
	if actor_id not in characters:
		raise ValueError(f"default actor not found in characters: {actor_id}")

	return WorldBundle(
		scene_name=str(metadata.get("scene_name", world_dir.name)),
		actor_id=actor_id,
		turn_start=int(metadata.get("turn_start", 1)),
		turn_limit=_parse_optional_int(metadata.get("turn_limit")),
		turn_limit_text=_parse_optional_text(metadata.get("turn_limit_text")),
		maps=maps,
		characters=characters,
		items=items,
		endings=endings,
	)


def load_world_bundle(world_dir: Path) -> WorldBundle:
	"""公开包装：供外部（如 Streamlit）加载分类 world 配置。"""
	return _load_world_bundle(world_dir)


def _build_world_store(bundle: WorldBundle) -> WorldEntityStore:
	"""把分类目录数据构造成统一 WorldEntityStore。"""
	return WorldEntityStore(
		maps={map_id: MapEntity.model_validate(payload) for map_id, payload in bundle.maps.items()},
		characters={char_id: CharacterEntity.model_validate(payload) for char_id, payload in bundle.characters.items()},
		items={item_id: ItemEntity.model_validate(payload) for item_id, payload in bundle.items.items()},
	)


def _format_world_snapshot(world_state: WorldState, actor_id: str) -> str:
	actor = world_state.get_character(actor_id)
	current_map = world_state.get_map(actor.location)
	chars = [x.name for x in world_state.get_characters_at(actor.location)]
	items = [x.name for x in world_state.get_items_at(actor.location)]

	lines = [
		f"角色: {actor.name} ({actor.id})",
		f"位置: {current_map.name} ({current_map.id})",
		"地图描述:",
	]
	for text in current_map.description.public:
		lines.append(f"- {text}")
	lines.append(f"在场角色: {', '.join(chars) if chars else 'none'}")
	lines.append(f"可见物品: {', '.join(items) if items else 'none'}")
	return "\n".join(lines)


def _extract_player_text(result: Dict[str, Any]) -> str:
	"""从 turn result 中提取玩家可见文本。"""
	route = str(result.get("route", ""))
	if route == "rule_system_meta":
		payload = result.get("payload", {})
		return str(payload.get("result", ""))
	if route == "dm_direct_reply":
		return str(result.get("reply", ""))
	if route == "consistency_blocked":
		return str(result.get("message", "系统一致性阻断，流程终止。"))

	fallback_error = result.get("fallback_error")
	if isinstance(fallback_error, dict) and fallback_error:
		degraded = str(fallback_error.get("degraded_output") or fallback_error.get("message") or "系统降级，请稍后重试。")
		return degraded

	narrative = result.get("narrative", {}) if isinstance(result.get("narrative"), dict) else {}
	text = str((narrative.get("llm_output") or {}).get("narrative_str", "")).strip()
	if text:
		return text

	merger = result.get("merger", {}) if isinstance(result.get("merger"), dict) else {}
	merged = str((merger.get("llm_output") or {}).get("narrative_str", "")).strip()
	if merged:
		return merged

	evolution = result.get("evolution", {}) if isinstance(result.get("evolution"), dict) else {}
	summary = str(evolution.get("summary", "")).strip()
	return summary or "本回合没有生成可见输出。"


def extract_player_text(result: Dict[str, Any]) -> str:
	"""公开包装：供外部提取玩家可见文本。"""
	return _extract_player_text(result)


def _check_endings_at_turn_start(rule_system: RuleSystem, world_state: WorldState, endings: Sequence[EndingRule]) -> Optional[EndingRule]:
	"""每回合开始前检查结局条件，命中后直接结束游戏。"""
	snapshot = world_state.get_snapshot()
	for ending in endings:
		try:
			if rule_system.evaluate_assert(ending.condition, snapshot):
				return ending
		except Exception as exc:
			raise RuntimeError(f"ending condition evaluation failed: {ending.ending_id} -> {exc}") from exc
	return None


def _check_turn_limit_at_turn_start(turn_id: int, turn_limit: Optional[int]) -> bool:
	"""当当前回合号已经超过上限时，返回 True。"""
	return turn_limit is not None and turn_id > turn_limit


def _parse_optional_int(value: Any) -> Optional[int]:
	"""把可能为空的配置项解析成整数。"""
	if value is None:
		return None
	text = str(value).strip()
	if not text:
		return None
	return int(text)


def _parse_optional_text(value: Any) -> Optional[str]:
	"""把可能为空的配置项解析成文本。"""
	if value is None:
		return None
	text = str(value).strip()
	return text or None


def check_endings_at_turn_start(rule_system: RuleSystem, world_state: WorldState, endings: Sequence[EndingRule]) -> Optional[EndingRule]:
	"""公开包装：供外部在每回合开始前执行结局 DSL 判定。"""
	return _check_endings_at_turn_start(rule_system, world_state, endings)


def build_engine(
	*,
	world_bundle: WorldBundle,
	config_path: str,
	use_real_llm: bool,
	log_path: Optional[Path],
) -> Engine:
	"""统一构建全链路引擎，不暴露 phase 分支给玩家。"""
	store = _build_world_store(world_bundle)
	world_state = WorldState()
	world_state.reset(store)

	logger = AgentIoLogger(log_path.parent if log_path is not None else (REPO_ROOT / "world" / "log"))
	io_logger = logger

	if not use_real_llm:
		raise ValueError("main.py 仅支持真实 LLM 联调，请移除 --use-fake-llm")

	cfg = ConfigLoader.load(config_path=config_path)
	llm_service = LLMServiceBase(config=cfg, io_recorder=io_logger)

	# 统一使用完整链路（包含串行入口和并发分支），对外不再区分 phase。
	return Engine(
		world_state=world_state,
		mode="phase3",
		llm_service=llm_service,
		io_logger=io_logger,
		config_path=config_path,
		enable_persistence=False,
	)


def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(description="Unified playable main entry")
	parser.add_argument("--world-dir", default=str(DEFAULT_WORLD_DIR), help="world directory with map/charactor/item/end")
	parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="engine config file path")
	parser.add_argument("--actor-id", default="", help="override default actor id")
	parser.add_argument("--use-fake-llm", action="store_true", help="use local fake LLM service instead of real LLM")
	parser.add_argument("--show-debug", action="store_true", help="print debug payload each turn")
	return parser.parse_args()


def main() -> int:
	args = parse_args()
	world_dir = Path(args.world_dir)

	if not world_dir.exists():
		print(f"world dir not found: {world_dir}")
		return 1

	try:
		bundle = _load_world_bundle(world_dir)
	except Exception as exc:
		print(f"failed to load world bundle: {exc}")
		return 1

	actor_id = str(args.actor_id).strip() or bundle.actor_id

	log_path = REPO_ROOT / "world" / "log" / "agent_io.jsonl"
	try:
		engine = build_engine(
			world_bundle=bundle,
			config_path=str(args.config),
			use_real_llm=not bool(args.use_fake_llm),
			log_path=log_path,
		)
	except EngineBootstrapError as exc:
		print(str(exc))
		return 1
	except Exception as exc:
		print(f"engine bootstrap failed: {exc}")
		return 1

	if actor_id not in engine.world_state.get_snapshot().get("characters", {}):
		print(f"actor not found in world: {actor_id}")
		return 1

	rule_system = engine.rule_system
	cfg = ConfigLoader.load(config_path=str(args.config))
	turn_id = bundle.turn_start
	trace_id = int(cfg.runtime.trace_id_start)
	turn_id_step = max(1, int(cfg.runtime.turn_id_step))
	trace_id_step = max(1, int(cfg.runtime.trace_id_step))
	causality_chain = E7CausalityChain()

	print(f"scene: {bundle.scene_name}")
	print(f"world_dir: {world_dir}")
	print(f"actor_id: {actor_id}")
	print(f"llm_mode: {'real' if not args.use_fake_llm else 'fake'}")
	print(f"log: {log_path}")
	print("输入自然语言进行游玩，输入 :quit 退出，输入 :snapshot 查看当前快照。")
	print()
	print(_format_world_snapshot(engine.world_state, actor_id=actor_id))

	while True:
		# 每轮开始前先做结局判定。
		ending = _check_endings_at_turn_start(rule_system, engine.world_state, bundle.endings)
		if ending is not None:
			print("\n=== 结局达成 ===")
			print(f"id: {ending.ending_id}")
			print(ending.text)
			return 0
		if _check_turn_limit_at_turn_start(turn_id, bundle.turn_limit):
			print("\n=== 回合耗尽 ===")
			print(bundle.turn_limit_text or f"你没有在 {bundle.turn_limit} 个回合内逃出生天，竖锯的机关彻底封死了出口。")
			return 0

		raw_input = input("\n>>> ").strip()
		if not raw_input:
			continue

		if raw_input == ":quit":
			print("game closed.")
			return 0
		if raw_input == ":snapshot":
			print(_format_world_snapshot(engine.world_state, actor_id=actor_id))
			continue

		try:
			result = engine.run_turn(
				raw_input=raw_input,
				actor_id=actor_id,
				turn_id=turn_id,
				trace_id=trace_id,
				causality_chain=causality_chain,
			)
		except Exception as exc:
			print(f"turn failed: {exc}")
			continue

		print(_extract_player_text(result))
		print(f"[trace_id={trace_id} turn_id={turn_id} route={result.get('route', '')}]")
		if args.show_debug:
			print(json.dumps(result, ensure_ascii=False, indent=2))

		# e7 只用于单回合拼装，回合结束后重置。
		causality_chain = E7CausalityChain()
		turn_id += turn_id_step
		trace_id += trace_id_step


if __name__ == "__main__":
	raise SystemExit(main())