"""
Agent 链路输入模型。

本文件只定义链路信息（E3 子项 e1/e2/e3/e4/e7）及各 Agent 的输入容器，
不与世界视图模型（agent_map_intput.py）混用。
"""

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ============================================================
# 链路信息基础模型（E3 子项）
# ============================================================


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


class TurnEnvelope(BaseModel):
	"""回合事务封装。"""

	raw_input: str = Field(default="", description="原始输入文本")
	turn: int = Field(default=0, description="回合号")
	trace_id: int = Field(default=0, description="链路追踪 ID")
	debug: Dict[str, Any] = Field(default_factory=dict, description="调试信息")
	world_version: Optional[int] = Field(default=None, description="世界版本号")
	event_id: Optional[str] = Field(default=None, description="事件 ID")


class E1InputInfo(BaseModel):
	"""
	e1: 输入信息
	输入系统产出的原始输入与路由元信息。
	"""

	turn_id: int = Field(description="回合号")
	trace_id: int = Field(default=0, description="链路追踪 ID")
	world_version: Optional[int] = Field(default=None, description="世界版本号")
	event_id: Optional[str] = Field(default=None, description="事件 ID")
	source_id: str = Field(default="", description="来源实体 ID（如 char-player-0000）")
	raw_text: str = Field(default="", description="原始输入文本")
	command: Optional[str] = Field(default=None, description="元命令名称（若 input_type=meta_command）")
	command_args: Dict[str, Any] = Field(default_factory=dict, description="元命令参数")
	metadata: Dict[str, Any] = Field(default_factory=dict, description="额外调试/追踪信息")


class E2IntentInfo(BaseModel):
	"""
	e2: 意图诠释
	由 dm_agent 产生的输入语义理解。
	"""

	intent: str = Field(default="", description="主意图")
	routing_hint: Optional[str] = Field(default=None, description="链路路由建议:是否需要鉴定,哪种鉴定类型,对抗还是数值,不需要鉴定就为null,需要就为num或者aginst")
	attributes: List[Optional[str]] = Field(default=None, description="需要进行鉴定的属性名称")
	against_char_id: List[Optional[str]] = Field(default=None, description="对抗鉴定对象的角色 id,第一个默认被鉴定者,如果对抗鉴定发起鉴定方第一个,被挑战的人第二个")
	difficulty: Optional[str] = Field(default=None, description="鉴定难度:普通,困难,简单")
	dm_reply: Optional[str] = Field(default=None, description="若该输入应由 DM 直接回复，则填回复文本；否则为 null")




class E3RuleResult(BaseModel):
	"""
	e3: 规则结算事实
	由 rule_system 产出的客观判断。
	"""
	intent: str = Field(default="",description="直接来自dm_agent解析出的意图")
	success: str = Field(default="", description="成功,失败,还是大成功,大失败")



class E4EvolutionStepResult(BaseModel):
	"""
	e4: 步骤结算
	由 evolution_agent 产生的推演结算。
	"""

	summary: str = Field(default="", description="本步骤摘要")


class E4SchedulerStepResult(BaseModel):
	"""
	e4: 步骤结算
	由 npc_scheduler 产生、给 npc_performer 消费的额外上下文。
	"""

	extra_npc_context: Dict[str, Optional[str]] = Field(default_factory=dict, description="scheduler 给 performer 的额外信息")



class E7CausalityChain(BaseModel):
	"""
	e7: 回合因果链
	记录narrative_agent产生的叙事输出以及其时序关系。
	"""
	narrative_list: List[Dict[str, str]] = Field(default_factory=list, description="narrative_agent 的输出列表，key 使用统一 trace_id（链路追踪编号）")
	


class FallbackError(BaseModel):
	"""状态变更失败后的重试/降级信息。"""

	code: str = Field(default="", description="错误码")
	message: str = Field(default="", description="错误信息")
	retry_count: int = Field(default=0, description="已重试次数")
	retriable: bool = Field(default=True, description="是否可重试")
	rollback_applied: bool = Field(default=False, description="是否已回滚")
	degraded_output: Optional[str] = Field(default=None, description="降级输出")
	details: Dict[str, Any] = Field(default_factory=dict, description="错误详情")


# ============================================================
# 各 Agent 链路输入容器
# ============================================================


class DmAgentChainInput(BaseModel):
	"""dmagent: e1"""

	e1: E1InputInfo = Field(description="输入信息")


class EvolutionAgentChainInput(BaseModel):
	"""evolution: e1 + e3 + e7"""

	e1: E1InputInfo = Field(description="输入信息")
	e3: E3RuleResult = Field(description="规则结算事实")
	e7: E7CausalityChain = Field(description="回合因果链")


class StateChangeAgentChainInput(BaseModel):
	"""state: e4 + fallback_error"""

	e4: E4EvolutionStepResult = Field(description="步骤结算（来自 evolution）")
	fallback_error: Optional[FallbackError] = Field(default=None, description="失败重试/降级信息")


class NpcSchedulerAgentChainInput(BaseModel):
	"""npcscheduler: e4"""

	e4: E4EvolutionStepResult = Field(description="步骤结算（来自 evolution）")


class NpcPerformerAgentChainInput(BaseModel):
	"""npcperformer: e4(来自 scheduler 的额外信息) + e1"""

	e4: E4SchedulerStepResult = Field(description="步骤结算（含 scheduler 提供的额外上下文）")
	e1: E1InputInfo = Field(description="输入信息")


class NarrativeAgentChainInput(BaseModel):
	"""narrative: e4(来自 evolution)"""

	e4: E4EvolutionStepResult = Field(description="步骤结算（来自 evolution）")


class MergerAgentChainInput(BaseModel):
	"""merger_agent: e7"""

	e7: E7CausalityChain = Field(description="回合因果链")

