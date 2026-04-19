from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, Optional

from src.data.model.input.agent_narrative_input import NarrativeInfo
from src.storage.sqlite_narrative_repository import SqliteNarrativeRepository


class NarrativeTruthManager:
    """Manage narrative-truth persistence boundaries for Engine."""

    @staticmethod
    def restore(repository: Optional[SqliteNarrativeRepository], current: NarrativeInfo) -> NarrativeInfo:
        if repository is None:
            return current
        return repository.load()

    @staticmethod
    def persist(repository: Optional[SqliteNarrativeRepository], narrative_info: NarrativeInfo) -> None:
        if repository is None:
            return
        repository.save(narrative_info)

    def commit_merged_narrative(
        self,
        *,
        repository: Optional[SqliteNarrativeRepository],
        narrative_info: NarrativeInfo,
        turn_id: int,
        merged_text: str,
        player_narrative_text: str,
        npc_visible_narratives: Iterable[str],
        recent_limit: int,
        emit_event: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> None:
        merged = str(merged_text or "").strip()
        if not merged:
            return

        narrative_info.add_narrative(
            turn=turn_id,
            content=merged,
            source="merger_agent",
            max_recent=recent_limit,
        )

        player_text = str(player_narrative_text or "").strip()
        if player_text:
            narrative_info.append_log(
                turn=turn_id,
                content=player_text,
                source="narrative_agent",
            )

        for npc_text in npc_visible_narratives:
            text = str(npc_text or "").strip()
            if not text:
                continue
            narrative_info.append_log(
                turn=turn_id,
                content=text,
                source="npc_narrative",
            )

        self.persist(repository=repository, narrative_info=narrative_info)

        if callable(emit_event):
            try:
                emit_event(
                    {
                        "event": "narrative.truth.committed",
                        "data": {
                            "turn_id": turn_id,
                            "content": merged,
                        },
                    }
                )
            except Exception:
                # Narrative event bridging must never break gameplay pipeline.
                return
