# SessionLock调度互斥

## 技术入口

- 服务模型：`cairn/src/cairn/server/models.py`
- 数据库迁移：`cairn/src/cairn/server/db.py`
- 项目 API：`cairn/src/cairn/server/routers/projects.py`
- Intent API：`cairn/src/cairn/server/routers/intents.py`
- Dispatcher 调度：`cairn/src/cairn/dispatcher/scheduler/loop.py`
- Reason 执行与输出校验：`cairn/src/cairn/dispatcher/tasks/reason.py`、`cairn/src/cairn/dispatcher/contracts.py`
- 前端入口：`cairn/src/cairn/server/static/index.html`

## 数据流

1. Project 持久化 `session_lock_enabled`，默认 `true`，列表、详情和导出都返回该字段。
2. Intent 持久化 `session_lock`，默认 `false`，详情和导出都返回该字段。
3. reason prompt 要求每个 intent 输出 `session_lock`；`validate_reason_payload` 拒绝缺失或非布尔字段。
4. `run_reason_task` 将 `session_lock` 传给 `CairnClient.create_intent`，再写入 Server。
5. Scheduler 派发 explore 时读取 intent 的 `session_lock`，并写入本地 `RunningTask.session_lock`。

## 调度约定

- `session_lock` 只约束 explore 任务，不改变 reason/bootstrap 的 lease 规则。
- `session_lock_wait_queues` 是 dispatcher 本地 `project_id -> deque[intent_id]` 队列，FIFO 排序，同一 intent 不重复入队。
- 项目开启 session lock 且已有同项目 `RunningTask.session_lock=true` 时，新的 locked open intent 入队，不派发。
- 项目没有 locked RunningTask 时，等待队列里仍 open、unclaimed、locked、非 bootstrap 的 intent 优先派发。
- 项目关闭 session lock 时清理并忽略等待队列，恢复旧并发逻辑。
- 项目 stopped、completed 或从列表消失时清理对应队列。

## 验证命令

- 核心无 FastAPI 子集：`PYTHONPATH=src python3 -m pytest --capture=no tests/test_scheduler_logic.py tests/test_contracts_and_drivers.py tests/test_worker_tasks.py tests/test_protocol_and_startup.py tests/test_db_migrations.py`
- 完整测试需要 FastAPI/TestClient；如果系统 Python 缺少依赖，需要先安装项目依赖或使用项目运行环境。
