import unittest

from src.agent.prompt.dm_prompt import DM_SYSTEM_PROMPT
from src.agent.prompt.evolution_prompt import EVOLUTION_SYSTEM_PROMPT
from src.agent.prompt.merger_prompt import MERGER_SYSTEM_PROMPT
from src.agent.prompt.narrative_prompt import NARRATIVE_SYSTEM_PROMPT
from src.agent.prompt.npc_performer_prompt import NPC_PERFORMER_SYSTEM_PROMPT
from src.agent.prompt.npc_scheduler_prompt import NPC_SCHEDULER_SYSTEM_PROMPT
from src.agent.prompt.state_change_prompt import STATE_CHANGE_SYSTEM_PROMPT


class TestPromptEducationOrientation(unittest.TestCase):
    def test_prompts_align_with_education_first_direction(self):
        self.assertIn("历史、文学与文化理解", DM_SYSTEM_PROMPT)
        self.assertIn("教育型场景演化代理", EVOLUTION_SYSTEM_PROMPT)
        self.assertIn("三顾茅庐", NARRATIVE_SYSTEM_PROMPT)
        self.assertIn("林黛玉到贾府", NARRATIVE_SYSTEM_PROMPT)
        self.assertIn("教育型叙事合并代理", MERGER_SYSTEM_PROMPT)
        self.assertIn("身份、礼仪", NPC_PERFORMER_SYSTEM_PROMPT)
        self.assertIn("教育型 NPC 调度代理", NPC_SCHEDULER_SYSTEM_PROMPT)
        self.assertIn("教育性取舍", STATE_CHANGE_SYSTEM_PROMPT)


    def test_narrative_prompt_limits_npc_appearance_to_activated_npcs(self):
        self.assertIn("NPC 出场边界", NARRATIVE_SYSTEM_PROMPT)
        self.assertIn("只能描写本回合已经被明确激活", NARRATIVE_SYSTEM_PROMPT)
        self.assertIn("未被激活", NARRATIVE_SYSTEM_PROMPT)


    def test_state_prompt_uses_summary_hints_for_location_changes(self):
        self.assertIn("Summary 中的隐含位置变化", STATE_CHANGE_SYSTEM_PROMPT)
        self.assertIn("summary 明确或隐含角色已经到达", STATE_CHANGE_SYSTEM_PROMPT)
        self.assertIn("必须判断是否需要生成 `MOVE`", STATE_CHANGE_SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
