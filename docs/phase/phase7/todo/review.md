**Review 结果（按严重级排序）**
1. High: State 分支的 LLM 调用异常没有被重试/降级链路接住。证据在 engine.py 调用 state_agent.run，而异常处理只覆盖了 engine.py 的 StatePatchError。影响是网络抖动或模型异常会直接打断回合，绕过你设计的 fallback 与回滚策略。建议把 state_agent.run 包进可重试异常处理，统一转成 last_feedback/last_error 再走现有 retry 循环。

2. High: Consistency 分支同样只处理了本地校验错误，未处理 LLM/传输异常。证据在 engine.py 调用 consistency_agent.run，而仅捕获了 engine.py 的 ValueError；并且 consistency 在 engine.py 是状态提交后的阶段。影响是世界状态已经提交，但回合可能因一致性调用失败而异常退出，造成“已落库但前端报错”的体验和追踪不一致。建议在 consistency 循环里补齐 LLMServiceError/RuntimeError 兜底，并返回结构化阻断或降级结果而不是抛出。

3. High: 玩家 against 鉴定目标选择依赖数组顺序，且语义校验不充分。证据是 engine.py 直接使用 participant_ids[1] 作为 target；而 DM 语义校验只做了“至少2个ID”与合法性检查，见 input_agent.py。影响是当 against_char_id 顺序不符合预期时，会把鉴定打到错误对象甚至打到自己。建议改成“从 against_char_id 中选择第一个不等于 actor_id 的目标”，或在 DM 校验里强制 actor_id 必须在首位并去重。

4. High: NPC against 路径存在未捕获 KeyError 风险。证据是下游仅捕获 ValueError，见 engine.py；但 _run_npc_check 会直接 get_character(target_id)，见 engine.py，无效 ID 会抛 KeyError。另一个放大因素是 NPC performer 当前没有像 DM 那样的语义合法性校验，见 npc_perform_agent.py。影响是单个 NPC 输出脏 ID 可直接炸掉整回合。建议在 NPC performer 增加和 DM 对齐的校验与反馈重试，并在下游把 KeyError 归一化为 check_error 而非异常外抛。

5. Medium: WorldState 的单例实现会提高多世界切换与隔离难度。证据在 world_state.py 与 world_state.py。影响是二次构造实例时会复用旧对象，需要依赖 reset 才能切世界，和 Phase7 的世界选择/调试并行场景容易冲突。建议评估去单例化，或明确只允许单进程单世界并在入口层强制 reset。

6. Medium: 模型定义存在重复与可空列表不一致。证据是 TurnEnvelope 重复定义在 agent_chain_input.py 与 infra.py；另外 E2IntentInfo 中列表字段默认值为 None，见 agent_chain_input.py。影响是序列化契约容易漂移，调用方需要到处写 or [] 防御代码。建议统一 TurnEnvelope 来源，并将这两个字段改为 List[str] + default_factory=list。

7. Low: 状态修复提示文本出现乱码。证据见 engine.py。影响是反馈给模型的 fix_hint 可读性下降，降低自修复成功率。建议统一文件编码为 UTF-8 并修正该提示文本。

8. Low: 文件命名拼写错误影响维护可读性。见 agent_map_intput.py。影响是新成员搜索成本高、易产生重复文件。建议重命名为 agent_map_input.py 并保留一层兼容导入过渡。

**开放问题 / 假设**
1. against_char_id 的契约是否要强制“首位必须是发起方”，还是改成“无序集合语义”更稳妥？回答:首位必须是发起方
2. state 与 consistency 分支在 LLM 不可用时，你期望的是“降级继续回合”还是“硬阻断并提示重试”？回答:硬阻断并重试
3. Phase7 的世界选择是否需要同进程同时持有多个世界实例？如果是，WorldState 单例建议优先调整。回答:不需要同进程多个实例

**次要总结**
1. 主链路分层总体是清晰的，state patch 元信息、并发分支日志、叙事池写入流程都比较完整。
2. 当前主要风险集中在“异常处理闭环”和“against 语义约束”两块，优先修这两块能显著提升稳定性。
3. 本次是静态代码 review，未执行全量测试或压测。

4. 需要我直接按这 8 条给出一版最小修复补丁（先修 High，再修 Medium）。
5. 需要我补一组针对 High 问题的失败注入测试清单（state/consistency LLM 异常、against 顺序、NPC 脏 ID）。
6. 需要我把“代码清理任务3”整理成可勾选的 done/todo 文档，落到你 phase7 的文档目录。