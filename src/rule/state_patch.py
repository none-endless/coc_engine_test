from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.data.model.base import WorldEntityStore
from src.data.model.world_state import WorldState
from src.data.model.agent_output import StateAgentOutput, StateChangeOp, StateOperator
from src.rule.dsl import DslError
from src.rule.rule_system import RuleSystem


ERROR_ENTITY_NOT_FOUND = "ENTITY_NOT_FOUND"
ERROR_FIELD_NOT_FOUND = "FIELD_NOT_FOUND"
ERROR_FIELD_NOT_MUTABLE = "FIELD_NOT_MUTABLE"
ERROR_FIELD_TYPE_MISMATCH = "FIELD_TYPE_MISMATCH"
ERROR_VALUE_OUT_OF_RANGE = "VALUE_OUT_OF_RANGE"
ERROR_DUPLICATE_ENTRY = "DUPLICATE_ENTRY"
ERROR_ENTRY_NOT_FOUND = "ENTRY_NOT_FOUND"
ERROR_INVALID_TARGET = "INVALID_TARGET"
ERROR_ASSERT_FAILED = "ASSERT_FAILED"


@dataclass
class StatePatchApplyResult:
    patch_id: str
    turn_id: int
    trace_id: int
    world_version: int
    applied_ops: int


class StatePatchError(ValueError):
    def __init__(self, code: str, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


class StatePatchRuntime:
    """Validate and apply state patch in deterministic execution order."""

    def __init__(self, world_state: WorldState) -> None:
        self.world_state = world_state
        self.rule_system = RuleSystem(world_state=world_state)

    def apply_patch(self, patch_output: StateAgentOutput) -> StatePatchApplyResult:
        patch_meta = patch_output.system_output.patch_meta
        snapshot = copy.deepcopy(self.world_state.get_snapshot())

        expected_version = patch_meta.expected_version
        if expected_version is not None and snapshot.get("version") != expected_version:
            raise StatePatchError(
                code=ERROR_ASSERT_FAILED,
                message="snapshot version mismatch",
                details={"expected_version": expected_version, "actual_version": snapshot.get("version")},
            )

        changes = list(patch_output.llm_output.changes)
        self._apply_asserts(changes=changes, snapshot=snapshot)

        working = copy.deepcopy(snapshot)
        ordered = self._reorder(changes)
        non_assert_count = 0
        for op in ordered:
            if op.op == StateOperator.ASSERT:
                continue
            self._apply_single_op(op=op, snapshot=working)
            non_assert_count += 1

        store = self.world_state.get_store_copy()
        store_payload = store.model_dump(mode="json")
        store_payload["maps"] = working.get("maps", {})
        store_payload["characters"] = working.get("characters", {})
        store_payload["items"] = working.get("items", {})
        new_store = WorldEntityStore.model_validate(store_payload)

        new_version = self.world_state.commit_store(
            store=new_store,
            expected_version=expected_version,
        )

        return StatePatchApplyResult(
            patch_id=patch_meta.patch_id or "",
            turn_id=patch_meta.turn_id,
            trace_id=patch_meta.trace_id,
            world_version=new_version,
            applied_ops=non_assert_count,
        )

    def _apply_asserts(self, changes: Sequence[StateChangeOp], snapshot: Dict[str, Any]) -> None:
        for op in changes:
            if op.op != StateOperator.ASSERT:
                continue
            if not op.condition:
                raise StatePatchError(ERROR_FIELD_TYPE_MISMATCH, "ASSERT requires condition")
            try:
                ok = self.rule_system.evaluate_assert(expression=op.condition, snapshot=snapshot)
            except DslError as exc:
                raise StatePatchError(ERROR_ASSERT_FAILED, f"ASSERT parse/eval failed: {exc}") from exc
            if not ok:
                raise StatePatchError(
                    ERROR_ASSERT_FAILED,
                    "ASSERT condition failed",
                    details={"condition": op.condition, "reason": op.reason or ""},
                )

    @staticmethod
    def _reorder(changes: Sequence[StateChangeOp]) -> List[StateChangeOp]:
        buckets = {
            StateOperator.ASSERT: [],
            StateOperator.MOVE: [],
            StateOperator.SET: [],
            StateOperator.UPDATE: [],
            StateOperator.ADD: [],
            StateOperator.REMOVE: [],
        }
        for op in changes:
            buckets[op.op].append(op)
        return (
            buckets[StateOperator.ASSERT]
            + buckets[StateOperator.MOVE]
            + buckets[StateOperator.SET]
            + buckets[StateOperator.UPDATE]
            + buckets[StateOperator.ADD]
            + buckets[StateOperator.REMOVE]
        )

    def _apply_single_op(self, op: StateChangeOp, snapshot: Dict[str, Any]) -> None:
        if not op.target_path:
            raise StatePatchError(ERROR_FIELD_NOT_FOUND, "target_path is required")

        entity_id, suffix = self._split_target_path(op.target_path)
        entity_type, entity = self._get_entity(snapshot=snapshot, entity_id=entity_id)
        self._validate_mutable(entity=entity, path_suffix=suffix)

        parent, key, current = self._resolve_parent_and_value(entity=entity, path=suffix)

        if op.op == StateOperator.MOVE:
            self._apply_move(
                entity_type=entity_type,
                entity=entity,
                path_suffix=suffix,
                value=op.value,
                snapshot=snapshot,
            )
            return

        if op.op == StateOperator.SET:
            if isinstance(current, (int, float)) and not isinstance(current, bool):
                raise StatePatchError(ERROR_FIELD_TYPE_MISMATCH, "SET cannot target number field")
            self._validate_assignable(current=current, value=op.value)
            self._set_value(parent=parent, key=key, value=op.value)
            return

        if op.op == StateOperator.UPDATE:
            if not isinstance(current, (int, float)) or isinstance(current, bool):
                raise StatePatchError(ERROR_FIELD_TYPE_MISMATCH, "UPDATE can only target number field")
            if not isinstance(op.value, (int, float)):
                raise StatePatchError(ERROR_FIELD_TYPE_MISMATCH, "UPDATE value must be number")
            self._validate_number_range(entity=entity, path_suffix=suffix, next_value=float(op.value))
            self._set_value(parent=parent, key=key, value=op.value)
            return

        if op.op in {StateOperator.ADD, StateOperator.REMOVE}:
            if not isinstance(current, list):
                raise StatePatchError(ERROR_FIELD_TYPE_MISMATCH, "list operation targets non-list field")
            if not isinstance(op.value, list):
                raise StatePatchError(ERROR_FIELD_TYPE_MISMATCH, "ADD/REMOVE value must be list")
            if op.op == StateOperator.ADD:
                for entry in op.value:
                    if entry in current:
                        raise StatePatchError(
                            ERROR_DUPLICATE_ENTRY,
                            "ADD duplicates existing entry",
                            details={"target_path": op.target_path, "entry": entry},
                        )
                    current.append(entry)
            else:
                for entry in op.value:
                    if entry not in current:
                        raise StatePatchError(
                            ERROR_ENTRY_NOT_FOUND,
                            "REMOVE target not found",
                            details={"target_path": op.target_path, "entry": entry},
                        )
                    current.remove(entry)
            return

        raise StatePatchError(ERROR_FIELD_TYPE_MISMATCH, f"unsupported op: {op.op}")

    @staticmethod
    def _split_target_path(path: str) -> Tuple[str, str]:
        if "." not in path:
            raise StatePatchError(ERROR_FIELD_NOT_FOUND, "target_path must include entity and field path")
        entity_id, suffix = path.split(".", 1)
        if not entity_id or not suffix:
            raise StatePatchError(ERROR_FIELD_NOT_FOUND, "invalid target_path")
        return entity_id, suffix

    @staticmethod
    def _split_parts(path: str) -> List[Tuple[str, Optional[int]]]:
        parts = path.split(".")
        out: List[Tuple[str, Optional[int]]] = []
        for raw in parts:
            if "[" in raw and raw.endswith("]"):
                name, index_raw = raw[:-1].split("[", 1)
                out.append((name, int(index_raw)))
            else:
                out.append((raw, None))
        return out

    @staticmethod
    def _get_entity(snapshot: Dict[str, Any], entity_id: str) -> Tuple[str, Dict[str, Any]]:
        if entity_id.startswith("char-"):
            entity = snapshot.get("characters", {}).get(entity_id)
            entity_type = "character"
        elif entity_id.startswith("item-"):
            entity = snapshot.get("items", {}).get(entity_id)
            entity_type = "item"
        elif entity_id.startswith("map-"):
            entity = snapshot.get("maps", {}).get(entity_id)
            entity_type = "map"
        else:
            entity = None
            entity_type = "unknown"

        if entity is None:
            raise StatePatchError(ERROR_ENTITY_NOT_FOUND, f"entity not found: {entity_id}")
        return entity_type, entity

    @staticmethod
    def _validate_mutable(entity: Dict[str, Any], path_suffix: str) -> None:
        forbidden_prefixes = (
            "description.public",
            "char_index",
            "item_index",
            "memory.log",
            "narrative_state",
        )
        if path_suffix.startswith(forbidden_prefixes):
            raise StatePatchError(ERROR_FIELD_NOT_MUTABLE, f"field is not mutable: {path_suffix}")

        if path_suffix.startswith("extensions."):
            key = path_suffix[len("extensions.") :]
            if key not in entity.get("extensions", {}):
                raise StatePatchError(ERROR_FIELD_NOT_FOUND, f"extension field not found: {key}")

    def _resolve_parent_and_value(self, entity: Dict[str, Any], path: str) -> Tuple[Any, Any, Any]:
        parts = self._split_parts(path)
        current: Any = entity

        for idx, (name, index) in enumerate(parts):
            is_last = idx == len(parts) - 1

            if not isinstance(current, dict):
                raise StatePatchError(ERROR_FIELD_NOT_FOUND, f"field not found: {name}")
            if name not in current:
                raise StatePatchError(ERROR_FIELD_NOT_FOUND, f"field not found: {name}")
            value = current[name]

            if index is not None:
                if not isinstance(value, list):
                    raise StatePatchError(ERROR_FIELD_TYPE_MISMATCH, f"field is not list: {name}")
                if index < 0 or index >= len(value):
                    raise StatePatchError(ERROR_FIELD_NOT_FOUND, f"list index out of range: {name}[{index}]")
                if is_last:
                    return value, index, value[index]
                current = value[index]
                continue

            if is_last:
                return current, name, value
            current = value

        raise StatePatchError(ERROR_FIELD_NOT_FOUND, "invalid field path")

    @staticmethod
    def _set_value(parent: Any, key: Any, value: Any) -> None:
        if isinstance(parent, dict):
            parent[key] = value
            return
        if isinstance(parent, list):
            parent[key] = value
            return
        raise StatePatchError(ERROR_FIELD_NOT_FOUND, "invalid assignment target")

    @staticmethod
    def _validate_assignable(current: Any, value: Any) -> None:
        if current is None:
            return
        if isinstance(current, bool):
            if not isinstance(value, bool):
                raise StatePatchError(ERROR_FIELD_TYPE_MISMATCH, "expect bool value")
            return
        if isinstance(current, str):
            if not isinstance(value, str):
                raise StatePatchError(ERROR_FIELD_TYPE_MISMATCH, "expect string value")
            return
        if isinstance(current, list):
            if not isinstance(value, list):
                raise StatePatchError(ERROR_FIELD_TYPE_MISMATCH, "expect list value")
            return

    def _apply_move(
        self,
        *,
        entity_type: str,
        entity: Dict[str, Any],
        path_suffix: str,
        value: Any,
        snapshot: Dict[str, Any],
    ) -> None:
        if path_suffix != "location":
            raise StatePatchError(ERROR_FIELD_TYPE_MISMATCH, "MOVE must target location")
        if not isinstance(value, str):
            raise StatePatchError(ERROR_FIELD_TYPE_MISMATCH, "MOVE value must be string")

        if entity_type == "character":
            if value not in snapshot.get("maps", {}):
                raise StatePatchError(ERROR_INVALID_TARGET, f"invalid move target map: {value}")
        elif entity_type == "item":
            if value not in snapshot.get("maps", {}) and value not in snapshot.get("characters", {}):
                raise StatePatchError(ERROR_INVALID_TARGET, f"invalid move target: {value}")
        else:
            raise StatePatchError(ERROR_FIELD_TYPE_MISMATCH, "MOVE only supports character/item")

        entity["location"] = value

    @staticmethod
    def _validate_number_range(entity: Dict[str, Any], path_suffix: str, next_value: float) -> None:
        parts = path_suffix.split(".")
        if len(parts) == 3 and parts[0] in {"attributes", "status"} and parts[2] == "value":
            group = entity.get(parts[0], {})
            field_obj = group.get(parts[1], {})
            min_value = field_obj.get("min_value")
            max_value = field_obj.get("max_value")
            if min_value is not None and next_value < float(min_value):
                raise StatePatchError(ERROR_VALUE_OUT_OF_RANGE, f"value below min: {next_value} < {min_value}")
            if max_value is not None and next_value > float(max_value):
                raise StatePatchError(ERROR_VALUE_OUT_OF_RANGE, f"value above max: {next_value} > {max_value}")
