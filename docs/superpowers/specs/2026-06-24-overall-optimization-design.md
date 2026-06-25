# 整体优化设计

## 背景

本轮优化基于对 Cairn SRC 当前代码、测试、文档、Docker/CI 配置和最近提交的只读勘察。项目当前是 Python/FastAPI + SQLite + Dispatcher + 静态 UI 架构，已有测试覆盖 Server API、调度器、worker task、runtime、prompt contract 和 mock E2E。

用户选择“均衡推进”，并明确要求本轮暂时不考虑安全问题。因此本设计只覆盖稳定性、性能和可维护性优化；不改变 Cookie、凭据、端口暴露、Docker context 敏感文件处理等安全语义。

## 目标

- 修复 dispatcher/report 任务在取消或 heartbeat 丢失时可能留下 intent claim 的问题。
- 补齐 dispatcher 对已删除项目残留容器的清理，并避免并发成功任务过早清除 worker cooldown。
- 强化 worker 输出契约校验，让不完整的 judge 或 finding payload 在 dispatcher 侧尽早失败。
- 优化 Server 常用查询的索引、排序和 intent source 批量加载，减少 N+1 和展示抖动。
- 增强 Server JSON 字段解析容错，避免坏行导致详情或列表 API 500。
- 做低风险维护项：配置/请求模型拒绝未知字段、UI 轮询避免重叠、补 Python CI。

## 非目标

- 不做 Cookie 脱敏、账号数据隐藏或 export 默认语义调整。
- 不清理 worker 镜像指令中的敏感内容。
- 不调整 Docker Compose 端口绑定或 Docker build context 排除策略。
- 不拆分 4000+ 行静态 UI 为模块化前端构建。
- 不引入新运行时依赖。

## 方案

### Dispatcher 稳定性

`report` 任务的 healthcheck cancellation 行为应与 `explore` 保持一致：一旦任务还未进入主执行阶段就被取消，必须 best-effort 释放 intent claim。若 healthcheck 阶段 heartbeat lease 失败，也应释放 claim 并返回 failed，避免 intent 等待超时回收。

调度器已有容器管理能力 `managed_container_names()` 和 `cleanup_orphan()`，但当前只调度 completed/stopped cleanup。本轮在 `_queue_container_cleanups()` 增加 orphan cleanup pass：从当前 project summaries 推导仍合法的动态 project container name，发现不在合法集合内的 `cairn-dispatch-*` 容器后提交异步清理。

worker cooldown 不应被同 worker 的无关成功任务提前清除。`unhealthy` 和 `rejected` 只在被设置后按时间自然过期，`_select_worker()` 在读取时清理过期项。

### Worker Contract

`judge` 契约按文档执行：`verdict`、`score`、`recommended_action`、五项 `checklist`、`blocking_gaps` 和 `non_blocking_gaps` 都必须合法。`explore` payload 中的 `findings` 在 dispatcher 侧完整校验必填字段，避免缺字段 payload 进入 Server API 后才失败。

### Server 数据访问

Server schema 增加常用索引并提供旧库迁移。列表和详情查询统一稳定排序，优先使用 `created_at, id`。`build_intents()` 和 export 使用批量 intent source 加载，避免每个 intent 一次查询。

JSON 字段解析通过小型 helper 统一处理。损坏 JSON 返回默认空对象或数组，保留现有 facts/accounts 的容错风格，避免坏行导致 API 500。

### 维护与质量门禁

Dispatcher config 嵌套模型和主要 Server write request model 统一 `extra="forbid"`。静态 UI 轮询从固定 `setInterval` 改为请求完成后再排下一轮，避免慢请求重叠。新增 Python CI workflow，执行 pytest 和 compileall。

## 测试策略

- 每项行为变更先补失败测试，再写实现。
- Dispatcher/report：`tests/test_worker_tasks.py`。
- Scheduler：`tests/test_scheduler_logic.py`。
- Contracts：`tests/test_contracts_and_drivers.py` 和相关 worker task 测试。
- Server/db：`tests/test_server_api.py`、`tests/test_db_migrations.py`。
- Config/static/CI：`tests/test_config_and_adapters.py`、轻量 static/API 测试。
- 最终运行 `cd cairn && uv run --group dev pytest -s` 和 `python3 -m compileall -q cairn/src/cairn cairn/tests`。

## 风险与回滚

- 强化契约校验可能导致旧 mock 或真实 worker 输出被拒绝；需要同步测试 prompt contract 和 mock 输出。
- `extra="forbid"` 会让拼写错误请求从静默忽略变成 422，这是有意的维护性改进。
- 索引迁移应通过 `CREATE INDEX IF NOT EXISTS` 实现，可安全重复运行。
- UI 轮询改动应保持现有 5 秒间隔和 `loadProject()` 入口，不改变用户可见功能。
