"""
Agent Prompts Module

导出所有 Agent 的 System Prompt
"""

from src.agent.prompt.dm_prompt import DM_SYSTEM_PROMPT
from src.agent.prompt.evolution_prompt import EVOLUTION_SYSTEM_PROMPT
from src.agent.prompt.state_change_prompt import STATE_CHANGE_SYSTEM_PROMPT
from src.agent.prompt.npc_scheduler_prompt import NPC_SCHEDULER_SYSTEM_PROMPT
from src.agent.prompt.narrative_prompt import NARRATIVE_SYSTEM_PROMPT
from src.agent.prompt.merger_prompt import MERGER_SYSTEM_PROMPT
from src.agent.prompt.npc_performer_prompt import NPC_PERFORMER_SYSTEM_PROMPT
from src.agent.prompt.consistency_prompt import CONSISTENCY_SYSTEM_PROMPT

__all__ = [
    "DM_SYSTEM_PROMPT",
    "EVOLUTION_SYSTEM_PROMPT",
    "STATE_CHANGE_SYSTEM_PROMPT",
    "NPC_SCHEDULER_SYSTEM_PROMPT",
    "NARRATIVE_SYSTEM_PROMPT",
    "MERGER_SYSTEM_PROMPT",
    "NPC_PERFORMER_SYSTEM_PROMPT",
    "CONSISTENCY_SYSTEM_PROMPT",
]
