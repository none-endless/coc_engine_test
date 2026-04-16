
---

### Phase 0：基础设施与核心模型搭建 (Foundation)

**目标**：确立项目骨架、核心数据模型、配置管理和最基础的存储/快照能力，为后续所有 Agent 开发提供不可变基础。

#### 需要做什么
1.  **项目初始化与配置管理**：
    -   搭建 Python 项目结构（`src/`、`tests/`、`config/`）。
    -   实现配置加载器，严格遵循 **优先级：命令行 > 环境变量 > 配置文件 > 默认值**。
    -   编写 `config.schema.yaml` 与 `config.yaml` 模板。
2.  **数据模型定义**：
    -   使用 Pydantic 或 Dataclasses 严格定义 **世界模型** (`Map`, `Item`, `Character`) 和 **记忆/目标模型** (`Memory`, `Goal`)。
    -   实现 **实体 ID 系统规范**（`[实体类型]-[有意义名称]-[唯一后缀]`）的校验逻辑，提供全局 ID 生成器与注册表。
3.  **世界状态容器与快照**：
    -   实现 `WorldState` 单例容器，维护 `char_index` / `item_index` 的自动派生逻辑。
    -   实现只读快照生成器 (`get_snapshot`)，确保读取不阻塞。
4.  **回合事务与日志基底**：
    -   定义 `TurnEnvelope` 结构（`turn_id`, `trace_id`）。
    -   实现基础的事件日志记录器（用于 `memory.log` 和 `shortLog`）。

#### 验收需求
-   配置文件修改后系统行为能正确覆盖。
-   尝试创建非法格式 ID（如 `char#bedroom`）会被拒绝。
-   修改 `Character.location` 后，对应的 `Map.char_index` 自动更新且无法通过 Agent 代码直接篡改索引。

#### 生产待检查物料
-   [ ] `config.schema.yaml` 文件。
-   [ ] 实体 ID 校验工具类单元测试通过报告。
-   [ ] 世界状态快照读写分离逻辑验证脚本。

