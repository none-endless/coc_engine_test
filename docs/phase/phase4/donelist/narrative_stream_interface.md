# Narrative 流式输出接口

## 目标

提供 Narrative 分支面向前端的轻量流式事件格式，便于后续接入 SSE 或 WebSocket。这里的流式内容就是玩家实际看到的 narrative 原文，不是 merger 压缩结果。

## 当前实现

位置：
- [src/agent/llm/narrative_agent.py](c:/Users/25173/Desktop/engine_refacting/src/agent/llm/narrative_agent.py)

接口：
- `NarrativeAgent.build_stream_events(output) -> list[dict]`

事件格式：

```json
{
  "event": "narrative.delta",
  "data": {
    "index": 0,
    "content": "他推门走出房间。"
  }
}
```

```json
{
  "event": "narrative.completed",
  "data": {
    "draft_id": "draft-4-4004",
    "content": "他推门走出房间，走廊里的冷风立刻扑了上来。"
  }
}
```

## 延迟测试样例

本地单元测试中使用伪 LLM，流式事件在 narrative 分支完成后一次性生成，目的是先固定事件契约。

建议前端接入时采集：

- `narrative_start_ms`
- `first_delta_ms`
- `completed_ms`
- `delta_count`

后续若接入真实 SSE/WebSocket，可保持事件格式不变，只替换事件发送时机。

## 边界说明

- 只有 `evolution_agent.visible_to_player = true` 时才会产生这些流式事件。
- merger 输出仅服务内部提示词压缩，不应进入玩家流式显示链路。
