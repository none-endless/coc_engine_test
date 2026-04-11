"""
叙事信息数据结构
用于存储和管理叙事相关的信息
"""

from datetime import datetime
from typing import List, Dict, Optional, Any, TYPE_CHECKING
from pydantic import BaseModel, Field

# 避免循环导入，仅在类型检查时导入
if TYPE_CHECKING:
    pass


# ============================================================
# 叙事日志条目
# ============================================================

class NarrativeLogItem(BaseModel):
    """
    叙事日志条目
    用于 debug 和回溯，记录已发生的完整叙事事件
    """
    turn: int = Field(description="回合数")
    timestamp: int = Field(description="时间戳")
    content: str = Field(description="叙事内容")
    source: str = Field(description="来源，如 'narrative_agent', 'merger_agent'")


class NarrativeEntry(BaseModel):
    """
    叙事条目
    单条叙事记录
    """
    turn: int = Field(description="回合数")
    content: str = Field(description="叙事内容")


# ============================================================
# 叙事信息容器
# ============================================================

class NarrativeInfo(BaseModel):
    """
    叙事信息

    说明：
    - recent: 最近5回合的叙事记录，超出部分被压入到 NarrativeLog 当中
    - NarrativeLog: 完整叙事日志，用于 debug 和回溯
    """
    recent: List["NarrativeEntry"] = Field(
        default_factory=list,
        description="最近5回合的叙事记录，超出部分被压入 NarrativeLog"
    )
    narrative_log: List["NarrativeLogItem"] = Field(
        default_factory=list,
        description="完整叙事日志，仅用于 debug 与回溯，不进入 LLM 上下文"
    )

    def _append_with_rollover(self, entry: "NarrativeEntry", source: str, max_recent: int = 5) -> None:
        """追加 recent，超出阈值后把最早条目压入 narrative_log。"""
        self.recent.append(entry)
        if len(self.recent) > max_recent:
            oldest = self.recent.pop(0)
            self.narrative_log.append(NarrativeLogItem(
                turn=oldest.turn,
                timestamp=int(datetime.now().timestamp()),
                content=oldest.content,
                source=source,
            ))

    def add_narrative(self, turn: int, content: str, source: str = "narrative_agent") -> None:
        """
        添加叙事记录

        Args:
            turn: 回合数
            content: 叙事内容
            source: 来源
        """
        self._append_with_rollover(
            entry=NarrativeEntry(turn=turn, content=content),
            source=source,
        )

    def get_recent_narratives(self, count: int = 5) -> List["NarrativeEntry"]:
        """
        获取最近 n 条叙事记录

        Args:
            count: 获取数量，默认5条

        Returns:
            最近 n 条叙事记录
        """
        return self.recent[-count:]


# ============================================================
# 叙事信息历史（用于 merger）
# ============================================================

class NarrativeHistory(BaseModel):
    """
    叙事历史
    用于 merger_agent 记录完整的叙事因果链
    """
    turn: int = Field(description="回合数")
    narratives: List["NarrativeEntry"] = Field(
        default_factory=list,
        description="本回合的所有叙事条目"
    )
    causality_chain: str = Field(
        default="",
        description="因果链描述，记录本回合叙事的事件时序关系"
    )
