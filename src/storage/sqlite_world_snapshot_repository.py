from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Dict, Optional

from src.data.model.world_state import WorldSnapshot


class SqliteWorldSnapshotRepository:
    """世界真值快照 SQLite 仓储，用于与叙事真值物理隔离存储。"""

    def __init__(self, sqlite_path: str) -> None:
        """初始化仓储并确保 world snapshot 表结构存在。"""
        self.sqlite_path = sqlite_path
        self._ensure_parent_dir()
        self._ensure_schema()

    def _ensure_parent_dir(self) -> None:
        """确保 SQLite 文件所在目录存在。"""
        if not self.sqlite_path:
            return
        Path(self.sqlite_path).parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        """创建到 world snapshot sqlite 的连接。"""
        connection = sqlite3.connect(self.sqlite_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_schema(self) -> None:
        """初始化 world_snapshots 表结构。"""
        with closing(self._connect()) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS world_snapshots (
                  version INTEGER PRIMARY KEY,
                  snapshot_at TEXT NOT NULL,
                  payload_json TEXT NOT NULL
                )
                """
            )
            connection.commit()

    def save_snapshot(self, snapshot: WorldSnapshot | Dict[str, Any]) -> None:
        """把当前世界快照写入 SQLite，供回放和双真值池验证使用。"""
        payload = snapshot.to_payload() if isinstance(snapshot, WorldSnapshot) else dict(snapshot)
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO world_snapshots(version, snapshot_at, payload_json)
                VALUES (?, ?, ?)
                """,
                (
                    int(payload.get("version", 0)),
                    str(payload.get("snapshot_at", "")),
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            connection.commit()

    def load_latest_snapshot(self) -> Optional[Dict[str, Any]]:
        """读取最新世界快照；若仓储为空则返回 None。"""
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM world_snapshots
                ORDER BY version DESC
                LIMIT 1
                """
            ).fetchone()
        if row is None:
            return None
        return json.loads(str(row["payload_json"]))
