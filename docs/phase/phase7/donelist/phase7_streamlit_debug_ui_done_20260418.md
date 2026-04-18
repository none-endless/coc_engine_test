# Phase7 Streamlit Play&&Debug UI 完成记录

## 本次目标
- 提供可游玩的 Web 面板。
- 在 Debug 模式下支持以 trace_id 追踪一笔输入从 InputSystem 到 MergerAgent 的完整生命周期。
- 提供世界选择、聊天输入、API 配置、玩家可见叙事流式显示。

## 已完成交付
1. 新增 Streamlit 应用入口：`streamlit_app.py`
2. 完成游玩 UI：
   - 对话框（聊天记录）
   - 聊天输入框（`st.chat_input`）
   - 世界选择（扫描 `world/*.json`）
   - API 参数覆盖（api_key / api_base / model / reasoning / temperature / tokens / timeout）
3. 完成玩家可见信息流式展示：
   - 优先消费 `narrative.stream_events`（`narrative.delta`）
   - 自动回退到 narrative / merger / evolution / fallback 输出
4. 完成 Debug UI：
   - 回合左右切换（上一回合/下一回合）
   - 当前回合 Trace/Turn/Route 指标
   - 世界快照展示
   - 叙事池（narrative_info）展示
   - 并发分支时间线（parallel_timeline）
   - E1-E7 结构化链路视图
   - Agent 级完整 I/O 展示（按 agent 切换 + 左右箭头切记录）
5. 日志能力：
   - 内存日志用于当前会话调试
   - 继续落盘到 `world/log/agent_io.jsonl`

## Trace 追踪说明
在 Debug 面板选择任意回合后可直接看到：
- E1：玩家输入与 DM 的 e1_view
- E2：叙事池状态
- E3：规则结算结果
- E4：推演/调度/NPC链路
- E5：状态提交结果
- E6：叙事投影
- E7：evolution 与 npc performer 的因果链及 merger 输出

该视图可满足“通过 trace_id 追踪从 InputSystem 到 MergerAgent 的完整生命周期”的验收要求。

## 运行方式（conda + a_engine）
```powershell
conda activate a_engine
cd C:\Users\25173\Desktop\engine_refacting
streamlit run streamlit_app.py
```

## 使用建议
- 快速本地联调：侧栏关闭“使用真实 LLM”，使用内置 `StageSceneLLMService`。
- 真实链路联调：开启“使用真实 LLM”，并在侧栏填写 API 覆盖项或走 `config/config.yaml`。
- 每次切换世界或配置后，点击“重建引擎”。

## 当前边界
- 当前 UI 以单会话单玩家为目标，未实现多人并发会话隔离。
- Agent I/O 展示以 engine/io_logger 采集为准，不额外重算链路。
