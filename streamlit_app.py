from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
import time
from typing import Any, Dict, Generator, List, Optional

import streamlit as st

from src.agent.llm.service import LLMServiceBase
from src.config.loader import ConfigLoader
from src.data.model.input.agent_chain_input import E7CausalityChain
from src.engine.engine import Engine
from src.utils.agent_io_logger import AgentIoLogger
from main import (
    DEFAULT_WORLD_DIR,
    build_engine,
    check_endings_at_turn_start,
    extract_player_text,
    load_world_bundle,
)


REPO_ROOT = Path(__file__).resolve().parent
WORLD_DIR = REPO_ROOT / "world"
DEFAULT_CONFIG_PATH = "config/config.yaml"
STREAM_CHUNK_SIZE = 6
STREAM_CHUNK_DELAY_SEC = 0.04
ENGINE_POLL_INTERVAL_SEC = 0.05


@dataclass
class AppRuntime:
    """保存单个 Streamlit 会话中的引擎运行态。"""

    engine: Engine
    world_name: str
    world_dir: Path
    turn_limit: Optional[int]
    turn_limit_text: Optional[str]
    actor_id: str
    turn_id: int
    trace_id: int
    causality_chain: E7CausalityChain
    endings: List[Any]
    io_records: List[Dict[str, Any]]
    narrative_events: List[Dict[str, Any]]
    narrative_event_lock: Any
    turn_records: List[Dict[str, Any]]
    log_path: Path
    mode: str
    use_real_llm: bool
    config_path: str
    turn_id_step: int
    trace_id_step: int
    stream_chunk_size: int
    stream_chunk_delay_sec: float
    engine_poll_interval_sec: float
    game_over: bool
    ending_text: str


def inject_chat_layout_style() -> None:
    """注入纸质档案风布局样式，固定输入框并优化聊天可读性。"""

    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Noto+Serif+SC:wght@400;600;700&family=IBM+Plex+Sans+SC:wght@400;500;600&display=swap');

        :root {
            --paper-bg: #efe2c7;
            --paper-bg-deep: #e2d2b2;
            --ink: #2f2418;
            --ink-soft: #5b4a36;
            --card: rgba(253, 247, 232, 0.88);
            --card-border: rgba(101, 74, 45, 0.35);
            --accent: #845a2f;
            --accent-soft: rgba(132, 90, 47, 0.14);
        }

        html, body, [data-testid="stAppViewContainer"] {
            background:
                radial-gradient(circle at 10% 10%, rgba(255, 255, 255, 0.55), rgba(255, 255, 255, 0) 40%),
                radial-gradient(circle at 90% 15%, rgba(182, 141, 95, 0.25), rgba(182, 141, 95, 0) 30%),
                repeating-linear-gradient(
                    -8deg,
                    rgba(124, 89, 51, 0.04),
                    rgba(124, 89, 51, 0.04) 2px,
                    rgba(255, 255, 255, 0.03) 2px,
                    rgba(255, 255, 255, 0.03) 6px
                ),
                linear-gradient(160deg, var(--paper-bg), var(--paper-bg-deep));
            color: var(--ink);
        }

        [data-testid="stAppViewContainer"],
        [data-testid="stAppViewContainer"] div[data-testid="stMarkdownContainer"],
        [data-testid="stAppViewContainer"] div[data-testid="stMarkdownContainer"] p,
        [data-testid="stAppViewContainer"] div[data-testid="stMarkdownContainer"] li,
        [data-testid="stAppViewContainer"] div[data-testid="stMarkdownContainer"] a,
        [data-testid="stAppViewContainer"] div[data-testid="stMarkdownContainer"] strong,
        [data-testid="stAppViewContainer"] div[data-testid="stMarkdownContainer"] em,
        [data-testid="stAppViewContainer"] div[data-testid="stMarkdownContainer"] code,
        [data-testid="stSidebar"] label,
        [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] {
            font-family: 'IBM Plex Sans SC', 'Microsoft YaHei UI', sans-serif;
        }

        /*
         * Root-cause fix:
         * Do NOT set font-family on generic spans/buttons/inputs.
         * Streamlit material icons use ligature text (e.g. key_double_arrow_right)
         * and must keep their dedicated icon font.
         */
        [data-testid="stIconMaterial"],
        [data-testid="stIconMaterial"] *,
        [data-testid="stExpanderToggleIcon"],
        [data-testid="stExpanderToggleIcon"] *,
        [data-baseweb="icon"],
        [data-baseweb="icon"] *,
        [class*="material-symbols"],
        [class*="material-icons"] {
            font-family: 'Material Symbols Rounded', 'Material Symbols Outlined', 'Material Icons' !important;
            font-weight: normal !important;
            font-style: normal !important;
            letter-spacing: normal !important;
            text-transform: none !important;
            white-space: nowrap !important;
            direction: ltr !important;
            line-height: 1 !important;
            font-feature-settings: 'liga' !important;
            -webkit-font-feature-settings: 'liga' !important;
            -webkit-font-smoothing: antialiased;
        }

        .material-icons,
        .material-icons-outlined,
        .material-icons-round,
        .material-icons-sharp,
        .material-icons-two-tone,
        .material-symbols-outlined,
        .material-symbols-rounded,
        .material-symbols-sharp,
        [class*="material-symbols"] {
            font-family: 'Material Symbols Rounded', 'Material Symbols Outlined', 'Material Icons' !important;
            font-weight: normal;
            font-style: normal;
            letter-spacing: normal;
            text-transform: none;
            white-space: nowrap;
            direction: ltr;
            line-height: 1;
            -webkit-font-feature-settings: 'liga';
            -webkit-font-smoothing: antialiased;
            font-feature-settings: 'liga';
        }

        [data-testid="stAppViewContainer"] h1,
        [data-testid="stAppViewContainer"] h2,
        [data-testid="stAppViewContainer"] h3 {
            font-family: 'Noto Serif SC', 'STSong', serif;
            letter-spacing: 0.02em;
            color: var(--ink);
        }

        [data-testid="stSidebar"] {
            background: rgba(248, 239, 220, 0.88);
            border-right: 1px solid rgba(90, 68, 46, 0.22);
        }

        [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
        [data-testid="stSidebar"] label {
            color: var(--ink-soft);
        }

        div[data-testid="stChatMessage"] {
            background: var(--card);
            border: 1px solid var(--card-border);
            border-radius: 14px;
            box-shadow: 0 8px 20px rgba(58, 37, 17, 0.08);
            padding: 0.55rem 0.9rem;
            margin-bottom: 0.75rem;
            animation: paperIn 220ms ease-out;
        }

        div[data-testid="stChatMessage"][aria-label="Chat message from user"] {
            background: rgba(238, 219, 183, 0.9);
            border-color: rgba(117, 84, 47, 0.45);
        }

        div[data-testid="stChatMessage"] p {
            color: var(--ink);
            line-height: 1.7;
        }

        div[data-testid="stExpander"] {
            border: 1px dashed rgba(117, 84, 47, 0.45);
            border-radius: 10px;
            background: rgba(255, 252, 245, 0.6);
        }

        div[data-testid="stChatInput"] {
            position: fixed;
            bottom: 0.8rem;
            left: max(1rem, calc((100vw - 1200px) / 2));
            right: max(1rem, calc((100vw - 1200px) / 2));
            z-index: 999;
            background: rgba(246, 236, 214, 0.96);
            backdrop-filter: blur(6px);
            border: 1px solid rgba(117, 84, 47, 0.35);
            border-radius: 12px;
            box-shadow: 0 8px 18px rgba(80, 54, 29, 0.14);
            padding: 0.45rem 0.5rem 0.2rem;
        }

        div[data-testid="stChatInput"] textarea {
            color: var(--ink) !important;
        }

        div[data-testid="stAlert"] {
            border-radius: 10px;
            border: 1px solid rgba(117, 84, 47, 0.3);
        }

        @media (max-width: 768px) {
            div[data-testid="stChatInput"] {
                left: 0.4rem;
                right: 0.4rem;
                bottom: 0.4rem;
            }

            div[data-testid="stChatMessage"] {
                padding: 0.5rem 0.7rem;
            }
        }

        div[data-testid="stAppViewContainer"] .main {
            padding-bottom: 6rem;
        }

        @keyframes paperIn {
            from {
                opacity: 0;
                transform: translateY(8px);
            }
            to {
                opacity: 1;
                transform: translateY(0);
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


class CombinedIoLogger:
    """同时写入内存与文件的日志记录器。"""

    def __init__(self, file_logger: AgentIoLogger, bucket: List[Dict[str, Any]]) -> None:
        self._file_logger = file_logger
        self._bucket = bucket

    def __call__(self, record: Dict[str, Any]) -> None:
        self._bucket.append(record)
        self._file_logger(record)


@st.cache_data(show_spinner=False)
def list_world_dirs(world_dir: str) -> List[str]:
    """列出 world 目录下可供选择的世界子目录。"""

    base = Path(world_dir)
    if not base.exists():
        return []
    result: List[str] = []
    for path in base.iterdir():
        if not path.is_dir():
            continue
        if (path / "map").exists() and (path / "charactor").exists() and (path / "item").exists():
            result.append(path.name)
    return sorted(result)


@st.cache_data(show_spinner=False)
def read_world_preview(world_path: str) -> Dict[str, Any]:
    """读取分类 world 的概要信息，用于侧栏预览。"""

    bundle = load_world_bundle(Path(world_path))
    return {
        "scene_name": bundle.scene_name,
        "default_actor_id": bundle.actor_id,
        "turn_start": bundle.turn_start,
        "turn_limit": bundle.turn_limit,
        "ending_count": len(bundle.endings),
    }


def ensure_session_state() -> None:
    """初始化 Streamlit session state。"""

    if "runtime" not in st.session_state:
        st.session_state.runtime = None
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []
    if "debug_turn_idx" not in st.session_state:
        st.session_state.debug_turn_idx = 0
    if "debug_agent_name" not in st.session_state:
        st.session_state.debug_agent_name = ""


def apply_llm_overrides(
    config_path: str,
    *,
    api_key: str,
    api_base: str,
    model: str,
    enable_reasoning: bool,
    temperature: float,
    max_tokens: int,
    timeout: int,
) -> Any:
    """加载配置并套用侧栏输入的 LLM 覆盖项。"""

    config = ConfigLoader.load(config_path=config_path)

    if api_key.strip():
        config.llm.api_key = api_key.strip()
    if api_base.strip():
        config.llm.api_base = api_base.strip()
    if model.strip():
        config.llm.model = model.strip()

    config.llm.enable_reasoning = bool(enable_reasoning)
    config.llm.temperature = float(temperature)
    config.llm.max_tokens = int(max_tokens)
    config.llm.timeout = int(timeout)
    return config


def build_runtime(
    *,
    world_name: str,
    mode: str,
    use_real_llm: bool,
    config_path: str,
    api_key: str,
    api_base: str,
    model: str,
    enable_reasoning: bool,
    temperature: float,
    max_tokens: int,
    timeout: int,
) -> AppRuntime:
    """创建新引擎实例并返回运行态。"""

    world_dir = WORLD_DIR / world_name
    bundle = load_world_bundle(world_dir)

    io_bucket: List[Dict[str, Any]] = []
    narrative_event_bucket: List[Dict[str, Any]] = []
    narrative_event_lock = threading.Lock()
    file_logger = AgentIoLogger(base_dir=WORLD_DIR / "log")
    combined_logger = CombinedIoLogger(file_logger=file_logger, bucket=io_bucket)

    if not use_real_llm:
        raise ValueError("Streamlit 已对接统一 main 流程，仅支持真实 LLM 模式")

    config = apply_llm_overrides(
        config_path,
        api_key=api_key,
        api_base=api_base,
        model=model,
        enable_reasoning=enable_reasoning,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
    )
    llm_service = LLMServiceBase(config=config, io_recorder=combined_logger)
    engine = build_engine(
        world_bundle=bundle,
        config_path=config_path,
        use_real_llm=True,
        log_path=file_logger.log_path,
    )
    # 覆盖 engine 内部 llm service 为侧栏配置后的实例。
    engine.dm_agent.llm_service = llm_service
    engine.evolution_agent.llm_service = llm_service
    engine.state_agent.llm_service = llm_service
    engine.consistency_agent.llm_service = llm_service
    engine.npc_scheduler_agent.llm_service = llm_service
    engine.npc_performer_agent.llm_service = llm_service
    engine.narrative_agent.llm_service = llm_service
    engine.merger_agent.llm_service = llm_service

    if hasattr(engine, "set_narrative_event_listener"):
        def _stream_bridge(event: Dict[str, Any]) -> None:
            if not isinstance(event, dict):
                return
            with narrative_event_lock:
                narrative_event_bucket.append(event)

        engine.set_narrative_event_listener(_stream_bridge)

    restored_turn = bundle.turn_start
    turn_id_step = max(1, int(config.runtime.turn_id_step))
    trace_id_step = max(1, int(config.runtime.trace_id_step))
    trace_id_start = int(config.runtime.trace_id_start)
    stream_chunk_size = max(1, int(config.runtime.stream_chunk_size))
    stream_chunk_delay_sec = max(0.0, float(config.runtime.stream_chunk_delay_sec))
    engine_poll_interval_sec = max(0.0, float(config.runtime.engine_poll_interval_sec))

    narrative_info = getattr(engine, "_narrative_info", None)
    if narrative_info is not None:
        existing_turns = [int(item.turn) for item in getattr(narrative_info, "recent", [])]
        if existing_turns:
            restored_turn = max(restored_turn, max(existing_turns) + 1)

    return AppRuntime(
        engine=engine,
        world_name=world_name,
        world_dir=world_dir,
        turn_limit=bundle.turn_limit,
        turn_limit_text=bundle.turn_limit_text,
        actor_id=bundle.actor_id,
        turn_id=restored_turn,
        trace_id=max(trace_id_start, trace_id_start + (max(0, restored_turn - 1) * trace_id_step)),
        causality_chain=E7CausalityChain(),
        endings=bundle.endings,
        io_records=io_bucket,
        narrative_events=narrative_event_bucket,
        narrative_event_lock=narrative_event_lock,
        turn_records=[],
        log_path=file_logger.log_path,
        mode=mode,
        use_real_llm=use_real_llm,
        config_path=config_path,
        turn_id_step=turn_id_step,
        trace_id_step=trace_id_step,
        stream_chunk_size=stream_chunk_size,
        stream_chunk_delay_sec=stream_chunk_delay_sec,
        engine_poll_interval_sec=engine_poll_interval_sec,
        game_over=False,
        ending_text="",
    )


def collect_narrative_events(runtime: AppRuntime, cursor: int) -> tuple[List[Dict[str, Any]], int]:
    """Collect newly emitted narrative stream events from the runtime queue."""

    with runtime.narrative_event_lock:
        queue_size = len(runtime.narrative_events)
        safe_cursor = min(max(cursor, 0), queue_size)
        batch = [
            item.copy() if isinstance(item, dict) else {"event": "", "data": {}}
            for item in runtime.narrative_events[safe_cursor:queue_size]
        ]
    return batch, queue_size


def extract_player_visible_output(result: Dict[str, Any]) -> Dict[str, Any]:
    """从回合结果中提取玩家可见文本、片段聚合与 merger 信息。"""

    route = str(result.get("route", ""))
    fallback_error = result.get("fallback_error")

    narrative_payload = result.get("narrative", {}) if isinstance(result.get("narrative"), dict) else {}
    aggregated_raw = str(narrative_payload.get("aggregated_raw", "")).strip()

    fragments: List[Dict[str, Any]] = []
    for item in narrative_payload.get("fragments", []):
        if not isinstance(item, dict):
            continue
        content = str(item.get("content", "")).strip()
        if not content:
            continue
        fragments.append(
            {
                "fragment_id": str(item.get("fragment_id", "")),
                "source_kind": str(item.get("source_kind", "")),
                "source_id": str(item.get("source_id", "")),
                "content": content,
            }
        )

    if not aggregated_raw and fragments:
        aggregated_raw = "|".join(fragment["content"] for fragment in fragments)

    merger_payload = result.get("merger", {}) if isinstance(result.get("merger"), dict) else {}
    merger_text = str((merger_payload.get("llm_output") or {}).get("narrative_str", "")).strip()

    text = aggregated_raw or extract_player_text(result)
    if text:
        return {
            "text": text,
            "title": (
                "系统回应" if route == "rule_system_meta" else
                "DM 回复" if route == "dm_direct_reply" else
                "一致性阻断" if route == "consistency_blocked" else
                "系统降级" if isinstance(fallback_error, dict) and bool(fallback_error) else
                "叙事输出"
            ),
            "fragments": fragments,
            "aggregated_raw": aggregated_raw,
            "merger_text": merger_text,
        }

    return {
        "text": "本回合未产出可见文本。",
        "title": "空输出",
        "fragments": [],
        "aggregated_raw": "",
        "merger_text": "",
    }


def yield_chunks(chunks: List[str]) -> Generator[str, None, None]:
    """把文本分片转换成 st.write_stream 可消费的生成器。"""

    for chunk in chunks:
        if not chunk:
            continue
        yield chunk


def chunk_text_for_stream(text: str, *, chunk_size: int = STREAM_CHUNK_SIZE) -> List[str]:
    """把纯文本拆成小分片，用于模拟慢速流式展示。"""

    normalized = str(text or "")
    if not normalized:
        return []
    step = max(1, int(chunk_size))
    return [normalized[index : index + step] for index in range(0, len(normalized), step)]


def render_chunks_safely(
    chunks: List[str],
    *,
    placeholder=None,
    start_text: str = "",
    delay_sec: float = STREAM_CHUNK_DELAY_SEC,
) -> str:
    """安全渲染流式分片，避免触发 st.write_stream 对 pyarrow 的依赖。"""

    target = placeholder if placeholder is not None else st.empty()
    merged = str(start_text or "")
    if merged:
        target.markdown(merged)
    for chunk in chunks:
        if not chunk:
            continue
        merged += chunk
        target.markdown(merged)
        if delay_sec > 0:
            time.sleep(delay_sec)
    return merged


def extract_narrative_preview_from_io(io_records: List[Dict[str, Any]], start_index: int) -> str:
    """从本回合 I/O 记录中提取 narrative_agent 的首版文本。"""

    for item in io_records[start_index:]:
        if not isinstance(item, dict):
            continue
        if item.get("kind") != "agent_io" or str(item.get("agent_name", "")) != "narrative":
            continue
        output = item.get("output", {})
        if not isinstance(output, dict):
            continue
        llm_output = output.get("llm_output", {})
        if not isinstance(llm_output, dict):
            continue
        preview_text = str(llm_output.get("narrative_str", "")).strip()
        if preview_text:
            return preview_text
    return ""


def capture_turn_record(
    runtime: AppRuntime,
    *,
    user_text: str,
    result: Dict[str, Any],
    io_start: int,
    io_end: int,
) -> Dict[str, Any]:
    """把本回合结果与调试快照保存到 turn_records。"""

    snapshot = runtime.engine.world_state.get_snapshot()
    narrative_info = result.get("narrative_info")
    if not isinstance(narrative_info, dict):
        narrative_info = {}

    turn_record = {
        "turn_id": result.get("turn_id", runtime.turn_id),
        "trace_id": result.get("trace_id", runtime.trace_id),
        "route": result.get("route", ""),
        "user_text": user_text,
        "result": result,
        "world_snapshot": snapshot,
        "narrative_info": narrative_info,
        "parallel_timeline": result.get("parallel_timeline", []),
        "io_records": runtime.io_records[io_start:io_end],
    }
    runtime.turn_records.append(turn_record)
    return turn_record


def build_e_chain_view(turn_record: Dict[str, Any]) -> Dict[str, Any]:
    """把回合结果规整为 E1-E7 调试视图。"""

    result = turn_record.get("result", {}) if isinstance(turn_record.get("result"), dict) else {}
    dm_payload = result.get("dm", {}) if isinstance(result.get("dm"), dict) else {}
    dm_system = dm_payload.get("system_output", {}) if isinstance(dm_payload.get("system_output"), dict) else {}

    e1_view = {
        "raw_input": turn_record.get("user_text", ""),
        "trace_id": turn_record.get("trace_id"),
        "turn_id": turn_record.get("turn_id"),
        "dm_e1_view": dm_system.get("e1_view", {}),
    }

    e2_view = turn_record.get("narrative_info", {})

    e3_view = result.get("e3", {})

    e4_view = {
        "evolution": result.get("evolution", {}),
        "scheduler": result.get("npcscheduler", {}),
        "npc_performer_chain": result.get("npc_performer_chain", []),
    }

    e5_view = result.get("state", {})

    e6_view = result.get("narrative", {})

    npc_chain = []
    for item in result.get("npc_performer_chain", []):
        if not isinstance(item, dict):
            continue
        npc_chain.append(
            {
                "npc_id": item.get("npc_id"),
                "intent": item.get("intent"),
                "e7": item.get("e7"),
            }
        )

    e7_view = {
        "evolution_e7": (result.get("evolution", {}) if isinstance(result.get("evolution"), dict) else {}).get("e7", {}),
        "npc_e7": npc_chain,
        "merger": result.get("merger", {}),
    }

    return {
        "E1_input": e1_view,
        "E2_narrative_pool": e2_view,
        "E3_rule_result": e3_view,
        "E4_step_result": e4_view,
        "E5_world_projection": e5_view,
        "E6_narrative_projection": e6_view,
        "E7_causality": e7_view,
    }


def group_agent_records(turn_record: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """按 agent_name 聚合本回合 I/O 记录。"""

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for item in turn_record.get("io_records", []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("agent_name", "unknown"))
        grouped.setdefault(name, []).append(item)
    return grouped


def render_sidebar() -> Dict[str, Any]:
    """渲染侧栏配置并返回当前设置。"""

    st.sidebar.header("游玩与配置")

    worlds = list_world_dirs(str(WORLD_DIR))
    if not worlds:
        st.sidebar.error("world 目录下未找到分类世界目录。")
        return {"can_run": False}

    default_world = DEFAULT_WORLD_DIR.name if DEFAULT_WORLD_DIR.name in worlds else worlds[0]
    selected_world = st.sidebar.selectbox("世界选择", options=worlds, index=worlds.index(default_world))
    preview = read_world_preview(str(WORLD_DIR / selected_world))
    st.sidebar.caption(f"场景: {preview['scene_name']}")
    st.sidebar.caption(f"默认角色: {preview['default_actor_id']}")
    st.sidebar.caption(f"结局数: {preview['ending_count']}")

    mode = st.sidebar.selectbox("运行模式", options=["unified-main"], index=0)
    use_real_llm = st.sidebar.checkbox("使用真实 LLM", value=True)

    config_path = st.sidebar.text_input("配置文件路径", value=DEFAULT_CONFIG_PATH)

    st.sidebar.subheader("LLM 参数")
    api_key = st.sidebar.text_input("API Key(可覆盖配置)", value="", type="password")
    api_base = st.sidebar.text_input("API Base(可覆盖配置)", value="")
    model = st.sidebar.text_input("模型名(可覆盖配置)", value="")
    enable_reasoning = st.sidebar.checkbox("启用推理", value=False)
    temperature = st.sidebar.slider("temperature", min_value=0.0, max_value=2.0, value=0.7, step=0.1)
    max_tokens = st.sidebar.number_input("max_tokens", min_value=100, max_value=8192, value=3000, step=100)
    timeout = st.sidebar.number_input("timeout(秒)", min_value=1, max_value=120, value=30, step=1)

    debug_mode = st.sidebar.checkbox("开启 Debug UI", value=True)

    col_left, col_right = st.sidebar.columns(2)
    rebuild_clicked = col_left.button("重建引擎", use_container_width=True)
    clear_chat_clicked = col_right.button("清空聊天", use_container_width=True)

    return {
        "can_run": True,
        "world_name": selected_world,
        "mode": mode,
        "use_real_llm": use_real_llm,
        "config_path": config_path,
        "api_key": api_key,
        "api_base": api_base,
        "model": model,
        "enable_reasoning": enable_reasoning,
        "temperature": float(temperature),
        "max_tokens": int(max_tokens),
        "timeout": int(timeout),
        "debug_mode": debug_mode,
        "rebuild_clicked": rebuild_clicked,
        "clear_chat_clicked": clear_chat_clicked,
    }


def render_chat_history() -> None:
    """渲染累计聊天记录。"""

    for message in st.session_state.chat_history:
        role = message.get("role", "assistant")
        content = str(message.get("content", ""))
        meta = message.get("meta", {}) if isinstance(message.get("meta"), dict) else {}
        with st.chat_message(role):
            st.markdown(content)
            if role == "assistant" and meta:
                merger_text = str(meta.get("merger_text", "")).strip()
                if merger_text and merger_text != content.strip():
                    with st.expander("查看 merger 压缩结果", expanded=False):
                        st.markdown(merger_text)
                st.caption(f"trace_id={meta.get('trace_id')} | turn_id={meta.get('turn_id')} | route={meta.get('route')}")


def handle_user_turn(runtime: AppRuntime, user_text: str) -> None:
    """执行单回合并把结果写入聊天与调试记录。"""

    if runtime.game_over:
        with st.chat_message("assistant"):
            st.warning(runtime.ending_text or "结局已达成，本局已结束。")
        return

    if runtime.turn_limit is not None and runtime.turn_id > runtime.turn_limit:
        runtime.game_over = True
        runtime.ending_text = runtime.turn_limit_text or f"你没有在 {runtime.turn_limit} 个回合内逃出生天，竖锯的机关彻底封死了出口。"
        with st.chat_message("assistant"):
            st.success(f"结局达成: {runtime.ending_text}")
        st.session_state.chat_history.append(
            {
                "role": "assistant",
                "content": f"结局达成: {runtime.ending_text}",
                "meta": {
                    "trace_id": runtime.trace_id,
                    "turn_id": runtime.turn_id,
                    "route": "turn_limit",
                },
            }
        )
        return

    hit_ending = check_endings_at_turn_start(runtime.engine.rule_system, runtime.engine.world_state, runtime.endings)
    if hit_ending is not None:
        runtime.game_over = True
        runtime.ending_text = hit_ending.text
        with st.chat_message("assistant"):
            st.success(f"结局达成: {hit_ending.text}")
        st.session_state.chat_history.append(
            {
                "role": "assistant",
                "content": f"结局达成: {hit_ending.text}",
                "meta": {
                    "trace_id": runtime.trace_id,
                    "turn_id": runtime.turn_id,
                    "route": "ending",
                },
            }
        )
        return

    st.session_state.chat_history.append({"role": "user", "content": user_text, "meta": {}})
    with st.chat_message("user"):
        st.markdown(user_text)

    io_start = len(runtime.io_records)
    with runtime.narrative_event_lock:
        narrative_cursor = len(runtime.narrative_events)

    result_holder: Dict[str, Any] = {"result": None, "error": None}

    def _run_turn_worker() -> None:
        try:
            result_holder["result"] = runtime.engine.run_turn(
                raw_input=user_text,
                actor_id=runtime.actor_id,
                turn_id=runtime.turn_id,
                trace_id=runtime.trace_id,
                causality_chain=runtime.causality_chain,
            )
        except Exception as worker_exc:
            result_holder["error"] = worker_exc

    worker = threading.Thread(target=_run_turn_worker, daemon=True)
    worker.start()

    stream_preview_placeholder = st.empty()
    status_placeholder = st.empty()
    live_fragments: Dict[str, Dict[str, Any]] = {}
    live_fragment_order: List[str] = []

    def apply_narrative_event(event: Dict[str, Any]) -> None:
        if not isinstance(event, dict):
            return
        event_name = str(event.get("event", ""))
        if not event_name.startswith("narrative.fragment."):
            return
        data = event.get("data", {})
        if not isinstance(data, dict):
            return

        fragment_id = str(data.get("fragment_id", "")).strip()
        if not fragment_id:
            return

        if fragment_id not in live_fragments:
            live_fragments[fragment_id] = {
                "source_kind": str(data.get("source_kind", "")),
                "source_id": str(data.get("source_id", "")),
                "content": "",
                "completed": False,
            }
            live_fragment_order.append(fragment_id)

        if event_name == "narrative.fragment.delta":
            live_fragments[fragment_id]["content"] += str(data.get("delta", ""))
        elif event_name == "narrative.fragment.completed":
            completed_text = str(data.get("content", "")).strip()
            if completed_text:
                live_fragments[fragment_id]["content"] = completed_text
            live_fragments[fragment_id]["completed"] = True

    def render_live_fragments() -> None:
        with stream_preview_placeholder.container():
            for fragment_id in live_fragment_order:
                fragment = live_fragments.get(fragment_id, {})
                source_kind = str(fragment.get("source_kind", ""))
                source_id = str(fragment.get("source_id", ""))
                content = str(fragment.get("content", ""))
                completed = bool(fragment.get("completed", False))

                if source_kind == "player":
                    title = "玩家叙事"
                elif source_kind == "npc":
                    title = f"NPC {source_id} 叙事"
                else:
                    title = "叙事片段"

                with st.chat_message("assistant"):
                    st.caption(f"{title} · {'已完成' if completed else '流式生成中'}")
                    st.markdown(content or "...")

    while worker.is_alive():
        new_events, narrative_cursor = collect_narrative_events(runtime, narrative_cursor)
        for event in new_events:
            apply_narrative_event(event)

        if live_fragment_order:
            render_live_fragments()
            status_placeholder.caption("narrative_agent 流式输出中...")
        else:
            status_placeholder.caption("正在处理主链路...")

        time.sleep(runtime.engine_poll_interval_sec)

    worker.join()
    trailing_events, narrative_cursor = collect_narrative_events(runtime, narrative_cursor)
    for event in trailing_events:
        apply_narrative_event(event)
    if live_fragment_order:
        render_live_fragments()
    status_placeholder.empty()

    if result_holder.get("error") is not None:
        exc = result_holder["error"]
        error_text = f"回合执行失败: {exc}"
        stream_preview_placeholder.empty()
        with st.chat_message("assistant"):
            st.error(error_text)
        st.session_state.chat_history.append(
            {
                "role": "assistant",
                "content": error_text,
                "meta": {
                    "trace_id": runtime.trace_id,
                    "turn_id": runtime.turn_id,
                    "route": "exception",
                },
            }
        )
        runtime.turn_records.append(
            {
                "turn_id": runtime.turn_id,
                "trace_id": runtime.trace_id,
                "route": "exception",
                "user_text": user_text,
                "result": {
                    "error": str(exc),
                },
                "world_snapshot": runtime.engine.world_state.get_snapshot(),
                "narrative_info": {},
                "parallel_timeline": [],
                "io_records": runtime.io_records[io_start:len(runtime.io_records)],
            }
        )
        st.session_state.debug_turn_idx = max(len(runtime.turn_records) - 1, 0)
        return

    result = result_holder.get("result") if isinstance(result_holder.get("result"), dict) else {}

    io_end = len(runtime.io_records)
    turn_record = capture_turn_record(
        runtime,
        user_text=user_text,
        result=result,
        io_start=io_start,
        io_end=io_end,
    )

    visible = extract_player_visible_output(result)
    display_text = str(visible.get("text", ""))
    merger_text = str(visible.get("merger_text", "")).strip()
    fragments = visible.get("fragments", []) if isinstance(visible.get("fragments"), list) else []
    aggregated_raw = str(visible.get("aggregated_raw", "")).strip()

    stream_preview_placeholder.empty()
    with st.chat_message("assistant"):
        st.caption(str(visible.get("title", "系统输出")))
        st.markdown(display_text)
        if merger_text and merger_text != display_text.strip():
            with st.expander("查看 merger 压缩结果", expanded=False):
                st.markdown(merger_text)
        st.caption(
            f"trace_id={turn_record.get('trace_id')} | "
            f"turn_id={turn_record.get('turn_id')} | "
            f"route={turn_record.get('route')}"
        )

    st.session_state.chat_history.append(
        {
            "role": "assistant",
            "content": display_text,
            "meta": {
                "trace_id": turn_record.get("trace_id"),
                "turn_id": turn_record.get("turn_id"),
                "route": turn_record.get("route"),
                "merger_text": merger_text,
                "aggregated_raw": aggregated_raw,
                "fragments": fragments,
            },
        }
    )

    runtime.turn_id += runtime.turn_id_step
    runtime.trace_id += runtime.trace_id_step
    runtime.causality_chain = E7CausalityChain()
    st.session_state.debug_turn_idx = max(len(runtime.turn_records) - 1, 0)


def render_turn_navigator(runtime: AppRuntime) -> Optional[Dict[str, Any]]:
    """渲染回合左右切换按钮并返回当前选中回合。"""

    if not runtime.turn_records:
        st.info("当前还没有回合记录。请先在游玩区输入内容。")
        return None

    max_idx = len(runtime.turn_records) - 1
    st.session_state.debug_turn_idx = min(max(st.session_state.debug_turn_idx, 0), max_idx)

    col_prev, col_mid, col_next = st.columns([1, 4, 1])

    if col_prev.button("⬅ 上一回合", use_container_width=True):
        st.session_state.debug_turn_idx = max(st.session_state.debug_turn_idx - 1, 0)

    turn_options = [f"回合 {item['turn_id']} | trace {item['trace_id']}" for item in runtime.turn_records]
    selected = col_mid.selectbox(
        "回合定位",
        options=list(range(len(turn_options))),
        index=st.session_state.debug_turn_idx,
        format_func=lambda idx: turn_options[idx],
    )
    st.session_state.debug_turn_idx = selected

    if col_next.button("下一回合 ➡", use_container_width=True):
        st.session_state.debug_turn_idx = min(st.session_state.debug_turn_idx + 1, max_idx)

    return runtime.turn_records[st.session_state.debug_turn_idx]


def render_agent_io_panel(turn_record: Dict[str, Any]) -> None:
    """渲染按 agent 切换的输入输出视图。"""

    st.subheader("Agent 输入输出")
    grouped = group_agent_records(turn_record)
    if not grouped:
        st.info("本回合没有可展示的 agent 记录。")
        return

    agent_names = sorted(grouped.keys())
    if st.session_state.debug_agent_name not in agent_names:
        st.session_state.debug_agent_name = agent_names[0]

    selected_agent = st.radio(
        "选择 Agent",
        options=agent_names,
        horizontal=True,
        index=agent_names.index(st.session_state.debug_agent_name),
    )
    st.session_state.debug_agent_name = selected_agent

    entries = grouped[selected_agent]
    index_key = f"agent_entry_idx::{selected_agent}"
    if index_key not in st.session_state:
        st.session_state[index_key] = 0
    st.session_state[index_key] = min(max(st.session_state[index_key], 0), len(entries) - 1)

    nav_prev, nav_mid, nav_next = st.columns([1, 3, 1])
    if nav_prev.button("⬅", key=f"agent_prev::{selected_agent}"):
        st.session_state[index_key] = max(st.session_state[index_key] - 1, 0)
    nav_mid.markdown(f"记录 {st.session_state[index_key] + 1}/{len(entries)}")
    if nav_next.button("➡", key=f"agent_next::{selected_agent}"):
        st.session_state[index_key] = min(st.session_state[index_key] + 1, len(entries) - 1)

    entry = entries[st.session_state[index_key]]
    st.json(entry, expanded=False)

    if "input" in entry or "output" in entry:
        io_left, io_right = st.columns(2)
        with io_left:
            st.caption("输入")
            st.json(entry.get("input", {}), expanded=False)
        with io_right:
            st.caption("输出")
            st.json(entry.get("output", {}), expanded=False)


def render_debug_panel(runtime: AppRuntime) -> None:
    """渲染调试视图：Trace、快照、E1-E7、agent I/O。"""

    st.header("Debug 面板")
    turn_record = render_turn_navigator(runtime)
    if turn_record is None:
        return

    result = turn_record.get("result", {}) if isinstance(turn_record.get("result"), dict) else {}
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Turn", str(turn_record.get("turn_id", "-")))
    col2.metric("Trace", str(turn_record.get("trace_id", "-")))
    col3.metric("Route", str(turn_record.get("route", "-")))
    col4.metric("Terminated", str(bool(result.get("terminated", False))))

    if runtime.turn_records:
        st.caption(f"日志文件: {runtime.log_path}")

    st.subheader("E1-E7 链路日志")
    st.json(build_e_chain_view(turn_record), expanded=False)

    st.subheader("并发分支时间线")
    timeline = turn_record.get("parallel_timeline", [])
    if timeline:
        # 避免触发 Streamlit dataframe 对 pyarrow 的硬依赖（Windows 下常见 DLL 问题）。
        st.json(timeline, expanded=False)
    else:
        st.info("本回合没有并发时间线数据。")

    world_col, narrative_col = st.columns(2)
    with world_col:
        st.subheader("世界快照")
        st.json(turn_record.get("world_snapshot", {}), expanded=False)
    with narrative_col:
        st.subheader("叙事池")
        st.json(turn_record.get("narrative_info", {}), expanded=False)

    render_agent_io_panel(turn_record)


def render_runtime_banner(runtime: AppRuntime) -> None:
    """渲染运行态摘要信息。"""

    llm_mode = "real" if runtime.use_real_llm else "fake"
    st.caption(
        f"world={runtime.world_name} | mode={runtime.mode} | llm={llm_mode} | "
        f"next_turn={runtime.turn_id} | next_trace={runtime.trace_id}"
    )

    actor = runtime.engine.world_state.get_character(runtime.actor_id)
    current_map = runtime.engine.world_state.get_map(actor.location)
    st.info(f"当前角色: {actor.name} ({runtime.actor_id}) | 位置: {current_map.name} ({current_map.id})")
    if runtime.turn_limit is not None:
        st.caption(f"回合上限: {runtime.turn_limit}")
    if runtime.game_over:
        st.success(f"当前已达成结局: {runtime.ending_text}")


def main() -> None:
    """应用入口。"""

    st.set_page_config(page_title="Engine Play & Debug UI", layout="wide")
    inject_chat_layout_style()
    st.title("LLM 文字冒险引擎 ")

    ensure_session_state()
    sidebar_state = render_sidebar()
    if not sidebar_state.get("can_run", False):
        st.stop()

    if sidebar_state.get("clear_chat_clicked"):
        st.session_state.chat_history = []

    should_build = sidebar_state.get("rebuild_clicked") or st.session_state.runtime is None
    if should_build:
        with st.spinner("正在初始化引擎..."):
            st.session_state.runtime = build_runtime(
                world_name=str(sidebar_state["world_name"]),
                mode=str(sidebar_state["mode"]),
                use_real_llm=bool(sidebar_state["use_real_llm"]),
                config_path=str(sidebar_state["config_path"]),
                api_key=str(sidebar_state["api_key"]),
                api_base=str(sidebar_state["api_base"]),
                model=str(sidebar_state["model"]),
                enable_reasoning=bool(sidebar_state["enable_reasoning"]),
                temperature=float(sidebar_state["temperature"]),
                max_tokens=int(sidebar_state["max_tokens"]),
                timeout=int(sidebar_state["timeout"]),
            )
            st.session_state.chat_history = []
            st.session_state.debug_turn_idx = 0
            st.session_state.debug_agent_name = ""

    runtime: AppRuntime = st.session_state.runtime
    render_runtime_banner(runtime)

    game_tab, debug_tab = st.tabs(["游玩", "Debug"])

    with game_tab:
        st.caption("可用快捷命令: \\look 查看环境, \\inventory 查看背包")
        render_chat_history()
        user_text = st.chat_input("输入玩家行为，例如: 我调查桌上的笔记")
        if user_text:
            handle_user_turn(runtime, user_text.strip())

    if sidebar_state.get("debug_mode"):
        with debug_tab:
            render_debug_panel(runtime)
    else:
        with debug_tab:
            st.info("Debug 模式未开启，请在侧栏勾选开启。")


if __name__ == "__main__":
    main()
