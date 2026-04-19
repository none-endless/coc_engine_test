"""
Agent 输入聚合模型（去冗余版）。

原则：
1. llm_input 仅放 LLM 需要的最小上下文。
2. system_input 放重试、追踪、原始链路等系统控制数据。
3. 一致性维护 agent 也必须走统一数据模型，不允许在 engine 中裸传字典替代契约。
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from .base import MemoryForNpc
from .infra import TurnEnvelope
from .input.agent_chain_input import (
    DmAgentChainInput,
    E4EvolutionStepResult,
    E4SchedulerStepResult,
    E7CausalityChain,
    EvolutionAgentChainInput,
    FallbackError,
    MergerAgentChainInput,
    NarrativeAgentChainInput,
    NpcPerformerAgentChainInput,
    NpcSchedulerAgentChainInput,
    StateChangeAgentChainInput,
)
from .input.agent_map_intput import (
    DMWorldView,
    NarrativeWorldView,
    NpcSchedulerWorldView,
    NpcWorldView,
    StateAgentWorldView,
)
from .input.agent_memory_input import DmMemory
from .input.agent_narrative_input import NarrativeInfo


class AgentIdentity(BaseModel):
    """每个 agent 的基础身份与 skill。"""

    id: str = Field(description="agent 名称，如 dmagent/evolution/state")
    skill: str = Field(default="", description="该 agent 的职责提示词")


class SystemExecutionMeta(BaseModel):
    """仅系统侧使用的执行元信息。"""

    turn_id: int = Field(default=0, description="回合号")
    trace_id: int = Field(default=0, description="链路追踪编号")
    world_version: Optional[int] = Field(default=None, description="世界版本号")
    event_id: Optional[str] = Field(default=None, description="事件 ID")
    debug: Dict[str, str] = Field(default_factory=dict, description="调试信息")
    turn_envelope: Optional[TurnEnvelope] = Field(default=None, description="统一回合事务封装")


class SystemRetryControl(BaseModel):
    """重试控制，仅系统使用。"""

    can_retry: bool = Field(default=False, description="是否允许重试")
    retry_budget: int = Field(default=0, description="剩余重试次数")
    fallback_error: Optional[FallbackError] = Field(default=None, description="最近一次失败信息")


class E1LlmView(BaseModel):
    """e1 的 LLM 精简视图。"""

    raw_text: str = Field(default="", description="输入文本")
    source_id: str = Field(default="", description="来源实体 ID")


class E3LlmView(BaseModel):
    """e3 的 LLM 精简视图。"""

    success: str = Field(default="", description="规则结算结果")


class E4EvolutionLlmView(BaseModel):
    """e4 的 LLM 精简视图，来自 evolution。"""

    summary: str = Field(default="", description="步骤摘要")


class E4SchedulerLlmView(BaseModel):
    """e4 的 LLM 精简视图，来自 scheduler。"""

    scheduled_npc_ids: list[str] = Field(default_factory=list, description="本回合实际进入调度的 NPC ID 顺序列表")
    extra_npc_context: Dict[str, Optional[str]] = Field(default_factory=dict, description="scheduler 给 performer 的额外上下文")


class E7LlmView(BaseModel):
    """e7 的 LLM 精简视图。"""

    narrative_causality: str = Field(default="", description="回合因果链摘要")


class StateErrorFeedback(BaseModel):
    """给 state_change 的可行动错误反馈，提供给 LLM。"""

    message: str = Field(default="", description="失败原因描述")
    details: Dict[str, str] = Field(default_factory=dict, description="可用于修正的细节")
    fix_hint: str = Field(default="", description="系统给出的修正建议")


class AvailableAttributeRef(BaseModel):
    """DM 可用属性引用。"""

    id: str = Field(default="", description="属性 ID")
    name: str = Field(default="", description="属性展示名")


class AvailableCharacterRef(BaseModel):
    """DM 可用角色引用。"""

    id: str = Field(default="", description="角色 ID")
    name: str = Field(default="", description="角色展示名")


class DmAgentLlmInput(BaseModel):
    """dmagent 的 LLM 输入。"""

    e1: E1LlmView = Field(description="链路输入（e1 精简）")
    world_info: DMWorldView = Field(description="世界信息（描述层视图）")
    narrative_info: NarrativeInfo = Field(description="叙事信息")
    agent_memory: DmMemory = Field(description="DM 记忆")
    available_attributes: list[AvailableAttributeRef] = Field(default_factory=list, description="当前玩家可用于鉴定的属性列表")
    valid_characters: list[AvailableCharacterRef] = Field(default_factory=list, description="当前可引用的合法角色列表")


class DmAgentSystemInput(BaseModel):
    """dmagent 的系统输入。"""

    chain_raw: Optional[DmAgentChainInput] = Field(default=None, description="原始链路输入（完整 e1）")
    execution: SystemExecutionMeta = Field(default_factory=SystemExecutionMeta, description="系统执行元信息")


class DmAgentInput(BaseModel):
    identity: AgentIdentity = Field(description="agent 身份与 skill")
    llm_input: DmAgentLlmInput = Field(description="仅提供给 LLM 的输入")
    system_input: DmAgentSystemInput = Field(default_factory=DmAgentSystemInput, description="仅系统使用")


class EvolutionAgentLlmInput(BaseModel):
    """evolution 的 LLM 输入。"""

    e1: E1LlmView = Field(description="链路输入（e1 精简）")
    e3: E3LlmView = Field(description="链路输入（e3 精简）")
    e7: E7LlmView = Field(description="链路输入（e7 精简）")
    world_info: DMWorldView = Field(description="世界信息（描述层视图）")
    narrative_info: NarrativeInfo = Field(description="叙事信息")


class EvolutionAgentSystemInput(BaseModel):
    """evolution 的系统输入。"""

    chain_raw: Optional[EvolutionAgentChainInput] = Field(default=None, description="原始链路输入（完整 e1/e3/e7）")
    execution: SystemExecutionMeta = Field(default_factory=SystemExecutionMeta, description="系统执行元信息")


class EvolutionAgentInput(BaseModel):
    identity: AgentIdentity = Field(description="agent 身份与 skill")
    llm_input: EvolutionAgentLlmInput = Field(description="仅提供给 LLM 的输入")
    system_input: EvolutionAgentSystemInput = Field(default_factory=EvolutionAgentSystemInput, description="仅系统使用")


class StateAgentLlmInput(BaseModel):
    """state_change 的 LLM 输入。"""

    e4: E4EvolutionLlmView = Field(description="步骤结算（e4 精简，来自 evolution）")
    world_info: StateAgentWorldView = Field(description="世界信息（描述层 + 数值层）")
    fallback_error: Optional[StateErrorFeedback] = Field(default=None, description="上一轮失败原因与修正提示")


class StateAgentSystemInput(BaseModel):
    """state_change 的系统输入。"""

    chain_raw: Optional[StateChangeAgentChainInput] = Field(default=None, description="原始链路输入（含 fallback_error）")
    retry_control: SystemRetryControl = Field(default_factory=SystemRetryControl, description="系统重试控制")
    execution: SystemExecutionMeta = Field(default_factory=SystemExecutionMeta, description="系统执行元信息")


class StateAgentInput(BaseModel):
    identity: AgentIdentity = Field(description="agent 身份与 skill")
    llm_input: StateAgentLlmInput = Field(description="仅提供给 LLM 的输入")
    system_input: StateAgentSystemInput = Field(default_factory=StateAgentSystemInput, description="仅系统使用")


class NpcSchedulerAgentLlmInput(BaseModel):
    """npcscheduler 的 LLM 输入。"""

    e4: E4EvolutionLlmView = Field(description="链路输入（e4 精简，来自 evolution）")
    world_info: NpcSchedulerWorldView = Field(description="世界信息（切片）")
    narrative_info: NarrativeInfo = Field(description="叙事信息")


class NpcSchedulerAgentSystemInput(BaseModel):
    """npcscheduler 的系统输入。"""

    chain_raw: Optional[NpcSchedulerAgentChainInput] = Field(default=None, description="原始链路输入（完整 e4）")
    execution: SystemExecutionMeta = Field(default_factory=SystemExecutionMeta, description="系统执行元信息")


class NpcSchedulerAgentInput(BaseModel):
    identity: AgentIdentity = Field(description="agent 身份与 skill")
    llm_input: NpcSchedulerAgentLlmInput = Field(description="仅提供给 LLM 的输入")
    system_input: NpcSchedulerAgentSystemInput = Field(default_factory=NpcSchedulerAgentSystemInput, description="仅系统使用")


class NpcPerformerAgentLlmInput(BaseModel):
    """npcperformer 的 LLM 输入。"""

    e4: E4SchedulerLlmView = Field(description="链路输入（e4 精简，来自 scheduler）")
    e1: E1LlmView = Field(description="链路输入（e1 精简）")
    world_info: NpcWorldView = Field(description="世界信息（NPC 切片）")
    agent_memory: MemoryForNpc = Field(description="NPC 记忆")
    available_attributes: list[AvailableAttributeRef] = Field(default_factory=list, description="当前 NPC 可用于鉴定的属性列表")
    valid_characters: list[AvailableCharacterRef] = Field(default_factory=list, description="当前可引用的合法角色列表")


class NpcPerformerAgentSystemInput(BaseModel):
    """npcperformer 的系统输入。"""

    chain_raw: Optional[NpcPerformerAgentChainInput] = Field(default=None, description="原始链路输入（完整 e4/e1）")
    execution: SystemExecutionMeta = Field(default_factory=SystemExecutionMeta, description="系统执行元信息")


class NpcPerformerAgentInput(BaseModel):
    identity: AgentIdentity = Field(description="agent 身份与 skill")
    llm_input: NpcPerformerAgentLlmInput = Field(description="仅提供给 LLM 的输入")
    system_input: NpcPerformerAgentSystemInput = Field(default_factory=NpcPerformerAgentSystemInput, description="仅系统使用")


class NarrativeAgentLlmInput(BaseModel):
    """narrative 的 LLM 输入。"""

    e4: E4EvolutionLlmView = Field(description="链路输入（e4 精简，来自 evolution）")
    world_info: NarrativeWorldView = Field(description="世界信息（切片）")
    narrative_info: NarrativeInfo = Field(description="叙事信息")


class NarrativeAgentSystemInput(BaseModel):
    """narrative 的系统输入。"""

    chain_raw: Optional[NarrativeAgentChainInput] = Field(default=None, description="原始链路输入（完整 e4）")
    execution: SystemExecutionMeta = Field(default_factory=SystemExecutionMeta, description="系统执行元信息")


class NarrativeAgentInput(BaseModel):
    identity: AgentIdentity = Field(description="agent 身份与 skill")
    llm_input: NarrativeAgentLlmInput = Field(description="仅提供给 LLM 的输入")
    system_input: NarrativeAgentSystemInput = Field(default_factory=NarrativeAgentSystemInput, description="仅系统使用")


class MergerAgentLlmInput(BaseModel):
    """merger 的 LLM 输入。"""

    e7: E7LlmView = Field(description="链路输入（e7 精简）")
    world_info: NarrativeWorldView = Field(description="世界信息（切片）")
    narrative_info: NarrativeInfo = Field(description="叙事信息")
    narrative_str: str = Field(default="", description="narrative 分支输出的可见叙事文本（可为空）")


class MergerAgentSystemInput(BaseModel):
    """merger 的系统输入。"""

    chain_raw: Optional[MergerAgentChainInput] = Field(default=None, description="原始链路输入（完整 e7）")
    execution: SystemExecutionMeta = Field(default_factory=SystemExecutionMeta, description="系统执行元信息")


class MergerAgentInput(BaseModel):
    identity: AgentIdentity = Field(description="agent 身份与 skill")
    llm_input: MergerAgentLlmInput = Field(description="仅提供给 LLM 的输入")
    system_input: MergerAgentSystemInput = Field(default_factory=MergerAgentSystemInput, description="仅系统使用")


class ConsistencyRecentChangeLog(BaseModel):
    """一致性维护 agent 消费的最近变更日志条目。"""

    turn_id: int = Field(default=0, description="发生该变更的回合号")
    route: str = Field(default="", description="该回合的主路由")
    summary: str = Field(default="", description="供一致性维护使用的简短摘要")


class ConsistencyNarrationCandidate(BaseModel):
    """一致性维护中待压缩的叙事候选条目。"""

    turn: int = Field(default=0, description="叙事发生回合")
    content: str = Field(default="", description="叙事文本")


class ConsistencyDescriptionCandidate(BaseModel):
    """一致性维护中待压缩的描述候选条目。"""

    entity_id: str = Field(default="", description="待维护实体 ID")
    public: List[str] = Field(default_factory=list, description="实体当前 public 描述")
    add: List[str] = Field(default_factory=list, description="待合并的 add 描述内容")


class ConsistencyKeyFactsCandidate(BaseModel):
    """一致性维护中待提炼 key_facts 的候选条目。"""

    character_id: str = Field(default="", description="NPC 角色 ID")
    key_facts: List[str] = Field(default_factory=list, description="当前 key_facts")
    short_log: List[str] = Field(default_factory=list, description="待压缩的 short_log 事件")


class ConsistencyAgentLlmInput(BaseModel):
    """一致性维护 agent 的 LLM 输入。"""

    narration_candidates: List[ConsistencyNarrationCandidate] = Field(default_factory=list, description="待压缩叙事列表")
    description_candidates: List[ConsistencyDescriptionCandidate] = Field(default_factory=list, description="待压缩描述列表")
    key_facts_candidates: List[ConsistencyKeyFactsCandidate] = Field(default_factory=list, description="待提炼 key_facts 列表")
    recent_change_logs: List[ConsistencyRecentChangeLog] = Field(default_factory=list, description="最近若干回合的变更日志")
    config_json: Dict[str, Any] = Field(default_factory=dict, description="完整配置快照，供一致性策略决策使用")


class ConsistencyAgentSystemInput(BaseModel):
    """一致性维护 agent 的系统输入。"""

    execution: SystemExecutionMeta = Field(default_factory=SystemExecutionMeta, description="系统执行元信息")


class ConsistencyAgentInput(BaseModel):
    """一致性维护 agent 输入封装。"""

    identity: AgentIdentity = Field(description="agent 身份与 skill")
    llm_input: ConsistencyAgentLlmInput = Field(description="仅提供给 LLM 的输入")
    system_input: ConsistencyAgentSystemInput = Field(default_factory=ConsistencyAgentSystemInput, description="仅系统使用")


class TurnAgentInputs(BaseModel):
    """单回合所有 agent 输入聚合。"""

    dmagent: DmAgentInput = Field(description="DM agent 输入")
    evolution: EvolutionAgentInput = Field(description="Evolution agent 输入")
    state: StateAgentInput = Field(description="StateChange agent 输入")
    npcscheduler: NpcSchedulerAgentInput = Field(description="NpcScheduler agent 输入")
    npcperformer: NpcPerformerAgentInput = Field(description="NpcPerformer agent 输入")
    narrative: NarrativeAgentInput = Field(description="Narrative agent 输入")
    merger_agent: MergerAgentInput = Field(description="Merger agent 输入")
    consistency_agent: Optional[ConsistencyAgentInput] = Field(default=None, description="Consistency agent 输入")


class NarrativeProjectionE4(BaseModel):
    """E4 叙事投影。"""

    e4_from_narrative: E4EvolutionStepResult = Field(description="由 narrative 产出的叙事片段表达")


class WorldProjectionE5(BaseModel):
    """E5 世界投影。"""

    applied: bool = Field(default=False, description="是否已成功写入持久层")
    e7_ref: Optional[E7CausalityChain] = Field(default=None, description="关联因果链引用")
