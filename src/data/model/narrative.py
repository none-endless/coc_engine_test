from __future__ import annotations

from enum import Enum
from typing import Any, Dict

from pydantic import BaseModel, Field


class NarrativeDraftStatus(str, Enum):
    """叙事草稿状态。"""

    DRAFT = "draft"
    COMMITTED = "committed"
    DISCARDED = "discarded"


class NarrativeDraft(BaseModel):
    """叙事草稿，只有状态提交成功后才能转为 committed。"""

    draft_id: str = Field(default="", description="草稿 ID")
    trace_id: int = Field(default=0, description="链路追踪 ID")
    turn_id: int = Field(default=0, description="回合号")
    content: str = Field(default="", description="草稿内容")
    visible_to_player: bool = Field(default=True, description="是否对玩家可见")
    status: NarrativeDraftStatus = Field(default=NarrativeDraftStatus.DRAFT, description="草稿状态")


class NarrativeStreamEvent(BaseModel):
    """叙事流式输出事件。"""

    event: str = Field(default="", description="事件类型")
    data: Dict[str, Any] = Field(default_factory=dict, description="事件负载")
