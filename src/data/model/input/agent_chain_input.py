"""
Agent 链路输入模型。

本文件只定义链路信息（E3 子项 e1/e2/e3/e4/e7）以及各 Agent 的输入容器，
不与世界视图模型混用。
"""

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

from ..infra import TurnEnvelope



class InputType(str, Enum):
    """输入类型。"""

    META_COMMAND = "meta_command"
    NATURAL_LANGUAGE = "natural_language"
    NPC_AUTONOMOUS = "npc_autonomous"


class InputSource(str, Enum):
    """输入来源。"""

    PLAYER = "player"
    NPC = "npc"
    SYSTEM = "system"


class E1InputInfo(BaseModel):
    """e1：输入信息。"""

    turn_id: int = Field(description="回合号")
    trace_id: int = Field(default=0, description="链路追踪 ID")
    world_version: Optional[int] = Field(default=None, description="世界版本号")
    event_id: Optional[str] = Field(default=None, description="事件 ID")
    source_id: str = Field(default="", description="来源实体 ID")
    raw_text: str = Field(default="", description="原始输入文本")
    command: Optional[str] = Field(default=None, description="元命令名称")
    command_args: Dict[str, Any] = Field(default_factory=dict, description="元命令参数")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="额外调试/追踪信息")


class E2IntentInfo(BaseModel):
    """e2：意图诠释。"""

    intent: str = Field(default="", description="主意图")
    routing_hint: Optional[str] = Field(default=None, description="链路路由建议")
    attributes: List[str] = Field(default_factory=list, description="需要进行鉴定的属性名称")
    against_char_id: List[str] = Field(default_factory=list, description="对抗鉴定对象角色 id 列表")
    difficulty: Optional[str] = Field(default=None, description="鉴定难度")
    dm_reply: Optional[str] = Field(default=None, description="若应由 DM 直接回复，则填回文本")

    @field_validator("attributes", "against_char_id", mode="before")
    @classmethod
    def _normalize_optional_list(cls, value):
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item) for item in value if item is not None and str(item).strip()]
        return value


class E3RuleResult(BaseModel):
    """e3：规则结算事实。"""

    intent: str = Field(default="", description="直接来自 dm_agent 的意图")
    check_type: Optional[str] = Field(default=None, description="本次规则结算的鉴定类型")
    success: str = Field(default="", description="触发方最终结果")
    difficulty: Optional[str] = Field(default=None, description="本次鉴定难度")
    actor_id: Optional[str] = Field(default=None, description="本次主动方角色 ID")
    opposed_id: Optional[str] = Field(default=None, description="本次对抗中的对手角色 ID")
    winner_id: Optional[str] = Field(default=None, description="本次规则结算的获胜者 ID")
    affected_ids: List[str] = Field(default_factory=list, description="本次结算直接作用到的角色 ID 列表")


class E4EvolutionStepResult(BaseModel):
    """e4：步骤结算。"""

    summary: str = Field(default="", description="本步摘要")


class E4SchedulerStepResult(BaseModel):
    """e4：scheduler 给 performer 的额外上下文。"""

    scheduled_npc_ids: List[str] = Field(default_factory=list, description="本回合实际进入调度的 NPC ID 顺序列表")
    extra_npc_context: Dict[str, Optional[str]] = Field(default_factory=dict, description="scheduler 给 performer 的额外信息")


class E7CausalityChain(BaseModel):
    """e7：回合因果链。"""

    narrative_list: List[Dict[str, str]] = Field(default_factory=list, description="回合因果事件列表（可含 evolution/narrative 分支）")


class FallbackError(BaseModel):
    """状态变更失败后的重试/降级信息。"""

    code: str = Field(default="", description="错误码")
    message: str = Field(default="", description="错误信息")
    retry_count: int = Field(default=0, description="已重试次数")
    retriable: bool = Field(default=True, description="是否可重试")
    rollback_applied: bool = Field(default=False, description="是否已回滚")
    degraded_output: Optional[str] = Field(default=None, description="降级输出")
    details: Dict[str, Any] = Field(default_factory=dict, description="错误详情")


class DmAgentChainInput(BaseModel):
    """dmagent：e1"""

    e1: E1InputInfo = Field(description="输入信息")


class EvolutionAgentChainInput(BaseModel):
    """evolution：e1 + e3 + e7"""

    e1: E1InputInfo = Field(description="输入信息")
    e3: E3RuleResult = Field(description="规则结算事实")
    e7: E7CausalityChain = Field(description="回合因果链")


class StateChangeAgentChainInput(BaseModel):
    """state：e4 + fallback_error"""

    e4: E4EvolutionStepResult = Field(description="步骤结算（来自 evolution）")
    fallback_error: Optional[FallbackError] = Field(default=None, description="失败重试/降级信息")


class NpcSchedulerAgentChainInput(BaseModel):
    """npcscheduler：e4"""

    e4: E4EvolutionStepResult = Field(description="步骤结算（来自 evolution）")


class NpcPerformerAgentChainInput(BaseModel):
    """npcperformer：e4 + e1"""

    e4: E4SchedulerStepResult = Field(description="步骤结算（含 scheduler 提供的额外上下文）")
    e1: E1InputInfo = Field(description="输入信息")


class NarrativeAgentChainInput(BaseModel):
    """narrative：e4"""

    e4: E4EvolutionStepResult = Field(description="步骤结算（来自 evolution）")


class MergerAgentChainInput(BaseModel):
    """merger_agent：e7。"""

    e7: E7CausalityChain = Field(description="回合因果链")
