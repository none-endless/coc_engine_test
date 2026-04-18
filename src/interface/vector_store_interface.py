from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class VectorStoreInterface(ABC):
    """长期记忆向量存储抽象接口，供后续接入真实向量数据库。"""

    @abstractmethod
    def upsert(self, *, vector_id: str, summary: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        """写入或更新一条长期记忆向量记录。"""

    @abstractmethod
    def delete(self, *, vector_id: str) -> None:
        """删除一条长期记忆向量记录。"""

    @abstractmethod
    def search(self, *, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """按查询文本检索最相关的长期记忆记录。"""
