"""
Agent 输出聚合模型。
原则：
1. 每个 Agent 输出拆分为 llm_output 与 system_output。
2. llm_output 仅承载模型产出内容。
3. system_output 承载调度、事务与追踪字段。
"""

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator

from .agent_input import E1LlmView, E4EvolutionLlmView, E4SchedulerLlmView
from .input.agent_chain_input import E2IntentInfo, E7CausalityChain


class AgentLlmOutputBase(BaseModel):
    """所有 LLM 输出的基类。"""


class AgentSystemOutputBase(BaseModel):
    """所有系统输出的基类。"""


class TurnTraceSystemOutputBase(AgentSystemOutputBase):
    """包含 turn_id 与 trace_id 的系统输出基类。"""

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
    visible_to_player: bool = Field(default=True, description="该变化是否对玩家可见")


class EvolutionAgentOutput(AgentOutputEnvelope):
    llm_output: EvolutionAgentLlmOutput = Field(description="LLM 输出")
    system_output: Optional[NoSystemOutput] = Field(default=None, description="系统输出（无）")


class NarrativeAgentLlmOutput(AgentLlmOutputBase):
    """narrative_agent 的 LLM 输出。"""

    narrative_str: str = Field(default="", description="叙事文本")


class NarrativeAgentSystemOutput(TurnTraceSystemOutputBase):
    """narrative_agent 的系统输出。"""


class NarrativeAgentOutput(AgentOutputEnvelope):
    llm_output: NarrativeAgentLlmOutput = Field(description="LLM 输出")
    system_output: NarrativeAgentSystemOutput = Field(description="系统输出")


class MergerAgentLlmOutput(AgentLlmOutputBase):
    """merger_agent 的 LLM 输出。"""

    narrative_str: str = Field(default="", description="合并后的叙事文本")


class MergerAgentSystemOutput(TurnTraceSystemOutputBase):
    """merger_agent 的系统输出。"""


class MergerAgentOutput(AgentOutputEnvelope):
    llm_output: MergerAgentLlmOutput = Field(description="LLM 输出")
    system_output: MergerAgentSystemOutput = Field(description="系统输出")


class CocCheckParticipant(BaseModel):
    """单个参与方的鉴定结果。"""

    id: str = Field(default="", description="参与方角色 ID")
    name: str = Field(default="", description="参与方角色名称")
    attribute: str = Field(default="", description="本次使用的属性名称")
    difficulty: Optional[str] = Field(default=None, description="本次鉴定难度")
    result_type: str = Field(default="", description="该参与方的原始结果")
    roll: int = Field(default=0, description="该参与方的骰点")
    target: int = Field(default=0, description="该参与方在当前难度下的目标值")
    is_winner: bool = Field(default=False, description="该参与方是否为本次对抗的获胜者")


class CocCheckResult(BaseModel):
    """rule_system 的结构化鉴定结果。"""

    check_type: str = Field(default="num", description="鉴定类型：num/against")
    id: str = Field(default="", description="触发鉴定的角色 ID")
    name: str = Field(default="", description="触发鉴定的角色名称")
    attribute: str = Field(default="", description="触发方使用的属性名称")
    difficulty: Optional[str] = Field(default=None, description="鉴定难度")
    result_type: str = Field(default="", description="触发方最终结果")
    roll: int = Field(default=0, description="本次骰点结果")
    target: int = Field(default=0, description="本次鉴定目标值")
    opposed_id: Optional[str] = Field(default=None, description="对抗鉴定中的对手 ID")
    opposed_name: Optional[str] = Field(default=None, description="对抗鉴定中的对手名称")
    winner_id: Optional[str] = Field(default=None, description="本次鉴定的获胜者 ID")
    affected_ids: List[str] = Field(default_factory=list, description="本次鉴定直接作用到的角色 ID 列表")
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

    op: str = Field(description="操作符")
    target_path: Optional[str] = Field(default=None, description="目标字段路径")
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
    scheduled_npc_ids: List[str] = Field(default_factory=list, description="本回合实际进入调度的 NPC ID 顺序列表")
    extra_npc_context: Dict[str, Optional[str]] = Field(default_factory=dict, description="scheduler 给 performer 的额外上下文")


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

    intent: str = Field(default="", description="NPC 互动类型")
    action_text: str = Field(default="", description="npc 输出行为文本")
    routing_hint: Optional[str] = Field(default=None, description="鉴定路由提示：num/against/null")
    attributes: List[str] = Field(default_factory=list, description="鉴定属性 ID 列表")
    against_char_id: List[str] = Field(default_factory=list, description="对抗鉴定角色 ID 列表")
    difficulty: Optional[str] = Field(default=None, description="可选鉴定难度，通常由系统决策")
    change_basic_goal: Optional[str] = Field(default=None, description="新的基础目标")
    change_active_goal: Optional[str] = Field(default=None, description="新的当前活跃目标")


class NpcPerformerPendingSideEffects(BaseModel):
    """由 performer 生成、由编排层决定何时提交的副作用载荷。"""

    current_event: str = Field(default="", description="本回合写入 NPC memory.current_event 的内容")
    append_short: bool = Field(default=False, description="是否将 current_event 追加到 memory.short")
    append_short_log: bool = Field(default=False, description="是否写入 memory.short_log")
    append_log: bool = Field(default=False, description="是否写入 memory.log")
    next_base_goal: Optional[str] = Field(default=None, description="可选的新 base_goal")
    next_active_goal: Optional[str] = Field(default=None, description="可选的新 active_goal")


class NpcPerformerAgentSystemOutput(TurnTraceSystemOutputBase):
    """npc_performer_agent 的系统输出。"""

    id: str = Field(default="", description="角色 id")
    pending_side_effects: NpcPerformerPendingSideEffects = Field(
        default_factory=NpcPerformerPendingSideEffects,
        description="待提交副作用，由编排层控制提交时机",
    )


class NpcPerformerAgentOutput(AgentOutputEnvelope):
    llm_output: NpcPerformerAgentLlmOutput = Field(description="LLM 输出")
    system_output: NpcPerformerAgentSystemOutput = Field(description="系统输出")


class NpcPerformerChainResult(BaseModel):
    """NPC performer 下游链路结果，用于把鉴定与演化结果接回主因果链。"""

    npc_id: str = Field(default="", description="触发本次下游链路的 NPC ID")
    intent: str = Field(default="", description="NPC 本次行为意图")
    check: Optional[CocCheckResult] = Field(default=None, description="可选的规则鉴定结果")
    check_error: Optional[str] = Field(default=None, description="鉴定执行失败时的错误信息")
    evolution_summary: str = Field(default="", description="NPC 下游演化摘要")
    evolution_visible_to_player: bool = Field(default=True, description="该演化是否对玩家可见")
    e7: E7CausalityChain = Field(default_factory=E7CausalityChain, description="NPC 下游并回主链前的因果链投影")


class ConsistencySummaryKind(str, Enum):
    """一致性压缩结果类型。"""

    NARRATION = "narration"
    DESCRIPTION = "description"
    KEY_FACTS = "key_facts"


class ConsistencySummaryItem(BaseModel):
    """一致性维护返回的单条压缩结果。"""

    kind: ConsistencySummaryKind = Field(description="压缩结果类型")
    value: str = Field(default="", description="压缩后的文本内容")


class ConsistencyAgentLlmOutput(AgentLlmOutputBase):
    """一致性维护 agent 的 LLM 输出。"""

    summary_items: List[ConsistencySummaryItem] = Field(default_factory=list, description="压缩结果列表，首项必须为 narration")
    can_proceed: bool = Field(default=True, description="当前快照是否允许继续被后续流程消费")
    system_message: str = Field(default="", description="最小系统提示，用于阻断或降级消息")

    @model_validator(mode="after")
    def _validate_summary_items(self) -> "ConsistencyAgentLlmOutput":
        """强约束输出结构，保证首项为 narration 且所有 value 非空。"""
        if not self.summary_items:
            raise ValueError("summary_items 不能为空")
        if self.summary_items[0].kind != ConsistencySummaryKind.NARRATION:
            raise ValueError("summary_items 第一项必须是 narration")
        for item in self.summary_items:
            if not item.value.strip():
                raise ValueError("summary_items.value 不能为空")
        return self


class ConsistencyAgentSystemOutput(AgentSystemOutputBase):
    """一致性维护 agent 的系统输出。"""

    patch_meta: PatchMeta = Field(default_factory=PatchMeta, description="一致性修复补丁元信息")


class ConsistencyAgentOutput(AgentOutputEnvelope):
    """一致性维护 agent 输出封装。"""

    llm_output: ConsistencyAgentLlmOutput = Field(description="LLM 输出")
    system_output: ConsistencyAgentSystemOutput = Field(description="系统输出")


class TurnAgentOutputs(BaseModel):
    """单回合所有 agent 输出聚合。"""

    dmagent: DmAgentOutput = Field(description="DM agent 输出")
    evolution: EvolutionAgentOutput = Field(description="Evolution agent 输出")
    state: StateAgentOutput = Field(description="StateChange agent 输出")
    npcscheduler: NpcSchedulerAgentOutput = Field(description="NpcScheduler agent 输出")
    npcperformer: NpcPerformerAgentOutput = Field(description="NpcPerformer agent 输出")
    narrative: NarrativeAgentOutput = Field(description="Narrative agent 输出")
    merger_agent: MergerAgentOutput = Field(description="Merger agent 输出")
    consistency_agent: Optional[ConsistencyAgentOutput] = Field(default=None, description="Consistency agent 输出")


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
