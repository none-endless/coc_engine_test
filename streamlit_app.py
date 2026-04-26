from __future__ import annotations

import base64
from dataclasses import dataclass
import html
import mimetypes
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
BACKGROUND_DIR = REPO_ROOT / "frontend_assets" / "local_backgrounds"
DEFAULT_CONFIG_PATH = "config/config.yaml"
STREAM_CHUNK_SIZE = 6
STREAM_CHUNK_DELAY_SEC = 0.04
ENGINE_POLL_INTERVAL_SEC = 0.05

SCENE_BACKGROUND_FILES = {
    "三顾茅庐": {
        "map-longzhong_path-0001": "optimized/sanguo_cottage_outer.jpg",
        "map-thatched_courtyard-0002": "optimized/sanguo_cottage_outer.jpg",
        "map-cottage_study-0003": "optimized/sanguo_cottage_inner.jpg",
    },
    "林黛玉到贾府": {
        "map-rong_gate-0001": "optimized/daiyu_gate.jpg",
        "map-corridor-0002": "optimized/daiyu_chuihua_gate.jpg",
        "map-grand_hall-0003": "optimized/daiyu_jiamu_room.jpg",
        "map-west_room-0004": "optimized/daiyu_jiamu_room.jpg",
    },
}


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
    """注入与参考项目前端一致的浅色舞台风样式。"""

    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;700&display=swap');

        :root {
            --lab-ink: #2d2219;
            --lab-muted: #6a4f3b;
            --lab-line: rgba(72, 52, 36, 0.22);
            --lab-card: rgba(255, 249, 241, 0.84);
            --lab-card-strong: rgba(255, 251, 246, 0.92);
            --lab-side: rgba(246, 241, 233, 0.96);
            --lab-accent: #7c5335;
            --lab-accent-deep: #3f594d;
            --lab-shadow: 0 18px 42px rgba(42, 30, 20, 0.14);
        }

        .stApp {
            background:
                linear-gradient(180deg, rgba(246, 241, 233, 0.96), rgba(236, 228, 215, 0.98)),
                radial-gradient(circle at top left, rgba(146, 94, 54, 0.10), transparent 28%),
                radial-gradient(circle at top right, rgba(63, 89, 77, 0.08), transparent 24%);
            color: var(--lab-ink);
        }

        .main .block-container {
            max-width: 1320px;
            padding-top: 1.1rem;
            padding-bottom: 7rem;
        }

        #MainMenu,
        footer,
        header,
        [data-testid="stToolbar"],
        [data-testid="stDecoration"],
        [data-testid="stStatusWidget"],
        [data-testid="stDeployButton"] {
            display: none !important;
        }

        .stage-shell {
            position: relative;
            overflow: hidden;
            aspect-ratio: 16 / 9;
            min-height: 400px;
            border-radius: 20px;
            border: 1px solid rgba(72, 52, 36, 0.26);
            box-shadow: 0 12px 28px rgba(42, 30, 20, 0.13);
            background: linear-gradient(145deg, rgba(56, 40, 28, 0.94), rgba(28, 20, 14, 0.97));
            margin-bottom: 0.7rem;
            contain: layout paint;
        }

        .stage-bg {
            position: absolute;
            inset: 0;
            width: 100%;
            height: 100%;
            object-fit: cover;
            object-position: center 42%;
            transform: none;
            filter: none;
        }

        .stage-fallback {
            position: absolute;
            inset: 0;
            display: flex;
            align-items: center;
            justify-content: center;
            color: #f8ede0;
            background:
                radial-gradient(circle at top, rgba(255, 225, 189, 0.21), transparent 36%),
                linear-gradient(135deg, rgba(122, 82, 49, 0.92), rgba(51, 36, 24, 0.98));
            font-size: 1.05rem;
        }

        .stage-mask {
            position: absolute;
            inset: 0;
            background:
                linear-gradient(180deg, rgba(16, 11, 8, 0.24) 0%, rgba(16, 11, 8, 0.10) 38%, rgba(16, 11, 8, 0.66) 100%);
        }

        .stage-overlay {
            position: absolute;
            inset: 0.95rem 0.95rem 0.45rem;
            z-index: 3;
            display: grid;
            grid-template-columns: minmax(240px, 1fr) minmax(300px, 1.22fr) minmax(240px, 1fr);
            grid-template-rows: auto auto 1fr;
            column-gap: 0.8rem;
            row-gap: 0.62rem;
            align-items: start;
        }

        .glass {
            border: 1px solid rgba(64, 47, 34, 0.28);
            border-radius: 12px;
            background: rgba(255, 249, 241, 0.90);
            backdrop-filter: none;
            padding: 0.64rem 0.8rem;
            color: var(--lab-ink);
            box-shadow: 0 4px 10px rgba(27, 18, 12, 0.10);
        }

        .stage-card-scene {
            grid-column: 1;
            grid-row: 1;
        }

        .stage-card-exits {
            grid-column: 3;
            grid-row: 1;
        }

        .stage-card-npcs {
            grid-column: 1;
            grid-row: 2;
        }

        .stage-card-items {
            grid-column: 3;
            grid-row: 2;
        }

        .glass h3 {
            margin: 0 0 0.3rem;
            color: var(--lab-ink);
            font-size: 1.08rem;
            line-height: 1.35;
        }

        .eyeline {
            margin: 0 0 0.34rem;
            color: var(--lab-muted);
            font-size: 0.74rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.06em;
        }

        .scene-meta {
            margin: 0 0 0.3rem;
            color: #4f3d2e;
            font-size: 0.84rem;
        }

        .scene-text {
            margin: 0;
            line-height: 1.58;
        }

        .pill-wrap {
            display: flex;
            flex-wrap: wrap;
            gap: 0.38rem;
        }

        .pill {
            display: inline-flex;
            align-items: center;
            border-radius: 999px;
            border: 1px solid rgba(97, 74, 55, 0.30);
            background: rgba(255, 255, 255, 0.62);
            padding: 0.2rem 0.5rem;
            font-size: 0.84rem;
            color: #3a2d23;
        }

        .sidebar-panel {
            margin-top: 0.55rem;
            padding: 0.68rem 0.75rem;
        }

        .sidebar-panel .pill-wrap {
            gap: 0.34rem;
        }

        .sidebar-panel .pill {
            font-size: 0.8rem;
            padding: 0.18rem 0.46rem;
        }

        .plain-list {
            display: flex;
            flex-direction: column;
            gap: 0.42rem;
        }

        .plain-line {
            color: #3a2d23;
            font-size: 0.83rem;
            line-height: 1.55;
        }

        .action-heading {
            margin-top: 0.35rem;
            margin-bottom: 0.15rem;
            color: var(--lab-ink);
            font-size: 1.18rem;
            font-weight: 700;
        }

        .action-panel {
            border: 1px solid rgba(85, 63, 46, 0.18);
            border-radius: 14px;
            background: rgba(255, 249, 241, 0.92);
            box-shadow: 0 4px 10px rgba(27, 18, 12, 0.06);
            padding: 0.75rem 0.85rem 0.55rem;
            margin-bottom: 0.8rem;
        }

        div[data-testid="stForm"] {
            border: 1px solid rgba(85, 63, 46, 0.18);
            border-radius: 14px;
            background: rgba(255, 249, 241, 0.92);
            box-shadow: 0 4px 10px rgba(27, 18, 12, 0.06);
            padding: 0.75rem 0.85rem 0.55rem;
            margin-bottom: 0.35rem;
        }

        .action-helper {
            margin-top: 0.28rem;
            color: var(--lab-muted);
            font-size: 0.88rem;
        }

        .live-story-shell {
            margin: 0.85rem 0 0.55rem;
            padding: 0.78rem 0.9rem 0.82rem;
        }

        .live-story-shell.live-story-pending {
            color: var(--lab-muted);
        }

        .live-story-label {
            margin: 0 0 0.38rem;
            color: var(--lab-muted);
            font-size: 0.74rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.06em;
        }

        .live-story-body {
            min-height: 2.3rem;
            color: var(--lab-ink);
            font-size: 1rem;
            line-height: 1.72;
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
            font-family: "Noto Sans SC", "Microsoft YaHei", sans-serif;
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
            font-family: "Noto Sans SC", "Microsoft YaHei", sans-serif;
            letter-spacing: 0;
            color: var(--lab-ink);
        }

        [data-testid="stAppViewContainer"] h1 {
            margin: 0 0 0.35rem;
            font-size: clamp(1.75rem, 2.1vw, 2.35rem);
            font-weight: 700;
        }

        [data-testid="stAppViewContainer"] h2,
        [data-testid="stAppViewContainer"] h3 {
            font-weight: 700;
        }

        [data-testid="stSidebar"] {
            background:
                linear-gradient(180deg, rgba(248, 243, 235, 0.98), rgba(235, 226, 213, 0.96));
            border-right: 1px solid var(--lab-line);
            box-shadow: 6px 0 16px rgba(42, 30, 20, 0.05);
        }

        [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
        [data-testid="stSidebar"] label {
            color: var(--lab-muted);
        }

        [data-testid="stSidebar"] h1,
        [data-testid="stSidebar"] h2,
        [data-testid="stSidebar"] h3,
        [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] strong {
            color: var(--lab-ink);
        }

        [data-testid="stSidebar"] hr {
            border-color: rgba(72, 52, 36, 0.16);
        }

        [data-testid="stTabs"] button {
            color: var(--lab-muted);
            font-weight: 600;
        }

        [data-testid="stTabs"] button[aria-selected="true"] {
            color: var(--lab-ink);
        }

        [data-testid="stTabs"] [data-baseweb="tab-highlight"] {
            background-color: var(--lab-accent);
        }

        [data-testid="stMetric"],
        div[data-testid="stExpander"],
        div[data-testid="stJson"],
        div[data-testid="stDataFrame"],
        [data-testid="stVerticalBlockBorderWrapper"] {
            border-radius: 14px;
        }

        [data-testid="stMetric"] {
            border: 1px solid rgba(85, 63, 46, 0.16);
            background: var(--lab-card);
            box-shadow: 0 4px 10px rgba(27, 18, 12, 0.06);
            padding: 0.65rem 0.75rem;
        }

        div[data-testid="stChatMessage"] {
            background: rgba(255, 251, 246, 0.80);
            border: 1px solid rgba(95, 71, 52, 0.14);
            border-radius: 14px;
            box-shadow: 0 4px 10px rgba(27, 18, 12, 0.07);
            padding: 0.35rem 0.55rem;
            margin-bottom: 0.62rem;
            color: var(--lab-ink);
            animation: labMessageIn 180ms ease-out;
            contain: layout paint;
        }

        div[data-testid="stChatMessage"][aria-label="Chat message from user"] {
            background: rgba(255, 249, 241, 0.92);
            border-color: rgba(124, 83, 53, 0.28);
        }

        div[data-testid="stChatMessage"] p {
            color: var(--lab-ink);
            line-height: 1.7;
        }

        div[data-testid="stExpander"] {
            border: 1px solid rgba(85, 63, 46, 0.18);
            border-radius: 14px;
            background: var(--lab-card);
            box-shadow: 0 4px 10px rgba(27, 18, 12, 0.06);
        }

        div[data-testid="stExpander"] summary {
            color: var(--lab-ink);
            font-weight: 600;
        }

        [data-testid="stSidebar"] div[data-testid="stExpanderDetails"] {
            padding-top: 0.5rem;
            padding-bottom: 1rem;
            min-height: 4.5rem;
        }

        div[data-testid="stAlert"] {
            border-radius: 12px;
            border: 1px solid rgba(117, 84, 47, 0.22);
            box-shadow: 0 3px 8px rgba(27, 18, 12, 0.05);
        }

        div[data-testid="stInfo"] {
            background: rgba(255, 249, 241, 0.78);
        }

        div[data-testid="stSpinner"] {
            color: var(--lab-muted);
        }

        div[data-testid="stCaptionContainer"],
        div[data-testid="stCaptionContainer"] p {
            color: var(--lab-muted);
        }

        [data-testid="stWidgetLabel"] p,
        [data-testid="stMarkdownContainer"] small {
            color: var(--lab-muted);
        }

        div[data-baseweb="select"] > div,
        div[data-baseweb="input"] > div,
        div[data-baseweb="textarea"] textarea,
        div[data-baseweb="base-input"] input,
        input,
        textarea {
            background: rgba(255, 251, 246, 0.88) !important;
            border-color: rgba(95, 71, 52, 0.22) !important;
            color: var(--lab-ink) !important;
        }

        div[data-baseweb="select"] > div:hover,
        div[data-baseweb="input"] > div:hover,
        textarea:hover {
            border-color: rgba(124, 83, 53, 0.36) !important;
        }

        button[kind="primary"],
        div[data-testid="stButton"] button[kind="primary"] {
            background: linear-gradient(135deg, #7c5335, #3f594d) !important;
            border: 1px solid rgba(63, 89, 77, 0.28) !important;
            color: #fff8ef !important;
            box-shadow: 0 5px 12px rgba(63, 89, 77, 0.14);
        }

        div[data-testid="stButton"] button,
        button[kind="secondary"] {
            border-radius: 10px;
            border: 1px solid rgba(85, 63, 46, 0.20);
            background: rgba(255, 249, 241, 0.82);
            color: var(--lab-ink);
            font-weight: 600;
        }

        div[data-testid="stButton"] button:hover,
        button[kind="secondary"]:hover {
            border-color: rgba(124, 83, 53, 0.38);
            background: rgba(255, 251, 246, 0.96);
            color: var(--lab-ink);
        }

        div[data-testid="stJson"] {
            border: 1px solid rgba(85, 63, 46, 0.16);
            background: rgba(255, 251, 246, 0.72);
        }

        code,
        pre {
            background: rgba(255, 251, 246, 0.70) !important;
            color: #463326 !important;
            border-radius: 8px;
        }

        a {
            color: var(--lab-accent-deep);
        }

        ::selection {
            background: rgba(124, 83, 53, 0.22);
        }

        @media (max-width: 768px) {
            .main .block-container {
                padding-left: 0.85rem;
                padding-right: 0.85rem;
            }

            .stage-shell {
                aspect-ratio: 5 / 6;
                min-height: 520px;
            }

            .stage-overlay {
                grid-template-columns: 1fr;
                grid-template-rows: auto auto auto auto;
                inset: 0.65rem;
            }

            .stage-card-scene,
            .stage-card-exits,
            .stage-card-npcs,
            .stage-card-items {
                grid-column: 1;
                grid-row: auto;
                width: auto;
                max-width: none;
            }

            div[data-testid="stChatMessage"] {
                padding: 0.5rem 0.7rem;
            }
        }

        @keyframes labMessageIn {
            from {
                opacity: 0;
                transform: translateY(6px);
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


@st.cache_data(show_spinner=False)
def build_image_data_uri(path_str: str) -> str:
    """把本地场景图转成可直接嵌入 HTML 的 data URI。"""

    path = Path(path_str)
    if not path.exists() or not path.is_file():
        return ""

    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def escape_html_text(value: Any) -> str:
    return html.escape(str(value or ""))


def format_html_text(value: Any) -> str:
    return escape_html_text(value).replace("\n", "<br>")


def description_public_text(entity: Any) -> str:
    description = getattr(entity, "description", None)
    public = getattr(description, "public", None)
    if isinstance(public, list):
        return "\n".join(str(item) for item in public if str(item).strip())
    if public:
        return str(public)
    return str(description or "")


def current_scene_public_entry(runtime: AppRuntime) -> Dict[str, str]:
    """返回当前场景 public 描述的前端展示文本。"""

    actor = runtime.engine.world_state.get_character(runtime.actor_id)
    current_map = runtime.engine.world_state.get_map(actor.location)
    public_text = description_public_text(current_map).strip()
    if not public_text:
        return {"map_id": current_map.id, "map_name": current_map.name, "public_text": "", "entry_text": ""}
    return {
        "map_id": current_map.id,
        "map_name": current_map.name,
        "public_text": public_text,
        "entry_text": f"进入场景：{current_map.name}\n{public_text}",
    }


def append_scene_public_to_output(display_text: str, scene_entry: Dict[str, str]) -> str:
    """玩家进入新场景后，把该场景 public 描述补进本回合输出。"""

    output = str(display_text or "").strip()
    public_text = scene_entry.get("public_text", "").strip()
    entry_text = scene_entry.get("entry_text", "").strip()
    if not entry_text:
        return output
    if public_text and public_text in output:
        return output
    if entry_text in output:
        return output
    return f"{output}\n\n{entry_text}".strip()


def append_current_scene_public_message(runtime: AppRuntime) -> None:
    """初始化或清空剧情后，自动展示当前场景 public。"""

    scene_entry = current_scene_public_entry(runtime)
    entry_text = scene_entry.get("entry_text", "").strip()
    if not entry_text:
        return

    st.session_state.chat_history.append(
        {
            "role": "assistant",
            "content": entry_text,
            "meta": {
                "type": "scene_public",
                "map_id": scene_entry.get("map_id", ""),
                "map_name": scene_entry.get("map_name", ""),
                "turn_id": runtime.turn_id,
                "trace_id": runtime.trace_id,
            },
        }
    )


def scene_background_uri(world_name: str, map_id: str) -> str:
    file_name = SCENE_BACKGROUND_FILES.get(world_name, {}).get(map_id)
    if not file_name:
        file_name = "_fallback_default.svg"

    path = BACKGROUND_DIR / file_name
    if not path.exists():
        path = BACKGROUND_DIR / "_fallback_default.svg"
    return build_image_data_uri(str(path)) if path.exists() else ""


def pill_markup(values: List[str], empty_text: str) -> str:
    clean_values = [str(value).strip() for value in values if str(value).strip()]
    if not clean_values:
        return f"<span class='pill'>{escape_html_text(empty_text)}</span>"
    return "".join(f"<span class='pill'>{escape_html_text(value)}</span>" for value in clean_values)


def plain_list_markup(values: List[str], empty_text: str) -> str:
    clean_values = [str(value).strip() for value in values if str(value).strip()]
    if not clean_values:
        return f"<div class='plain-line'>{escape_html_text(empty_text)}</div>"
    return "".join(f"<div class='plain-line'>{escape_html_text(value)}</div>" for value in clean_values)


def localize_direction_label(value: Any) -> str:
    """把常见英文方位词转换成中文。"""

    raw = str(value or "").strip()
    key = raw.lower().replace("_", "").replace("-", "").replace(" ", "")
    mapping = {
        "east": "东边",
        "west": "西边",
        "south": "南边",
        "north": "北边",
        "northeast": "东北边",
        "northwest": "西北边",
        "southeast": "东南边",
        "southwest": "西南边",
        "up": "上方",
        "down": "下方",
        "inside": "里面",
        "outside": "外面",
    }
    return mapping.get(key, raw)


def format_exit_label(direction: Any, description: Any) -> str:
    """把出口信息格式化为更适合展示的中文菜单项。"""

    direction_text = localize_direction_label(direction)
    description_text = str(description or "").strip()
    if direction_text in {"出口", "入口"}:
        return f"{direction_text}：{description_text}" if description_text else direction_text
    if description_text:
        return f"{direction_text}：{description_text}"
    return direction_text


def render_sidebar_info_expander(
    title: str,
    values: List[str],
    empty_text: str,
    *,
    use_pills: bool = True,
) -> None:
    """在左侧栏渲染可点击展开的信息区。"""

    with st.sidebar.expander(title, expanded=False):
        content_markup = pill_markup(values, empty_text) if use_pills else plain_list_markup(values, empty_text)
        wrapper_class = "pill-wrap" if use_pills else "plain-list"
        st.markdown(
            f"<div class='{wrapper_class}'>{content_markup}</div>",
            unsafe_allow_html=True,
        )


def presentation_text(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith("回合执行失败:"):
        return "剧情生成暂时失败，请稍后重试或检查模型配置。"
    return text.replace("|", "\n\n")


def render_stage_panel(runtime: AppRuntime) -> None:
    """渲染参考项目同款场景舞台，不参与后端状态计算。"""

    actor = runtime.engine.world_state.get_character(runtime.actor_id)
    current_map = runtime.engine.world_state.get_map(actor.location)
    scene_text = description_public_text(current_map)
    bg_uri = scene_background_uri(runtime.world_name, current_map.id)

    image_markup = (
        f'<img class="stage-bg" src="{html.escape(bg_uri)}" alt="scene" decoding="async" draggable="false">'
        if bg_uri
        else '<div class="stage-fallback">背景图未加载</div>'
    )

    stage_markup = "".join(
        [
            '<div class="stage-shell">',
            image_markup,
            '<div class="stage-mask"></div>',
            '<div class="stage-overlay">',
            '<section class="glass stage-card-scene">',
            '<div class="eyeline">当前场景</div>',
            f"<h3>{escape_html_text(current_map.name)}</h3>",
            f'<div class="scene-meta">回合 {runtime.turn_id} · 玩家 {escape_html_text(actor.name)}</div>',
            f'<p class="scene-text">{format_html_text(scene_text)}</p>',
            "</section>",
            "</div>",
            "</div>",
        ]
    )
    st.html(stage_markup)


def render_sidebar_world_panels(runtime: AppRuntime) -> None:
    """把地图相关信息放到左侧栏。"""

    actor = runtime.engine.world_state.get_character(runtime.actor_id)
    current_map = runtime.engine.world_state.get_map(actor.location)

    characters = [
        getattr(item, "name", str(item))
        for item in runtime.engine.world_state.get_characters_at(actor.location)
        if getattr(item, "id", "") != runtime.actor_id
    ]
    items = [getattr(item, "name", str(item)) for item in runtime.engine.world_state.get_items_at(actor.location)]

    exit_labels: List[str] = []
    for connection in getattr(current_map, "connections", []) or []:
        direction = str(getattr(connection, "direction", "") or getattr(connection, "name", "") or "出口")
        description = str(getattr(connection, "description", "") or getattr(connection, "target_map_id", "") or "")
        exit_labels.append(format_exit_label(direction, description))

    st.sidebar.markdown("---")
    st.sidebar.markdown(
        f"<div class='eyeline'>当前场景</div><p class='scene-meta'>{escape_html_text(current_map.name)}</p>",
        unsafe_allow_html=True,
    )
    render_sidebar_info_expander("在场角色", characters, "当前没有其他在场角色")
    render_sidebar_info_expander("可通往地点", exit_labels, "当前没有可通往地点", use_pills=False)
    render_sidebar_info_expander("可见物品", items, "当前没有可见物品")


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

    streamed_text = join_story_segments([fragment["content"] for fragment in fragments])
    text = streamed_text or aggregated_raw or extract_player_text(result)
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


def join_story_segments(values: List[str]) -> str:
    """把多个叙事片段按展示友好的段落形式拼接。"""

    clean_values = [str(value).strip() for value in values if str(value).strip()]
    return "\n\n".join(clean_values)


def merge_narrative_stream_events(
    events: List[Dict[str, Any]],
    *,
    fragments_by_id: Dict[str, Dict[str, str]],
    fragment_order: List[str],
) -> bool:
    """把新增 narrative 流事件并入前端缓冲区。"""

    changed = False
    for event in events:
        if not isinstance(event, dict):
            continue
        data = event.get("data", {})
        if not isinstance(data, dict):
            continue

        fragment_id = str(data.get("fragment_id", "")).strip()
        if not fragment_id:
            continue

        if fragment_id not in fragments_by_id:
            fragments_by_id[fragment_id] = {
                "fragment_id": fragment_id,
                "source_kind": str(data.get("source_kind", "")),
                "source_id": str(data.get("source_id", "")),
                "content": "",
            }
            fragment_order.append(fragment_id)

        event_name = str(event.get("event", ""))
        if event_name == "narrative.fragment.delta":
            delta = str(data.get("delta", ""))
            if delta:
                fragments_by_id[fragment_id]["content"] += delta
                changed = True
        elif event_name == "narrative.fragment.completed":
            completed_text = str(data.get("content", "")).strip()
            if completed_text and fragments_by_id[fragment_id].get("content", "") != completed_text:
                fragments_by_id[fragment_id]["content"] = completed_text
                changed = True

    return changed


def build_stream_preview_text(
    *,
    fragments_by_id: Dict[str, Dict[str, str]],
    fragment_order: List[str],
) -> str:
    """把当前已接收的流式片段拼成前端预览文本。"""

    return join_story_segments(
        [fragments_by_id[fragment_id].get("content", "") for fragment_id in fragment_order]
    )


def render_live_story_panel(
    placeholder,
    text: str,
    *,
    is_streaming: bool,
    label: Optional[str] = None,
) -> None:
    """在输入框下方渲染当前回合的实时剧情输出。"""

    normalized_text = presentation_text(text)
    has_text = bool(normalized_text.strip())
    label_text = label or ("剧情生成中" if is_streaming else "本回合剧情")
    body = normalized_text if has_text else "正在生成剧情..."
    pending_class = " live-story-pending" if not has_text else ""
    placeholder.markdown(
        (
            f"<div class='glass live-story-shell{pending_class}'>"
            f"<div class='live-story-label'>{escape_html_text(label_text)}</div>"
            f"<div class='live-story-body'>{format_html_text(body)}</div>"
            "</div>"
        ),
        unsafe_allow_html=True,
    )


def stream_text_to_live_panel(
    placeholder,
    text: str,
    *,
    chunk_size: int,
    delay_sec: float,
) -> str:
    """在没有后端流事件时，用前端分片方式平滑展示文本。"""

    merged = ""
    for chunk in chunk_text_for_stream(text, chunk_size=chunk_size):
        if not chunk:
            continue
        merged += chunk
        render_live_story_panel(placeholder, merged, is_streaming=True)
        if delay_sec > 0:
            time.sleep(delay_sec)
    if merged:
        render_live_story_panel(placeholder, merged, is_streaming=False)
    return merged


def latest_assistant_output() -> str:
    """返回最近一条 assistant 剧情文本，用于输入框下方常驻展示。"""

    for message in reversed(st.session_state.chat_history):
        if message.get("role") != "assistant":
            continue
        content = str(message.get("content", "")).strip()
        if content:
            return content
    return ""


def render_current_turn_output_panel() -> None:
    """在输入框下方常驻展示当前回合输出。"""

    latest_output = latest_assistant_output()
    if not latest_output:
        return

    output_placeholder = st.empty()
    render_live_story_panel(
        output_placeholder,
        latest_output,
        is_streaming=False,
        label="当前回合输出",
    )


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

    st.sidebar.title("AI阅读实验室")

    worlds = list_world_dirs(str(WORLD_DIR))
    if not worlds:
        st.sidebar.error("world 目录下未找到分类世界目录。")
        return {"can_run": False}

    default_world = DEFAULT_WORLD_DIR.name if DEFAULT_WORLD_DIR.name in worlds else worlds[0]
    runtime = st.session_state.get("runtime")
    current_world = getattr(runtime, "world_name", default_world)
    if current_world not in worlds:
        current_world = default_world

    selected_world = st.sidebar.selectbox(
        "世界",
        options=worlds,
        index=worlds.index(current_world),
    )

    st.sidebar.markdown("---")
    col_left, col_right = st.sidebar.columns(2)
    rebuild_clicked = col_left.button("重新开始", use_container_width=True)
    clear_chat_clicked = col_right.button("清空剧情", use_container_width=True)

    return {
        "can_run": True,
        "world_name": selected_world,
        "mode": "unified-main",
        "use_real_llm": True,
        "config_path": DEFAULT_CONFIG_PATH,
        "api_key": "",
        "api_base": "",
        "model": "",
        "enable_reasoning": False,
        "temperature": 0.7,
        "max_tokens": 3000,
        "timeout": 30,
        "debug_mode": False,
        "rebuild_clicked": rebuild_clicked,
        "clear_chat_clicked": clear_chat_clicked,
    }


def render_chat_history() -> None:
    """渲染累计聊天记录。"""

    for message in st.session_state.chat_history:
        role = message.get("role", "assistant")
        content = presentation_text(message.get("content", ""))
        with st.chat_message(role):
            st.markdown(content)


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
    actor_before_turn = runtime.engine.world_state.get_character(runtime.actor_id)
    location_before_turn = str(getattr(actor_before_turn, "location", ""))

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

    live_story_placeholder = st.empty()
    render_live_story_panel(live_story_placeholder, "", is_streaming=True)
    stream_fragments_by_id: Dict[str, Dict[str, str]] = {}
    stream_fragment_order: List[str] = []
    streamed_text = ""

    while worker.is_alive():
        new_events, narrative_cursor = collect_narrative_events(runtime, narrative_cursor)
        if merge_narrative_stream_events(
            new_events,
            fragments_by_id=stream_fragments_by_id,
            fragment_order=stream_fragment_order,
        ):
            streamed_text = build_stream_preview_text(
                fragments_by_id=stream_fragments_by_id,
                fragment_order=stream_fragment_order,
            )
            render_live_story_panel(live_story_placeholder, streamed_text, is_streaming=True)
        time.sleep(runtime.engine_poll_interval_sec)

    worker.join()
    new_events, narrative_cursor = collect_narrative_events(runtime, narrative_cursor)
    if merge_narrative_stream_events(
        new_events,
        fragments_by_id=stream_fragments_by_id,
        fragment_order=stream_fragment_order,
    ):
        streamed_text = build_stream_preview_text(
            fragments_by_id=stream_fragments_by_id,
            fragment_order=stream_fragment_order,
        )
    if streamed_text:
        render_live_story_panel(live_story_placeholder, streamed_text, is_streaming=False)

    if result_holder.get("error") is not None:
        exc = result_holder["error"]
        error_text = "剧情生成暂时失败，请稍后重试或检查模型配置。"
        st.session_state.chat_history.append(
            {
                "role": "assistant",
                "content": error_text,
                "meta": {
                    "trace_id": runtime.trace_id,
                    "turn_id": runtime.turn_id,
                    "route": "exception",
                    "error": str(exc),
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
        st.rerun()
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
    display_text = str(visible.get("text", "")).strip()
    fragments = visible.get("fragments", []) if isinstance(visible.get("fragments"), list) else []
    aggregated_raw = str(visible.get("aggregated_raw", "")).strip()

    if streamed_text.strip():
        display_text = streamed_text.strip()
        render_live_story_panel(live_story_placeholder, display_text, is_streaming=False)
    elif display_text:
        display_text = stream_text_to_live_panel(
            live_story_placeholder,
            display_text,
            chunk_size=runtime.stream_chunk_size,
            delay_sec=runtime.stream_chunk_delay_sec,
        ).strip()
    else:
        live_story_placeholder.empty()

    actor_after_turn = runtime.engine.world_state.get_character(runtime.actor_id)
    location_after_turn = str(getattr(actor_after_turn, "location", ""))
    scene_entry_for_turn: Dict[str, str] = {}
    if location_after_turn and location_after_turn != location_before_turn:
        scene_entry_for_turn = current_scene_public_entry(runtime)
        display_text = append_scene_public_to_output(display_text, scene_entry_for_turn)
        if display_text:
            render_live_story_panel(live_story_placeholder, display_text, is_streaming=False)

    st.session_state.chat_history.append(
        {
            "role": "assistant",
            "content": display_text,
            "meta": {
                "trace_id": turn_record.get("trace_id"),
                "turn_id": turn_record.get("turn_id"),
                "route": turn_record.get("route"),
                "aggregated_raw": aggregated_raw,
                "fragments": fragments,
                "scene_public": scene_entry_for_turn,
            },
        }
    )

    runtime.turn_id += runtime.turn_id_step
    runtime.trace_id += runtime.trace_id_step
    runtime.causality_chain = E7CausalityChain()
    st.session_state.debug_turn_idx = max(len(runtime.turn_records) - 1, 0)
    st.rerun()


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


def render_action_input_panel(runtime: AppRuntime) -> Optional[str]:
    """在场景图下方渲染固定位置的输入面板。"""

    disabled = bool(runtime.game_over)
    with st.form("action-input-form", clear_on_submit=True):
        input_col, submit_col = st.columns([0.86, 0.14], gap="small")
        with input_col:
            user_text = st.text_input(
                "输入你的行动",
                value="",
                placeholder="可直接输入自然语言行动，例如：前往东边、我沿山道前往草庐",
                label_visibility="collapsed",
                disabled=disabled,
            )
        with submit_col:
            submitted = st.form_submit_button(
                "发送",
                use_container_width=True,
                type="primary",
                disabled=disabled,
            )
    if submitted and user_text.strip():
        return user_text.strip()
    return None


def main() -> None:
    """应用入口。"""

    st.set_page_config(page_title="AI阅读实验室", layout="wide")
    inject_chat_layout_style()

    ensure_session_state()
    sidebar_state = render_sidebar()
    if not sidebar_state.get("can_run", False):
        st.stop()

    if sidebar_state.get("clear_chat_clicked"):
        st.session_state.chat_history = []

    selected_world = str(sidebar_state["world_name"])
    current_runtime = st.session_state.runtime
    should_build = (
        sidebar_state.get("rebuild_clicked")
        or current_runtime is None
        or getattr(current_runtime, "world_name", selected_world) != selected_world
    )
    if should_build:
        with st.spinner("正在初始化引擎..."):
            st.session_state.runtime = build_runtime(
                world_name=selected_world,
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
    if should_build or sidebar_state.get("clear_chat_clicked") or not st.session_state.chat_history:
        append_current_scene_public_message(runtime)

    render_sidebar_world_panels(runtime)
    render_stage_panel(runtime)
    st.markdown("<div class='action-heading'>行动输入</div>", unsafe_allow_html=True)
    user_text = render_action_input_panel(runtime)
    if user_text:
        handle_user_turn(runtime, user_text.strip())
    render_current_turn_output_panel()

    if st.session_state.chat_history:
        with st.expander("剧情记录", expanded=False):
            render_chat_history()

    if runtime.game_over:
        st.warning("游戏已经结束。你可以在侧栏重新开始。")

    if sidebar_state.get("debug_mode"):
        with st.expander("Debug", expanded=False):
            render_debug_panel(runtime)


if __name__ == "__main__":
    main()
