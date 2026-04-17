from __future__ import annotations

import statistics
import time
from pathlib import Path

from src.data.model.base import CharacterEntity, Description, MapEntity, WorldEntityStore
from src.data.model.world_state import WorldState
from src.rule import RuleSystem


def main() -> None:
    room = MapEntity(
        id="map-hall-0001",
        name="走廊",
        description=Description(public=["走廊昏暗，空气潮湿。"]),
    )
    player = CharacterEntity(id="char-player-0000", name="玩家", location=room.id)

    world = WorldState()
    world.reset(
        WorldEntityStore(
            maps={room.id: room},
            characters={player.id: player},
            items={},
        )
    )

    rule = RuleSystem(world_state=world)

    samples = []
    for _ in range(100):
        start = time.perf_counter()
        rule.run_meta_command(actor_id=player.id, command="\\look")
        end = time.perf_counter()
        samples.append((end - start) * 1000)

    output = [
        "# 元命令执行耗时日志（\u005clook）",
        f"count: {len(samples)}",
        f"avg_ms: {statistics.mean(samples):.6f}",
        f"p95_ms: {statistics.quantiles(samples, n=20)[18]:.6f}",
        f"max_ms: {max(samples):.6f}",
        "llm_call: false",
    ]

    out_path = Path("docs/phase/phase1/donelist/meta_command_latency.log")
    out_path.write_text("\n".join(output) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
