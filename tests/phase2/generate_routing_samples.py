from __future__ import annotations

from pathlib import Path

from src.data.model.base import Attribute, CharacterEntity, Description, MapEntity, WorldEntityStore
from src.data.model.world_state import WorldState
from src.engine.engine import Engine


def main() -> None:
    room = MapEntity(
        id="map-lab-0001",
        name="实验室",
        description=Description(public=["你在一个安静的实验室里。"]),
    )
    player = CharacterEntity(
        id="char-player-0000",
        name="玩家",
        location=room.id,
        attributes={
            "dexterity": Attribute(id="dexterity", name="敏捷", value=70, max_value=100, min_value=0),
            "fight": Attribute(id="fight", name="格斗", value=60, max_value=100, min_value=0),
            "investigation": Attribute(id="investigation", name="侦查", value=55, max_value=100, min_value=0),
            "stealth": Attribute(id="stealth", name="潜行", value=45, max_value=100, min_value=0),
        },
    )
    guard = CharacterEntity(
        id="char-guard-0001",
        name="守卫",
        location=room.id,
        attributes={
            "dexterity": Attribute(id="dexterity", name="敏捷", value=50, max_value=100, min_value=0),
            "fight": Attribute(id="fight", name="格斗", value=50, max_value=100, min_value=0),
        },
    )

    world = WorldState()
    world.reset(
        WorldEntityStore(
            maps={room.id: room},
            characters={player.id: player, guard.id: guard},
            items={},
        )
    )

    engine = Engine(world_state=world, mode="phase2")

    non_check = engine.run_turn(
        raw_input="我和守卫聊聊最近的情况",
        actor_id=player.id,
        turn_id=11,
        trace_id=2001,
    )
    check = engine.run_turn(
        raw_input="我攻击守卫",
        actor_id=player.id,
        turn_id=12,
        trace_id=2002,
    )

    lines = [
        "# 非鉴定意图与鉴定意图分流日志样例",
        "",
        "[non_check]",
        f"route={non_check['route']}",
        f"intent={non_check['dm']['intent_info']['intent']}",
        f"routing_hint={non_check['dm']['intent_info']['routing_hint']}",
        f"summary={non_check['evolution']['summary']}",
        "",
        "[check]",
        f"route={check['route']}",
        f"intent={check['dm']['intent_info']['intent']}",
        f"routing_hint={check['dm']['intent_info']['routing_hint']}",
        f"participants={check['dm']['intent_info']['against_char_id']}",
        f"e3_success={check['e3']['success']}",
        f"summary={check['evolution']['summary']}",
    ]

    output = Path("docs/phase/phase2/donelist/intent_routing_samples.log")
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
