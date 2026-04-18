from __future__ import annotations

import json
from typing import Any, Dict, List


class NarrativeStreamInterface:
    """叙事流式接口适配层，把内部事件格式转换为 SSE / WebSocket 可直接消费的数据。"""

    @staticmethod
    def build_sse_frames(events: List[Dict[str, Any]]) -> List[str]:
        """把 narrative 事件列表转换成 SSE 文本帧。"""
        frames: List[str] = []
        for event in events:
            frames.append(
                f"event: {event.get('event', '')}\n"
                f"data: {json.dumps(event.get('data', {}), ensure_ascii=False)}\n\n"
            )
        return frames

    @staticmethod
    def build_websocket_messages(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """把 narrative 事件列表转换成 WebSocket 消息对象。"""
        return [
            {
                "type": "narrative.event",
                "event": event.get("event", ""),
                "data": event.get("data", {}),
            }
            for event in events
        ]

    @classmethod
    def build_transport_payload(cls, events: List[Dict[str, Any]]) -> Dict[str, List[Any]]:
        """统一导出给调用方的 SSE 与 WebSocket 负载。"""
        return {
            "sse": cls.build_sse_frames(events),
            "websocket": cls.build_websocket_messages(events),
        }
