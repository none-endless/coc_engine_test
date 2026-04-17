"""
Agent 输出聚合模型。

原则：
1. 每个 Agent 输出拆分为 llm_output 与 system_output。
2. llm_output 仅承载模型产出内容。
3. system_output 承载调度、事务与追踪字段。
"""

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from .agent_input import E1LlmView, E4EvolutionLlmView, E4SchedulerLlmView
from .input.agent_chain_input import E2IntentInfo


class AgentLlmOutputBase(BaseModel):
	"""所有 LLM 输出的基类。"""


class AgentSystemOutputBase(BaseModel):
	"""所有系统输出的基类。"""


class TurnTraceSystemOutputBase(AgentSystemOutputBase):
	"""包含 turn_id/trace_id 的系统输出基类。"""

	trace_id: int = Field(default=0, description="链路追踪编号")
	turn_id: int = Field(default=0, description="回合编号")


class AgentOutputEnvelope(BaseModel):
	"""输出封装基类。"""


class NoSystemOutput(AgentSystemOutputBase):
	"""无系统输出时使用。"""


class NoLlmOutput(AgentLlmOutputBase):
	"""无 LLM 输出时使用。"""


class DmAgentLlmOutput(AgentLlmOutputBase):
	"""dm_agent 的 LLM 输出。"""

	intent_info: E2IntentInfo = Field(description="输入语义理解")


class DmAgentSystemOutput(AgentSystemOutputBase):
	"""dm_agent 的系统输出。"""

	e1_view: E1LlmView = Field(description="e1 的 LLM 视图快照")


class DmAgentOutput(AgentOutputEnvelope):
	llm_output: DmAgentLlmOutput = Field(description="LLM 输出")
	system_output: DmAgentSystemOutput = Field(description="系统输出")


class EvolutionAgentLlmOutput(AgentLlmOutputBase):
	"""evolution_agent 的 LLM 输出。"""

	summary: str = Field(default="", description="步骤摘要")
	visible_to_player: bool = Field(default=True, description="该变化是否对玩家可见，不可见则不经过 narrative_agent")


class EvolutionAgentOutput(AgentOutputEnvelope):
	llm_output: EvolutionAgentLlmOutput = Field(description="LLM 输出")
	system_output: Optional[NoSystemOutput] = Field(default=None, description="系统输出（无）")
	


class NarrativeDraftStatus(str, Enum):
	"""叙事草稿状态。"""

	DRAFT = "draft"
	COMMITTED = "committed"
	DISCARDED = "discarded"


class NarrativeDraft(BaseModel):
	"""叙事草稿，状态提交成功前仅允许保持 draft。"""

	draft_id: str = Field(default="", description="草稿 ID")
	trace_id: int = Field(default=0, description="链路追踪 ID")
	turn_id: int = Field(default=0, description="回合号")
	content: str = Field(default="", description="草稿内容")
	visible_to_player: bool = Field(default=True, description="是否可见给玩家")
	status: NarrativeDraftStatus = Field(default=NarrativeDraftStatus.DRAFT, description="草稿状态")


class NarrativeAgentLlmOutput(AgentLlmOutputBase):
	"""narrative_agent 的 LLM 输出。"""

	narrative_str: str = Field(default="", description="叙事文本")
	narrative_draft: Optional[NarrativeDraft] = Field(default=None, description="结构化叙事草稿")


class NarrativeAgentSystemOutput(TurnTraceSystemOutputBase):
	"""narrative_agent 的系统输出。"""


class NarrativeAgentOutput(AgentOutputEnvelope):
	llm_output: NarrativeAgentLlmOutput = Field(description="LLM 输出")
	system_output: NarrativeAgentSystemOutput = Field(description="系统输出")


class MergerAgentLlmOutput(AgentLlmOutputBase):
	"""merger_agent 的 LLM 输出。"""

	narrative_str: str = Field(default="", description="叙事文本")


class MergerAgentOutput(AgentOutputEnvelope):
	llm_output: MergerAgentLlmOutput = Field(description="LLM 输出")
	system_output: Optional[NoSystemOutput] = Field(default=None, description="系统输出（无）")


class CocCheckParticipant(BaseModel):
	"""单个参与方的鉴定结果。"""

	id: str = Field(default="", description="参与方角色 ID")
	name: str = Field(default="", description="参与方角色名称")
	attribute: str = Field(default="", description="本次使用的属性名称")
	difficulty: Optional[str] = Field(default=None, description="本次鉴定难度")
	result_type: str = Field(default="", description="该参与方的原始结果：成功/失败/大成功/大失败")
	roll: int = Field(default=0, description="该参与方的骰点")
	target: int = Field(default=0, description="该参与方在当前难度下的目标值")
	is_winner: bool = Field(default=False, description="该参与方是否为本次对抗的胜者")


class CocCheckResult(BaseModel):
	"""rule_system 的结构化鉴定结果。"""

	check_type: str = Field(default="num", description="鉴定类型：num/against")
	id: str = Field(default="", description="触发鉴定的角色 ID")
	name: str = Field(default="", description="触发鉴定的角色名称")
	attribute: str = Field(default="", description="触发方使用的属性名称")
	difficulty: Optional[str] = Field(default=None, description="鉴定难度")
	result_type: str = Field(default="", description="触发方最终结果：成功/失败/大成功/大失败")
	roll: int = Field(default=0, description="本次骰点结果")
	target: int = Field(default=0, description="本次鉴定目标值")
	opposed_id: Optional[str] = Field(default=None, description="对抗检定中的对手 ID")
	opposed_name: Optional[str] = Field(default=None, description="对抗检定中的对手名称")
	winner_id: Optional[str] = Field(default=None, description="本次检定的胜者 ID")
	affected_ids: List[str] = Field(default_factory=list, description="本次检定直接作用到的角色 ID 列表")
	participants: List[CocCheckParticipant] = Field(default_factory=list, description="全部参与方的结构化结果")


class StateOperator(str, Enum):
	"""状态变更操作符。"""

	ADD = "ADD"
	REMOVE = "REMOVE"
	SET = "SET"
	UPDATE = "UPDATE"
	MOVE = "MOVE"
	ASSERT = "ASSERT"


class StateChangeOp(BaseModel):
	"""单条状态变更操作。"""

	op: StateOperator = Field(description="操作符")
	target_path: Optional[str] = Field(default=None, description="目标字段路径，例如 char-player-0000.attributes.health.value")
	value: Any = Field(default=None, description="操作值")
	condition: Optional[str] = Field(default=None, description="ASSERT 条件表达式")
	reason: Optional[str] = Field(default=None, description="可选解释信息")


class PatchMeta(BaseModel):
	"""状态补丁元信息。"""

	trace_id: int = Field(default=0, description="链路追踪编号")
	turn_id: int = Field(default=0, description="回合编号")
	retry_seq: int = Field(default=0, description="重试序号")
	patch_id: Optional[str] = Field(default=None, description="补丁唯一标识")
	expected_version: Optional[int] = Field(default=None, description="期望世界版本")


class StateAgentLlmOutput(AgentLlmOutputBase):
	"""state_agent 的 LLM 输出。"""

	changes: List[StateChangeOp] = Field(default_factory=list, description="状态变更列表")


class StateAgentSystemOutput(AgentSystemOutputBase):
	"""state_agent 的系统输出。"""

	patch_meta: PatchMeta = Field(default_factory=PatchMeta, description="补丁元信息")


class StateAgentOutput(AgentOutputEnvelope):
	llm_output: StateAgentLlmOutput = Field(description="LLM 输出")
	system_output: StateAgentSystemOutput = Field(description="系统输出")


class NpcSchedulerStepResultOutput(BaseModel):
	"""npc_scheduler 步骤结果输出。"""

	summary: str = Field(default="", description="来自 evolution 的步骤摘要")
	extra_npc_context: Dict[str, Optional[str]] = Field(default_factory=dict, description="scheduler 给 performer 的额外上下文,key表示需要激活的npcid,value是从summary中总结提供给npc的额外信息,如果没有可以填null")


class NpcSchedulerAgentLlmOutput(AgentLlmOutputBase):
	"""npc_scheduler_agent 的 LLM 输出。"""

	step_result: NpcSchedulerStepResultOutput = Field(default_factory=NpcSchedulerStepResultOutput, description="调度步骤结果")


class NpcSchedulerAgentSystemOutput(TurnTraceSystemOutputBase):
	"""npc_scheduler_agent 的系统输出。"""


class NpcSchedulerAgentOutput(AgentOutputEnvelope):
	llm_output: NpcSchedulerAgentLlmOutput = Field(description="LLM 输出")
	system_output: NpcSchedulerAgentSystemOutput = Field(description="系统输出")


class NpcPerformerAgentLlmOutput(AgentLlmOutputBase):
	"""npc_performer_agent 的 LLM 输出。"""

	intent: str = Field(default="", description="NPC 互动类型：interaction(交互)/dialogue(对话)/description(描述)")
	action_text: str = Field(default="", description="npc 输出行为文本")
	change_basic_goal: Optional[str] = Field(default=None, description="新的基础目标，无则为 null")
	change_active_goal: Optional[str] = Field(default=None, description="新的当前活跃目标，无则为 null")


class NpcPerformerAgentSystemOutput(TurnTraceSystemOutputBase):
	"""npc_performer_agent 的系统输出。"""

	id: str = Field(default="", description="角色 id")


class NpcPerformerAgentOutput(AgentOutputEnvelope):
	llm_output: NpcPerformerAgentLlmOutput = Field(description="LLM 输出")
	system_output: NpcPerformerAgentSystemOutput = Field(description="系统输出")


class TurnAgentOutputs(BaseModel):
	"""单回合所有 agent 输出聚合。"""

	dmagent: DmAgentOutput = Field(description="DM agent 输出")
	evolution: EvolutionAgentOutput = Field(description="Evolution agent 输出")
	state: StateAgentOutput = Field(description="StateChange agent 输出")
	npcscheduler: NpcSchedulerAgentOutput = Field(description="NpcScheduler agent 输出")
	npcperformer: NpcPerformerAgentOutput = Field(description="NpcPerformer agent 输出")
	narrative: NarrativeAgentOutput = Field(description="Narrative agent 输出")
	merger_agent: MergerAgentOutput = Field(description="Merger agent 输出")


class EvolutionToNarrativeProjection(BaseModel):
	"""演化到叙事分支的投影。"""

	e4: E4EvolutionLlmView = Field(default_factory=E4EvolutionLlmView, description="来自 evolution 的步骤摘要")


class SchedulerToPerformerProjection(BaseModel):
	"""调度到执行分支的投影。"""

	e4: E4SchedulerLlmView = Field(default_factory=E4SchedulerLlmView, description="来自 scheduler 的额外上下文")


class StatePatchProjection(BaseModel):
	"""状态补丁投影。"""

	patch_meta: PatchMeta = Field(default_factory=PatchMeta, description="补丁元信息")
	changes: List[StateChangeOp] = Field(default_factory=list, description="状态变更列表")
	extensions: Dict[str, Any] = Field(default_factory=dict, description="扩展保留字段")
