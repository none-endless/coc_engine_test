"""Phase0 snapshot read/write isolation verification script."""

from src.data.model.base import CharacterEntity, MapEntity, WorldEntityStore
from src.data.model.world_state import WorldState


def run() -> None:
    map_a = MapEntity(id="map-lobby-0001", name="Lobby")
    map_b = MapEntity(id="map-yard-0001", name="Yard")
    npc = CharacterEntity(id="char-guard-0001", name="Guard", location=map_a.id)

    store = WorldEntityStore(
        maps={map_a.id: map_a, map_b.id: map_b},
        characters={npc.id: npc},
        items={},
    )

    world = WorldState()
    world.reset(store)

    before = world.get_snapshot()
    world.update_character_location(npc.id, map_b.id)
    after = world.get_snapshot()

    before_copy = world.get_map(map_b.id)
    before_copy.char_index.append("char-injected-0001")

    final_snapshot = world.get_snapshot()

    print("before:", before["maps"][map_a.id]["char_index"])
    print("after:", after["maps"][map_b.id]["char_index"])
    print("final:", final_snapshot["maps"][map_b.id]["char_index"])


if __name__ == "__main__":
    run()
