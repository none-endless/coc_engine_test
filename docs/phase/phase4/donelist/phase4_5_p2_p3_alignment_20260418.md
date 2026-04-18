# Phase4/5 P2-P3 对齐完成记录

## 本轮目标

基于 `docs/review/review_result/review_debug_report_phase4_5.md` 中的 `P2-P3` 计划，本轮继续收口两类问题：

1. Phase4 的 narrative stream 只有内部事件切片，没有真正的接口适配层。
2. `NarrativeInfo` 仍然只是内存对象，没有独立持久化仓储，双真值池物理隔离存储缺少运行时实现。

## 已完成的对齐内容

### 1. 新增叙事流式接口适配层

- 新增 `src/interface/narrative_stream_interface.py`

当前提供三种接口能力：

- `build_sse_frames(events)`
- `build_websocket_messages(events)`
- `build_transport_payload(events)`

作用：

- 保留 `NarrativeAgent.build_stream_events(...)` 作为内部事件切片层
- 在其上新增真正面向前端接入的适配层
- 让调用方无需自己再拼接 SSE 文本帧或 websocket 消息结构

### 2. narrative 分支输出新增 `stream_transport`

- 修改 `src/engine/engine.py`

当 narrative 分支被触发时，当前回合返回结果里会同步包含：

- `narrative.stream_events`
- `narrative.stream_transport.sse`
- `narrative.stream_transport.websocket`

这样可以保证：

- 内部事件契约仍然可测试
- 外部接口层也已有稳定可消费的输出格式

### 3. 新增 NarrativeInfo 独立 SQLite 仓储

- 新增 `src/storage/sqlite_narrative_repository.py`

当前仓储负责独立持久化：

- `narrative_recent`
- `narrative_log`

并直接围绕 `src/data/model/input/agent_narrative_input.py` 中的 `NarrativeInfo`、`NarrativeEntry`、`NarrativeLogItem` 工作，没有在仓储层重新定义同义数据模型。

### 4. 新增 WorldSnapshot 独立 SQLite 仓储

- 新增 `src/storage/sqlite_world_snapshot_repository.py`

当前仓储负责独立持久化：

- `world_snapshots`

作用：

- 给世界真值提供独立 SQLite 快照存储
- 与 `NarrativeInfo` 使用不同 SQLite 文件，满足双真值池物理隔离的最小运行时落地

### 5. engine 接入双仓储

- 修改 `src/engine/engine.py`
- 修改 `src/config/loader.py`
- 修改 `config/config.yaml`

新增配置项：

- `storage.world.sqlite_path`
- `storage.narrative.sqlite_path`

当前接入行为：

- 引擎启动时会按配置创建双仓储
- 启动后会尝试从 narrative 仓储恢复 `NarrativeInfo`
- state 成功提交或回滚后，会把最新世界快照写入 world snapshot 仓储
- merger 成功写入 `NarrativeInfo` 后，会把 narrative truth 同步刷入 narrative 仓储

这样可以保证：

- `WorldState` 与 `NarrativeInfo` 不再只是内存中“两个对象”
- 两者已经具备真实、独立、可落盘的持久化通路

## 结构收口（P3）

### 1. 新增配置而不是硬编码路径

- SQLite 路径不再写死在代码中
- 改为统一从配置读取

### 2. 数据模型仍以 model 层为单一真值

- Narrative 仓储直接消费 `NarrativeInfo` 模型
- 没有在 storage / engine 中定义新的 narrative 同义结构

### 3. phase4/5 测试矩阵继续扩展

本轮新增覆盖了：

- narrative stream 接口层输出
- narrative truth 独立 SQLite 持久化
- world snapshot 独立 SQLite 持久化
- 引擎重启后从 narrative SQLite 恢复 narrative truth

## 测试与验证

本轮执行的测试：

```powershell
python -m unittest tests.phase4.test_phase4_narrative_merger
python -m unittest tests.phase3.test_phase3_concurrent_state_pipeline tests.phase5.test_phase5_npc_scheduler tests.phase5.test_phase5_npc_performer
```

结果：

- `tests.phase4.test_phase4_narrative_merger`：`Ran 3 tests, OK`
- `tests.phase3 + tests.phase5`：`Ran 13 tests, OK`

## 本轮新增或更新的验证点

### 1. Phase4 narrative stream 接口验证

- `tests/phase4/test_phase4_narrative_merger.py`

验证：

- `stream_transport.sse` 非空
- `stream_transport.websocket` 非空

### 2. 双 SQLite 仓储验证

- `tests/phase4/test_phase4_narrative_merger.py`

验证：

- narrative truth SQLite 文件存在
- world snapshot SQLite 文件存在
- world snapshot 仓储中可以读到最新世界快照

### 3. narrative truth 恢复验证

- `tests/phase4/test_phase4_narrative_merger.py`

验证：

- 首个 engine 写入 merger 结果后
- 新 engine 再次启动时可以从 narrative SQLite 恢复 `NarrativeInfo.recent`

## 当前已收口的边界

本轮已经完成：

- narrative stream 从内部事件格式推进到接口适配层
- 双真值池从“内存级分离”推进到“独立 SQLite 持久化”
- SQLite 路径外置到配置，不再硬编码
- phase4/5 的主要主链、事务边界、接口层和持久化层都已有最小可运行实现

## 当前仍保留的边界

本轮没有继续推进：

- 真实 HTTP SSE / WebSocket 服务端
- 真实 LLM 联调用例与终端过程落盘
- world truth 的完整数据库回放/恢复引导流程
- phase6/phase7 的一致性维护代理与全局修复调度

因此本轮结论是：

- `phase4` 已接近完成，但“真实网络接口服务”仍是后续可继续增强项
- `phase5` 的主闭环已经收口到可验收范围，后续重点应转向 phase6/7
