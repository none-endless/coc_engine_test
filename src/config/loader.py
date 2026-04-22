from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from pydantic import BaseModel, Field, field_validator

from src.config.constants import DEFAULT_DEXTERITY_ATTRIBUTE_KEYS


class LlmConfig(BaseModel):
    api_key: str = Field(default="")
    model: str = Field(default="qwen3.6-flash")
    enable_reasoning: bool = Field(default=False)
    temperature: float = Field(default=0.7)
    max_tokens: int = Field(default=2000)
    timeout: int = Field(default=30)
    api_base: str = Field(default="https://dashscope.aliyuncs.com/compatible-mode/v1")
    agent_config_dir: str = Field(default="")

    @field_validator("api_key", mode="before")
    @classmethod
    def _normalize_api_key(cls, value: Any) -> str:
        if value is None:
            return ""
        return value

    @field_validator("agent_config_dir", mode="before")
    @classmethod
    def _normalize_agent_config_dir(cls, value: Any) -> str:
        if value is None:
            return ""
        return str(value)


class SystemConfig(BaseModel):
    max_retry_count: int = Field(default=3)
    retry_timeout_ms: int = Field(default=5000)
    fallback_error: str = Field(default="系统繁忙，请稍后重试")
    snapshot_interval: int = Field(default=10)
    dexterity_attribute_keys: List[str] = Field(default_factory=lambda: list(DEFAULT_DEXTERITY_ATTRIBUTE_KEYS))


class AgentDmConfig(BaseModel):
    memory_turns: int = Field(default=5)


class AgentNpcConfig(BaseModel):
    memory_turns: int = Field(default=15)
    shortlog_turns: int = Field(default=30)
    shortlog_merge_threshold: int = Field(default=5)
    max_actions_per_turn: int = Field(default=3)
    cooldown_turns: int = Field(default=1)


class AgentNarrativeConfig(BaseModel):
    recent_turns: int = Field(default=5)


class StorageWorldConfig(BaseModel):
    """世界真值持久化配置。"""

    sqlite_path: str = Field(default="", description="世界快照 SQLite 文件路径")


class StorageNarrativeConfig(BaseModel):
    """叙事真值持久化配置。"""

    sqlite_path: str = Field(default="", description="叙事真值 SQLite 文件路径")


class StorageConfig(BaseModel):
    """双真值池持久化配置。"""

    world: StorageWorldConfig = Field(default_factory=StorageWorldConfig)
    narrative: StorageNarrativeConfig = Field(default_factory=StorageNarrativeConfig)


class AgentConfig(BaseModel):
    dm: AgentDmConfig = Field(default_factory=AgentDmConfig)
    npc: AgentNpcConfig = Field(default_factory=AgentNpcConfig)
    narrative: AgentNarrativeConfig = Field(default_factory=AgentNarrativeConfig)


class DescriptionConfig(BaseModel):
    add_interval: int = Field(default=10)
    merge_threshold: int = Field(default=3)


class ConsistencyConfig(BaseModel):
    enabled: bool = Field(default=True)
    trigger_interval_turns: int = Field(default=10)
    description_add_threshold: int = Field(default=3)
    shortlog_threshold: int = Field(default=5)
    min_narration_candidates: int = Field(default=2)
    include_full_config_json: bool = Field(default=True)
    block_on_failure: bool = Field(default=True)
    narration_fallback_recent_changes: int = Field(default=3)


class RuntimeConfig(BaseModel):
    trace_id_start: int = Field(default=1000)
    trace_id_step: int = Field(default=1)
    turn_id_step: int = Field(default=1)
    stream_chunk_size: int = Field(default=6)
    stream_chunk_delay_sec: float = Field(default=0.04)
    engine_poll_interval_sec: float = Field(default=0.05)


class EngineConfig(BaseModel):
    llm: LlmConfig = Field(default_factory=LlmConfig)
    llm_agent_overrides: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    system: SystemConfig = Field(default_factory=SystemConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    description: DescriptionConfig = Field(default_factory=DescriptionConfig)
    consistency: ConsistencyConfig = Field(default_factory=ConsistencyConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)


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

        llm_data = merged.get("llm", {})
        if not isinstance(llm_data, dict):
            raise ValueError("llm config root must be an object")
        merged["llm_agent_overrides"] = cls._load_agent_llm_overrides(
            config_path=config_path,
            llm_data=llm_data,
        )

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
            # ER_SYSTEM__MAX_RETRY_COUNT -> system.max_retry_count
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

    @classmethod
    def _load_agent_llm_overrides(
        cls,
        *,
        config_path: Optional[str],
        llm_data: Dict[str, Any],
    ) -> Dict[str, Dict[str, Any]]:
        raw_dir = str(llm_data.get("agent_config_dir", "")).strip()
        if not raw_dir:
            return {}

        directory = cls._resolve_agent_config_dir(config_path=config_path, raw_dir=raw_dir)
        if not directory.exists() or not directory.is_dir():
            return {}

        base_llm = dict(llm_data)
        base_llm.pop("agent_config_dir", None)
        validated_base = LlmConfig.model_validate({**base_llm, "agent_config_dir": raw_dir}).model_dump(mode="python")
        validated_base.pop("agent_config_dir", None)

        overrides: Dict[str, Dict[str, Any]] = {}
        for file_path in sorted(directory.iterdir()):
            if not file_path.is_file():
                continue
            if file_path.suffix.lower() not in {".yaml", ".yml"}:
                continue

            loaded = cls._load_file(str(file_path))
            llm_override = loaded.get("llm", loaded)
            if not isinstance(llm_override, dict):
                raise ValueError(f"agent llm config must be object: {file_path}")

            merged = dict(validated_base)
            cls._deep_merge(merged, llm_override)
            LlmConfig.model_validate({**merged, "agent_config_dir": raw_dir})

            clean_override = dict(llm_override)
            clean_override.pop("agent_config_dir", None)
            overrides[file_path.stem] = clean_override

        return overrides

    @staticmethod
    def _resolve_agent_config_dir(*, config_path: Optional[str], raw_dir: str) -> Path:
        directory = Path(raw_dir)
        if directory.is_absolute():
            return directory
        if config_path:
            return Path(config_path).resolve().parent / directory
        return Path.cwd() / directory

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
