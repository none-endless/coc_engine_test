from __future__ import annotations

from typing import Optional

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
