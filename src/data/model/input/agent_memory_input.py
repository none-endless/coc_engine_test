"""
Agent Memory 数据结构
用于存储 Agent 的记忆信息
"""

from datetime import datetime
from typing import List
from pydantic import BaseModel, ConfigDict, Field


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
    - dialogues: 最近若干回合的对话信息，超出部分被压入 dialogue_log
    - dialogue_log: 完整对话日志，用于 debug 和回溯
    - memory_turns: recent 对话保留回合数，由配置驱动
    """
    model_config = ConfigDict(
        populate_by_name=True,
        serialize_by_alias=True,
    )

    dialogues: List["DialogueEntry"] = Field(
        default_factory=list,
        description="最近若干回合的对话信息，超出部分被压入 dialogue_log"
    )
    dialogue_log: List["DialogueLogItem"] = Field(
        default_factory=list,
        description="完整对话日志，仅用于 debug 与回溯"
    )
    memory_turns: int = Field(
        default=5,
        description="DM 对话记忆保留回合数"
    )

    def _append_with_rollover(self, entry: "DialogueEntry") -> None:
        """追加 recent，超出阈值后把最早条目压入 log。"""
        self.dialogues.append(entry)
        if len(self.dialogues) > self.memory_turns:
            oldest = self.dialogues.pop(0)
            self.dialogue_log.append(DialogueLogItem(
                turn=oldest.turn,
                timestamp=int(datetime.now().timestamp()),
                speaker=oldest.speaker,
                content=oldest.content,
            ))

    def add_dialogue(self, turn: int, speaker: str, content: str) -> None:
        """
        添加对话记录

        Args:
            turn: 回合数
            speaker: 说话者 ID
            content: 对话内容
        """
        self._append_with_rollover(DialogueEntry(
            turn=turn,
            speaker=speaker,
            content=content
        ))

    def get_recent_dialogues(self, count: int = 5) -> List["DialogueEntry"]:
        """
        获取最近 n 条对话记录

        Args:
            count: 获取数量，默认5条

        Returns:
            最近 n 条对话记录
        """
        return self.dialogues[-count:]
