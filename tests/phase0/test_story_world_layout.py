import unittest
from pathlib import Path

from main import load_world_bundle


class TestStoryWorldLayout(unittest.TestCase):
    def test_sangu_maolu_bundle_has_expected_layout(self):
        bundle = load_world_bundle(Path("world/三顾茅庐"))

        self.assertEqual(bundle.scene_name, "三顾茅庐")
        self.assertEqual(bundle.actor_id, "char-player-0000")
        self.assertEqual(bundle.turn_start, 1)
        self.assertEqual(bundle.turn_limit, 12)
        self.assertIn("十二回合", bundle.turn_limit_text or "")
        self.assertEqual(len(bundle.maps), 3)
        self.assertEqual(len(bundle.characters), 4)
        self.assertEqual(len(bundle.items), 3)
        self.assertEqual(len(bundle.endings), 3)

    def test_lindaiyu_bundle_has_expected_layout(self):
        bundle = load_world_bundle(Path("world/林黛玉到贾府"))

        self.assertEqual(bundle.scene_name, "林黛玉到贾府")
        self.assertEqual(bundle.actor_id, "char-player-0000")
        self.assertEqual(bundle.turn_start, 1)
        self.assertEqual(bundle.turn_limit, 16)
        self.assertIn("十六回合", bundle.turn_limit_text or "")
        self.assertEqual(len(bundle.maps), 4)
        self.assertEqual(len(bundle.characters), 4)
        self.assertEqual(len(bundle.items), 3)
        self.assertEqual(len(bundle.endings), 3)


if __name__ == "__main__":
    unittest.main()
