# Phase5 NPC 调度与敏捷前置校验完成记录

## 本次完成内容

- 在 [src/engine/bootstrap_validation.py](c:\Users\25173\Desktop\engine_refacting\src\engine\bootstrap_validation.py) 新增引擎启动前置校验：
  - 所有角色必须具备 `敏捷/dexterity` 属性
  - 缺失时直接阻断 `Engine` 初始化，并给出明确报错
- 在 [src/engine/engine.py](c:\Users\25173\Desktop\engine_refacting\src\engine\engine.py) 接入启动校验，并把 NPC 调度预算参数传入 `NpcSchedulerAgent`
- 重写 [src/agent/llm/npc_schedul_agent.py](c:\Users\25173\Desktop\engine_refacting\src\agent\llm\npc_schedul_agent.py) 的系统侧调度后处理：
  - 对候选 NPC 做存在性过滤
  - 过滤 `hp/health/san/sanity` 等关键状态值为 0 的 NPC
  - 应用 `cooldown_turns`
  - 按 `敏捷/dexterity` 降序排序
  - 应用 `max_actions_per_turn`
- 新增 [src/agent/llm/npc_perform_agent.py](c:\Users\25173\Desktop\engine_refacting\src\agent\llm\npc_perform_agent.py)：
  - 执行 scheduler 选中的 NPC 行为
  - 通过系统侧逻辑维护 `current_event`
  - 写入 `short`、`short_log`、`log`
  - 维护 `base_goal`、`active_goal` 和 `goal_history`
- 在 [src/engine/engine.py](c:\Users\25173\Desktop\engine_refacting\src\engine\engine.py) 接入 `npc_performer` 分支：
  - 基于 scheduler 输出逐个构建 performer 输入
  - 将 performer 输出并入 turn result
  - 将 performer 执行记录写入 agent io 日志
- 更新 [src/agent/prompt/npc_scheduler_prompt.py](c:\Users\25173\Desktop\engine_refacting\src\agent\prompt\npc_scheduler_prompt.py)，同步提示词中的硬约束与输出结构
- 扩展 scheduler 输出模型，新增 `scheduled_npc_ids`
- 更新场景样例与 phase2/3/4 相关测试数据，为角色补充 `dexterity`
- 新增 [tests/phase5/test_phase5_npc_performer.py](c:\Users\25173\Desktop\engine_refacting\tests\phase5\test_phase5_npc_performer.py) 验证 performer 会真正更新 NPC 目标和记忆

## 验证

执行命令：

```powershell
python -m unittest tests.phase2.test_phase2_serial_pipeline tests.phase3.test_phase3_concurrent_state_pipeline tests.phase3.test_agent_io_logging tests.phase4.test_phase4_narrative_merger tests.phase5.test_phase5_npc_scheduler tests.phase5.test_phase5_npc_performer
```

结果：

- Ran 21 tests
- OK

## 补充说明

- 由于仓库当前环境未安装 `pytest`，本次使用 `unittest` 完成定向验证
- PowerShell 启动时会额外打印本机 profile 中缺失脚本的提示，但不影响测试执行结果
