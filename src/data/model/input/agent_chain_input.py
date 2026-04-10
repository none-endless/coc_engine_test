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


class E1InputInfo(BaseModel):
	"""
	e1: 输入信息
	输入系统产出的原始输入与路由元信息。
	"""

	turn: int = Field(description="回合号")
	source: InputSource = Field(description="输入来源")
	source_id: str = Field(default="", description="来源实体 ID（如 char-player-0000）")
	input_type: InputType = Field(description="输入类型")
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
	atirrbutes: str = Field(default=None,description="需要进行鉴定的属性名称")
	charlist : str = Field(default="",description="如果要对抗鉴定对象的id")
	hard: str = Field(default="",description="鉴定难度:普通,困难.简单")




class E3RuleResult(BaseModel):
	"""
	e3: 规则结算事实
	由 rule_system 产出的客观判断。
	"""

	sucusess: str = Field(description="成功,失败,还是大成功,大失败")



class E4StepResult(BaseModel):
	"""
	e4: 步骤结算
	由 evolution_agent / npc_scheduler 产生的推演结算。
	"""

	producer: str = Field(description="产生该 e4 的 agent 名称")
	summary: str = Field(default="", description="本步骤摘要")
	extra_npc_context: Dict[str, Any] = Field(default_factory=dict, description="scheduler 给 performer 的额外信息")
	metadata: Dict[str, Any] = Field(default_factory=dict, description="扩展信息")



class E7CausalityChain(BaseModel):
	"""
	e7: 回合因果链
	记录narrative_agent产生的叙事输出以及其时序关系。
	"""
	


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
	"""state: e4 + fallbackerror"""

	e4: E4StepResult = Field(description="步骤结算")
	fallbackerror: Optional[FallbackError] = Field(default=None, description="失败重试/降级信息")


class NpcSchedulerAgentChainInput(BaseModel):
	"""npcscheduler: e4"""

	e4: E4StepResult = Field(description="步骤结算")


class NpcPerformerAgentChainInput(BaseModel):
	"""npcperformer: e4(来自 scheduler 的额外信息) + e1"""

	e4: E4StepResult = Field(description="步骤结算（含 scheduler 提供的额外上下文）")
	e1: E1InputInfo = Field(description="输入信息")


class NarrativeAgentChainInput(BaseModel):
	"""narrative: e4(来自 evolution)"""

	e4: E4StepResult = Field(description="步骤结算（来自 evolution）")


class MergerAgentChainInput(BaseModel):
	"""merger_agent: e7"""

	e7: E7CausalityChain = Field(description="回合因果链")

