from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from pydantic import BaseModel, Field


class RuntimeConfig(BaseModel):
    turn_timeout: int = Field(default=30)
    log_level: str = Field(default="INFO")


class StorageConfig(BaseModel):
    memory_log_path: str = Field(default="data/memory.log")
    short_log_path: str = Field(default="data/shortLog.log")


class EngineConfig(BaseModel):
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)


class ConfigLoader:
    """Load config with precedence: CLI > ENV > FILE > DEFAULT."""

    ENV_PREFIX = "ER_"

    @classmethod
    def load(
        cls,
        config_path: Optional[str] = None,
        cli_overrides: Optional[Dict[str, Any]] = None,
        env: Optional[Mapping[str, str]] = None,
    ) -> EngineConfig:
        merged: Dict[str, Any] = EngineConfig().model_dump()

        if config_path:
            file_data = cls._load_file(config_path)
            cls._deep_merge(merged, file_data)

        env_data = cls._parse_env(env or os.environ)
        cls._deep_merge(merged, env_data)

        cli_data = cls._parse_cli_overrides(cli_overrides or {})
        cls._deep_merge(merged, cli_data)

        return EngineConfig.model_validate(merged)

    @staticmethod
    def _load_file(config_path: str) -> Dict[str, Any]:
        path = Path(config_path)
        if not path.exists():
            return {}

        text = path.read_text(encoding="utf-8")
        if not text.strip():
            return {}

        try:
            import yaml  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise RuntimeError("PyYAML is required to load YAML config files") from exc

        data = yaml.safe_load(text)
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise ValueError("config file root must be an object")
        return data

    @classmethod
    def _parse_env(cls, env: Mapping[str, str]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for key, value in env.items():
            if not key.startswith(cls.ENV_PREFIX):
                continue
            # ER_RUNTIME__TURN_TIMEOUT -> runtime.turn_timeout
            path = key[len(cls.ENV_PREFIX):].lower().replace("__", ".")
            cls._set_dotted_key(result, path, cls._coerce_value(value))
        return result

    @staticmethod
    def _parse_cli_overrides(cli_overrides: Dict[str, Any]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for key, value in cli_overrides.items():
            if not isinstance(key, str):
                raise TypeError("cli override keys must be str")
            ConfigLoader._set_dotted_key(result, key, value)
        return result

    @staticmethod
    def _set_dotted_key(target: Dict[str, Any], dotted_key: str, value: Any) -> None:
        parts = [p for p in dotted_key.split(".") if p]
        if not parts:
            return
        cur: Dict[str, Any] = target
        for part in parts[:-1]:
            nxt = cur.get(part)
            if not isinstance(nxt, dict):
                nxt = {}
                cur[part] = nxt
            cur = nxt
        cur[parts[-1]] = value

    @staticmethod
    def _deep_merge(base: Dict[str, Any], incoming: Dict[str, Any]) -> None:
        for key, value in incoming.items():
            if isinstance(value, dict) and isinstance(base.get(key), dict):
                ConfigLoader._deep_merge(base[key], value)
            else:
                base[key] = value

    @staticmethod
    def _coerce_value(raw: str) -> Any:
        value = raw.strip()
        if value.lower() in {"true", "false"}:
            return value.lower() == "true"

        if value.lower() in {"none", "null"}:
            return None

        for caster in (int, float):
            try:
                return caster(value)
            except ValueError:
                pass

        if (value.startswith("{") and value.endswith("}")) or (
            value.startswith("[") and value.endswith("]")
        ):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                pass

        return value
