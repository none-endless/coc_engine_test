import unittest
from pathlib import Path

from main import load_world_bundle


class TestWorld2Layout(unittest.TestCase):
    def test_world2_bundle_has_three_rooms_three_npcs_and_turn_limit(self):
        bundle = load_world_bundle(Path("world/world2"))

        self.assertEqual(bundle.scene_name, "竖锯试炼室")
        self.assertEqual(bundle.actor_id, "char-player-0000")
        self.assertEqual(bundle.turn_start, 1)
        self.assertEqual(bundle.turn_limit, 50)
        self.assertIn("回合耗尽", bundle.turn_limit_text or "")
        self.assertEqual(len(bundle.maps), 3)
        self.assertEqual(len(bundle.characters), 4)
        self.assertEqual(len(bundle.items), 3)
        self.assertEqual(len(bundle.endings), 4)


if __name__ == "__main__":
    unittest.main()
