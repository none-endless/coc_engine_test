from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from src.data.model.input.agent_narrative_input import (
    NarrativeEntry,
    NarrativeInfo,
    NarrativeLogItem,
)


class SqliteNarrativeRepository:
    """叙事真值 SQLite 仓储，负责独立持久化 recent 与 narrative_log。"""

    def __init__(self, sqlite_path: str) -> None:
        """初始化仓储并确保目录与表结构存在。"""
        self.sqlite_path = sqlite_path
        self._ensure_parent_dir()
        self._ensure_schema()

    def _ensure_parent_dir(self) -> None:
        """确保 SQLite 文件所在目录存在。"""
        if not self.sqlite_path:
            return
        Path(self.sqlite_path).parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        """创建到 narrative sqlite 的连接。"""
        connection = sqlite3.connect(self.sqlite_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_schema(self) -> None:
        """初始化 narrative recent 与 narrative log 表结构。"""
        with closing(self._connect()) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS narrative_recent (
                  turn_id INTEGER PRIMARY KEY,
                  content TEXT NOT NULL,
                  source TEXT NOT NULL,
                  committed_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS narrative_log (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  turn_id INTEGER NOT NULL,
                  source TEXT NOT NULL,
                  content TEXT NOT NULL,
                  logged_at TEXT NOT NULL
                )
                """
            )
            connection.commit()

    def load(self) -> NarrativeInfo:
        """从 SQLite 载入完整叙事真值，恢复为 NarrativeInfo 模型。"""
        with closing(self._connect()) as connection:
            recent_rows = connection.execute(
                """
                SELECT turn_id, content
                FROM narrative_recent
                ORDER BY turn_id ASC
                """
            ).fetchall()
            log_rows = connection.execute(
                """
                SELECT turn_id, content, source, logged_at
                FROM narrative_log
                ORDER BY id ASC
                """
            ).fetchall()

        return NarrativeInfo(
            recent=[
                NarrativeEntry(
                    turn=int(row["turn_id"]),
                    content=str(row["content"]),
                )
                for row in recent_rows
            ],
            narrative_log=[
                NarrativeLogItem(
                    turn=int(row["turn_id"]),
                    content=str(row["content"]),
                    source=str(row["source"]),
                    timestamp=self._coerce_timestamp(str(row["logged_at"])),
                )
                for row in log_rows
            ],
        )

    def save(self, narrative_info: NarrativeInfo) -> None:
        """把当前 NarrativeInfo 全量刷入 SQLite，作为 narrative truth 单一持久化事实源。"""
        recent_by_turn = {}
        for recent_item in narrative_info.recent:
            recent_by_turn[int(recent_item.turn)] = recent_item

        with closing(self._connect()) as connection:
            connection.execute("DELETE FROM narrative_recent")
            connection.execute("DELETE FROM narrative_log")
            for turn_id in sorted(recent_by_turn.keys()):
                recent_item = recent_by_turn[turn_id]
                connection.execute(
                    """
                    INSERT OR REPLACE INTO narrative_recent(turn_id, content, source, committed_at)
                    VALUES (?, ?, ?, datetime('now'))
                    """,
                    (recent_item.turn, recent_item.content, "merger_agent"),
                )
            for log_item in narrative_info.narrative_log:
                connection.execute(
                    """
                    INSERT INTO narrative_log(turn_id, source, content, logged_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (log_item.turn, log_item.source, log_item.content, str(log_item.timestamp)),
                )
            connection.commit()

    @staticmethod
    def _coerce_timestamp(raw_value: str) -> int:
        """把 SQLite 中的 logged_at 字段稳定转换为 int 时间戳。"""
        try:
            return int(raw_value)
        except ValueError:
            return 0
