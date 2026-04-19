"""
叙事信息数据结构。
用于存储和管理叙事相关的信息。
"""

from datetime import datetime
from typing import List

from pydantic import BaseModel, Field


class NarrativeLogItem(BaseModel):
    """叙事日志条目，用于 debug 和回溯。"""

    turn: int = Field(description="回合数")
    timestamp: int = Field(description="时间戳")
    content: str = Field(description="叙事内容")
    source: str = Field(description="来源，如 narrative_agent、merger_agent")


class NarrativeEntry(BaseModel):
    """单条叙事记录。"""

    turn: int = Field(description="回合数")
    content: str = Field(description="叙事内容")


class NarrativeInfo(BaseModel):
    """
    叙事信息容器。

    说明：
    - recent：最近若干回合的正式叙事真值。
    - narrative_log：完整叙事日志，仅用于 debug 与回溯。
    """

    recent: List[NarrativeEntry] = Field(default_factory=list, description="最近回合的叙事记录")
    narrative_log: List[NarrativeLogItem] = Field(default_factory=list, description="完整叙事日志")

    def _append_with_rollover(self, entry: NarrativeEntry, source: str, max_recent: int = 5) -> None:
        """追加 recent，超出阈值后把最早条目压入 narrative_log。"""
        self.recent.append(entry)
        if len(self.recent) > max_recent:
            oldest = self.recent.pop(0)
            self.narrative_log.append(
                NarrativeLogItem(
                    turn=oldest.turn,
                    timestamp=int(datetime.now().timestamp()),
                    content=oldest.content,
                    source=source,
                )
            )

    def add_narrative(self, turn: int, content: str, source: str = "merger_agent", max_recent: int = 5) -> None:
        """追加正式叙事真值。"""
        for index in range(len(self.recent) - 1, -1, -1):
            if self.recent[index].turn == turn:
                # 同回合重复写入时以最新内容覆盖，避免持久化唯一键冲突。
                self.recent[index].content = content
                return
        self._append_with_rollover(
            entry=NarrativeEntry(turn=turn, content=content),
            source=source,
            max_recent=max_recent,
        )

    def append_log(self, turn: int, content: str, source: str) -> None:
        """仅向 narrative_log 追加调试日志，不写入 recent。"""
        self.narrative_log.append(
            NarrativeLogItem(
                turn=turn,
                timestamp=int(datetime.now().timestamp()),
                content=content,
                source=source,
            )
        )

    def get_recent_narratives(self, count: int = 5) -> List[NarrativeEntry]:
        """获取最近 n 条叙事记录。"""
        return self.recent[-count:]
