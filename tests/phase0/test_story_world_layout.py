import unittest
from pathlib import Path

from main import _build_world_store, check_endings_at_turn_start, load_world_bundle
from src.data.model.world_state import WorldState
from src.rule.rule_system import RuleSystem


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
        self.assertEqual(len(bundle.endings), 4)
        zhuge_liang = bundle.characters["char-zhuge_liang-0001"]
        self.assertEqual(zhuge_liang["status"]["liubei_favor"]["value"], 20)
        self.assertEqual(zhuge_liang["status"]["study_meet_rounds"]["value"], 0)

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
        self.assertEqual(len(bundle.endings), 5)
        jia_mu = bundle.characters["char-jia_mu-0002"]
        self.assertEqual(jia_mu["status"]["daiyu_favor"]["value"], 20)
        self.assertEqual(jia_mu["status"]["first_meet_rounds"]["value"], 0)
        baoyu = bundle.characters["char-jia_baoyu-0003"]
        self.assertEqual(baoyu["status"]["daiyu_favor"]["value"], 20)
        self.assertEqual(baoyu["status"]["first_meet_rounds"]["value"], 0)

    def test_lindaiyu_bundle_good_ending_uses_baoyu_favor(self):
        bundle = load_world_bundle(Path("world/林黛玉到贾府"))
        store = _build_world_store(bundle)
        store.characters["char-player-0000"].location = "map-west_room-0004"
        store.items["item-family_letter-0001"].location = "char-player-0000"
        store.items["item-spirit_jade-0003"].location = "char-jia_baoyu-0003"
        store.characters["char-jia_baoyu-0003"].status["daiyu_favor"].value = 55

        world = WorldState()
        world.reset(store)

        ending = check_endings_at_turn_start(RuleSystem(world), world, bundle.endings)
        self.assertIsNotNone(ending)
        self.assertEqual(ending.ending_id, "ending-first_meet_baoyu-0001")

    def test_lindaiyu_bundle_good_ending_uses_jia_mu_favor(self):
        bundle = load_world_bundle(Path("world/林黛玉到贾府"))
        store = _build_world_store(bundle)
        store.characters["char-player-0000"].location = "map-grand_hall-0003"
        store.characters["char-jia_mu-0002"].status["daiyu_favor"].value = 55

        world = WorldState()
        world.reset(store)

        ending = check_endings_at_turn_start(RuleSystem(world), world, bundle.endings)
        self.assertIsNotNone(ending)
        self.assertEqual(ending.ending_id, "ending-meet_grandmother-0002")

    def test_lindaiyu_bundle_failed_to_settle_ending_uses_round_clock(self):
        bundle = load_world_bundle(Path("world/林黛玉到贾府"))
        store = _build_world_store(bundle)
        store.characters["char-jia_mu-0002"].status["daiyu_favor"].value = 54
        store.characters["char-jia_mu-0002"].status["first_meet_rounds"].value = 5

        world = WorldState()
        world.reset(store)

        ending = check_endings_at_turn_start(RuleSystem(world), world, bundle.endings)
        self.assertIsNotNone(ending)
        self.assertEqual(ending.ending_id, "ending-failed_to_settle-0004")

    def test_lindaiyu_bundle_failed_baoyu_meet_ending_uses_round_clock(self):
        bundle = load_world_bundle(Path("world/林黛玉到贾府"))
        store = _build_world_store(bundle)
        store.characters["char-jia_baoyu-0003"].status["daiyu_favor"].value = 54
        store.characters["char-jia_baoyu-0003"].status["first_meet_rounds"].value = 5

        world = WorldState()
        world.reset(store)

        ending = check_endings_at_turn_start(RuleSystem(world), world, bundle.endings)
        self.assertIsNotNone(ending)
        self.assertEqual(ending.ending_id, "ending-first_meet_baoyu_failed-0005")

    def test_sangu_maolu_bundle_good_ending_uses_kongming_favor(self):
        bundle = load_world_bundle(Path("world/三顾茅庐"))
        store = _build_world_store(bundle)
        store.characters["char-player-0000"].location = "map-cottage_study-0003"
        store.items["item-visit_card-0001"].location = "char-player-0000"
        store.items["item-gift_wine-0002"].location = "char-player-0000"
        store.characters["char-zhuge_liang-0001"].status["liubei_favor"].value = 55

        world = WorldState()
        world.reset(store)

        ending = check_endings_at_turn_start(RuleSystem(world), world, bundle.endings)
        self.assertIsNotNone(ending)
        self.assertEqual(ending.ending_id, "ending-kongming-out-0001")

    def test_sangu_maolu_bundle_failed_recruit_ending_uses_round_clock(self):
        bundle = load_world_bundle(Path("world/三顾茅庐"))
        store = _build_world_store(bundle)
        store.characters["char-zhuge_liang-0001"].status["liubei_favor"].value = 54
        store.characters["char-zhuge_liang-0001"].status["study_meet_rounds"].value = 5

        world = WorldState()
        world.reset(store)

        ending = check_endings_at_turn_start(RuleSystem(world), world, bundle.endings)
        self.assertIsNotNone(ending)
        self.assertEqual(ending.ending_id, "ending-failed-to-recruit-kongming-0004")


if __name__ == "__main__":
    unittest.main()
