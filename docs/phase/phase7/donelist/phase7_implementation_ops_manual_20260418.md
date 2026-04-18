# Phase7 实现与部署手册

## 1. 当前实现总览
引擎采用“规则结算 + 多 Agent 生成 + 双真值池持久化”架构：
- 输入分流：`InputSystem`
- 规则结算：`RuleSystem`
- 推演与并发：`EvolutionAgent` 后并发触发 `NpcScheduler/StateChange/Narrative`
- NPC 闭环：`NpcPerformer`
- 叙事合并：`MergerAgent`
- 一致性维护：`ConsistencyAgent`

核心文件：
- `src/engine/engine.py`
- `world/run_stage_main.py`
- `streamlit_app.py`

## 2. 系统架构说明（运行期）
### 2.1 主链路
1. 输入进入 `InputSystem`
2. 元命令走 `rule_system_meta` 快速分支
3. 自然语言进入 DM + Rule + Evolution
4. phase3 并发分支：
   - NPC Scheduler
   - State Change（含重试与回滚）
   - Narrative（仅 visible_to_player=true）
5. State 成功后执行 NPC Performer 下游链
6. Merger 合并本回合叙事并写入 narrative truth

### 2.2 双真值池
- 世界真值：`world/world_snapshots.sqlite3`
- 叙事真值：`world/narrative_truth.sqlite3`

### 2.3 可观测性
- 结构化 I/O：`world/log/agent_io.jsonl`
- 并发时间线：`turn_result.parallel_timeline`
- Streamlit Debug：E1-E7、快照、叙事池、agent I/O

## 3. 部署前准备
### 3.1 环境
```powershell
conda activate a_engine
cd C:\Users\25173\Desktop\engine_refacting
```

### 3.2 配置
检查 `config/config.yaml`：
- `llm.api_key`
- `llm.api_base`
- `llm.model`
- `system.max_retry_count`
- `storage.world.sqlite_path`
- `storage.narrative.sqlite_path`

### 3.3 启动
开发调试：
```powershell
streamlit run streamlit_app.py
```

命令行回归：
```powershell
python world/run_stage_main.py --mode phase3 --scene world/simple_stage_scene.json
```

## 4. 运维排障流程
### 4.1 回合失败
1. 在 Debug 面板定位对应 `trace_id`
2. 查看 `state.error_history` 与 `fallback_error`
3. 对照 `agent_io.jsonl` 复盘同 trace 的 state_change 输入输出

### 4.2 叙事异常
1. 查看 `narrative.stream_events`
2. 查看 `merger.llm_output.narrative_str`
3. 检查 `narrative_truth.sqlite3` 的 recent/log 是否同步

### 4.3 一致性阻断
1. 检查 `route=consistency_blocked`
2. 读取返回 `message`
3. 按阻断原因修复场景/补丁逻辑后重启会话

## 5. trace_id 链路追踪操作手册
1. 在 Streamlit 输入一条玩家消息。
2. 打开 Debug 页，定位该回合 trace。
3. 依次查看：
   - E1 输入
   - E3 规则结果
   - E4 推演与调度
   - E5 状态提交
   - E6 叙事投影
   - E7 因果合并
4. 在 Agent I/O 面板切换 agent，使用左右箭头逐条核对输入输出。

## 6. 生产待检查物料对照
- [x] 包含对话框、世界选择、聊天输入、API配置、玩家可见流式输出的 Streamlit UI
- [x] 系统架构说明（本手册第 1-2 章）
- [x] 全流程 trace_id 链路追踪说明（本手册第 5 章 + Debug UI）
- [x] 开发指南手册（见 `phase7_developer_handbook_20260418.md`）

## 7. 当前边界
- 当前 Streamlit 为单进程单会话调试定位，未接入多用户会话管理。
- 未做容器化编排（可在下一阶段补 Dockerfile 与进程健康检查）。
- 未接入外部监控系统（Prometheus/Grafana），目前以结构化日志和 UI 调试为主。
