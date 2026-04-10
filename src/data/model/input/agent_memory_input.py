"""
Agent Memory 数据结构
用于存储 Agent 的记忆信息
"""

from datetime import datetime
from typing import List, Dict, Optional, Any, TYPE_CHECKING
from pydantic import BaseModel, Field

# 避免循环导入，仅在类型检查时导入
if TYPE_CHECKING:
    pass


# ============================================================
# DM Memory 数据结构
# ============================================================

class DialogueLogItem(BaseModel):
    """
    对话日志条目
    用于记录完整的对话历史，仅用于 debug
    """
    turn: int = Field(description="回合数")
    timestamp: int = Field(description="时间戳")
    speaker: str = Field(description="说话者 ID")
    content: str = Field(description="对话内容")


class DialogueEntry(BaseModel):
    """
    对话条目
    单条对话记录
    """
    turn: int = Field(description="回合数")
    speaker: str = Field(description="说话者 ID")
    content: str = Field(description="对话内容")


class DmMemory(BaseModel):
    """
    DM 的记忆

    说明：
    - dialogues: 最近5回合的对话信息，超出部分被压入 dialogue_log
    - dialogue_log: 完整对话日志，用于 debug 和回溯
    """
    dialogues: List["DialogueEntry"] = Field(
        default_factory=list,
        description="最近5回合的对话信息，超出部分被压入 dialogue_log"
    )
    dialogue_log: List["DialogueLogItem"] = Field(
        default_factory=list,
        description="完整对话日志，仅用于 debug 与回溯"
    )

    def add_dialogue(self, turn: int, speaker: str, content: str) -> None:
        """
        添加对话记录

        Args:
            turn: 回合数
            speaker: 说话者 ID
            content: 对话内容
        """
        # 添加到 dialogues
        self.dialogues.append(DialogueEntry(
            turn=turn,
            speaker=speaker,
            content=content
        ))

        # 如果 dialogues 超过5条，将最早的条目移到 log 中
        if len(self.dialogues) > 5:
            oldest = self.dialogues.pop(0)
            self.dialogue_log.append(DialogueLogItem(
                turn=oldest.turn,
                timestamp=int(datetime.now().timestamp()),
                speaker=oldest.speaker,
                content=oldest.content
            ))

    def get_recent_dialogues(self, count: int = 5) -> List["DialogueEntry"]:
        """
        获取最近 n 条对话记录

        Args:
            count: 获取数量，默认5条

        Returns:
            最近 n 条对话记录
        """
        return self.dialogues[-count:] if len(self.dialogues) <= count else self.dialogues[-count:]
