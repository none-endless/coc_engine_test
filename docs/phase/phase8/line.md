基于你提供的全部代码文件（Engine、Agent 输入/输出模型、Agent 实现、服务层、世界状态等），以下是从**可修改性、可维护性、健壮性、优雅性**四个维度总结出的**待修改清单**，按优先级和影响范围排序。

---

## 🔴 高优先级修改（直接影响正确性或扩展性）

### 1. **`Engine` 类职责过重，需拆分**
**问题**：近 1500 行的 Engine 承担了太多职责，包括初始化所有 Agent、并行流程协调、叙事持久化、一致性维护、日志记录等，导致修改任何流程都需触碰核心类。  
**修改建议**：
- 抽取 `TurnOrchestrator` 类，负责单回合的完整流程编排（phase2/phase3）。
- 抽取 `ConsistencyOrchestrator` 类，负责一致性检查的触发、输入构建、结果应用。
- 抽取 `NarrativeTruthManager` 类，负责叙事真值的读写、持久化、事件发射。
- `Engine` 仅保留依赖组装、顶层入口 (`run_turn`) 和全局状态持有。

**参考代码位置**：
- `Engine._run_phase3_turn_async` (约 300 行) 可整体迁移到新类。
- `Engine._run_consistency_cycle` 及相关辅助方法可独立。

---

### 2. **`WorldState.get_snapshot()` 返回裸字典，丢失类型安全**
**问题**：快照返回 `Dict[str, object]`，调用方需通过字符串键访问且无类型提示，易出错。  
**修改建议**：
- 定义 `WorldSnapshot` Pydantic 模型，包含 `version`、`maps`、`characters`、`items` 等字段。
- `get_snapshot()` 返回该模型实例，使下游代码获得自动补全和校验。

**参考代码位置**：
- `world_state.py` 中的 `get_snapshot` 方法。
- `Engine._build_consistency_input` 等多处使用 `snapshot.get(...)`。

---

### 3. **`LLMServiceBase._extract_json_object` 对 Markdown 代码块清洗不健壮**
**问题**：仅处理以 ` ``` ` 开头的情况，未处理 ` ```json` 后的换行符、结尾空格等边缘情况。  
**修改建议**：
- 使用正则表达式 `re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)` 提取。
- 若无匹配，再回退到寻找第一个 `{` 和最后一个 `}`。

**参考代码位置**：
- `service.py` 第 180 行附近。

---

### 4. **`EvolutionAgent.run` 中的因果链兼容逻辑冗长且重复**
**问题**：对 `causality_chain` 做了多层类型判断和转换，而 Engine 中已有 `_normalize_causality_chain` 做类似处理。  
**修改建议**：
- 将归一化逻辑统一收敛到 Engine 的 `_normalize_causality_chain` 中，确保传入 Agent 的链始终是 `E7CausalityChain` 实例。
- `EvolutionAgent.run` 直接假设 `causality_chain` 为 `E7CausalityChain`。

**参考代码位置**：
- `evolution_agent.py` 第 45-55 行。
- `engine.py` 中的 `_normalize_causality_chain`。

---

### 5. **部分 Agent 的 `run` 签名不统一**
**问题**：
- `DMAgent.run` 额外接收 `available_attributes` 和 `valid_character_ids`。
- `NpcPerformerAgent.run` 却从内部查询这些值。
- 调用方需记忆差异，增加了理解成本。

**修改建议**：
- 将所有 Agent 运行所需数据预先封装到各自的 `*AgentInput` 中。
- `DMAgent.run` 改为仅接收 `agent_input`，并从 `agent_input.llm_input` 中提取所需字段（这些字段本就存在，只需在构建输入时填充完整）。

**参考代码位置**：
- `input_agent.py` 的 `run` 方法签名。
- `npc_perform_agent.py` 的 `run` 方法内部查询。

---

## 🟡 中优先级修改（提升代码质量和可维护性）

### 6. **视图类缺乏公共基类，存在字段重复**
**问题**：`DMWorldView`、`NpcSchedulerWorldView` 等均包含 `map_id`、`map_name`、`map_description` 或其变体，重复定义。  
**修改建议**：
- 抽象 `BaseMapView`，包含地图基本描述字段。
- 各具体视图继承或组合该基类。

**参考代码位置**：
- `agent_map_input.py` 中的多个 WorldView 类。

---

### 7. **`NpcPerformerAgent.apply_side_effects` 作为独立方法增加调用方负担**
**问题**：Engine 需在状态提交成功后手动调用此方法，破坏了原子性。  
**修改建议**：
- 引入 `UnitOfWork` 模式，Agent 输出中携带 `SideEffect` 列表，由 Engine 在事务提交时统一应用。
- 或者，将副作用应用内聚到 `StatePatchRuntime` 中，与状态补丁一同提交。

**参考代码位置**：
- `npc_perform_agent.py` 中的 `apply_side_effects` 方法。
- `engine.py` 中的 `_run_performer_branch` 调用处。

---

### 8. **魔法字符串散落各处**
**问题**：
- `NpcSchedulerAgent` 中硬编码 `DEXTERITY_ATTRIBUTE_KEYS = {"dexterity", "敏捷"}`。
- 一致性维护的阈值字段名在多处使用字符串。

**修改建议**：
- 将游戏术语（属性 ID、状态 ID）移至配置文件或常量模块。
- 例如在 `config.yaml` 中定义 `attributes.dexterity_ids: ["dexterity", "敏捷"]`，运行时加载。

**参考代码位置**：
- `npc_schedul_agent.py` 顶部常量。

---

### 9. **`StateAgentWorldView` 中的可写字段缺乏显式声明机制**
**问题**：当前可写字段似乎是运行时遍历实体属性生成的，但未见明确的白名单机制。  
**修改建议**：
- 在 `CharacterEntity` 等模型中，为字段添加 `writable: bool` 元数据标记。
- 视图构建时，仅收集标记为 `writable=True` 的字段。

**参考代码位置**：
- `agent_map_input.py` 中的 `StateAgentWorldView` 和 `WritableFieldInfo`。

---

### 10. **`NarrativeHistory` 模型未被使用**
**问题**：`agent_narrative_input.py` 定义了 `NarrativeHistory`，但 Engine 和 MergerAgent 均未使用。  
**修改建议**：
- 若该模型用于未来功能，请添加注释说明；否则删除以保持精简。

**参考代码位置**：
- `agent_narrative_input.py` 末尾。

---

## 🟢 低优先级修改（优化细节，非紧急）

### 11. **`service.py` 使用同步 `urllib.request`**
**问题**：在异步 Engine 中通过 `asyncio.to_thread` 调用，虽能工作，但高并发时线程开销大。  
**修改建议**：
- 改用 `httpx.AsyncClient` 实现真正的异步 HTTP 调用，减少线程切换。

**参考代码位置**：
- `service.py` 的 `_default_transport` 和 `_default_stream_transport`。

---

### 12. **日志记录中部分字段使用中文描述**
**问题**：`io_logger` 记录的 `validation_feedback` 等字段有时含中文，不利于国际化日志分析。  
**修改建议**：
- 统一使用英文键值，或确保日志系统支持 UTF-8 全文检索。

**参考代码位置**：
- `input_agent.py` 的 `_build_validation_feedback`。

---

### 13. **`EntityIdGenerator` 在归档 ID 过多时可能循环**
**问题**：当某个名称的实体被大量归档后，生成器会不断递增计数器直到找到可用 ID，虽概率低，但理论上可能死循环。  
**修改建议**：
- 增加最大尝试次数（如 10000），超时后抛出明确异常。

**参考代码位置**：
- `entity_id.py` 的 `generate` 方法。

---

### 14. **`ConsistencyAgent` 的重试逻辑在 Engine 中而非 Agent 内部**
**问题**：`Engine._run_consistency_cycle` 手动实现了重试循环，而其他 Agent 依赖 `LLMServiceBase` 的内置重试。  
**修改建议**：
- 将重试逻辑下沉到 `ConsistencyAgent.run`，利用 `LLMServiceBase.call_llm_json` 的 `retry_budget` 参数。

**参考代码位置**：
- `engine.py` 的 `_run_consistency_cycle` 第 80-120 行。

---

## 📋 修改优先级总结

| 优先级 | 问题编号 | 预估改动量 | 影响范围 |
|--------|----------|------------|----------|
| 🔴 高 | 1. Engine 拆分 | 大 | 全局架构 |
| 🔴 高 | 2. 快照类型化 | 中 | 多处调用点 |
| 🔴 高 | 3. JSON 提取健壮性 | 小 | LLM 调用 |
| 🔴 高 | 4. 因果链归一化 | 小 | EvolutionAgent |
| 🔴 高 | 5. Agent 签名统一 | 中 | DMAgent, Engine |
| 🟡 中 | 6. 视图基类 | 中 | 视图模型 |
| 🟡 中 | 7. 副作用提交模式 | 中 | NpcPerformer |
| 🟡 中 | 8. 魔法字符串常量化 | 小 | 多处 |
| 🟡 中 | 9. 可写字段声明 | 中 | 视图构建 |
| 🟡 中 | 10. 未使用模型清理 | 小 | 模型文件 |
| 🟢 低 | 11. HTTP 异步化 | 中 | 服务层 |
| 🟢 低 | 12. 日志国际化 | 小 | 日志 |
| 🟢 低 | 13. ID 生成器防死循环 | 小 | 实体 ID |
| 🟢 低 | 14. 一致性重试内聚 | 小 | ConsistencyAgent |

建议按优先级依次处理，高优先级问题修复后，系统的可维护性和健壮性将有显著提升。