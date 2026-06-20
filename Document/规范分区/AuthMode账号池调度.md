# AuthMode账号池调度

## 技术入口

- 数据模型：`cairn/src/cairn/server/models.py`
- 数据库迁移：`cairn/src/cairn/server/db.py`
- 项目 API：`cairn/src/cairn/server/routers/projects.py`
- 导出 API：`cairn/src/cairn/server/routers/export.py`
- 调度循环：`cairn/src/cairn/dispatcher/scheduler/loop.py`
- Explore prompt 注入：`cairn/src/cairn/dispatcher/tasks/explore.py`
- SRC prompt：`cairn/src/cairn/dispatcher/prompts/default/src/`

## 数据契约

- `projects.auth_mode TEXT NOT NULL DEFAULT 'anonymous'`。
- `project_accounts(id, project_id, label, username, password)` 保存项目账号。
- `projects.session_lock_enabled` 和 `intents.session_lock` 已物理移除；迁移通过重建旧表删除遗留列。
- `CreateProjectRequest` 只允许 SRC 项目使用 `authenticated`，且必须提供至少一个账号。
- `Intent` 和 `CreateIntentRequest` 不包含 auth/session 字段。

## 调度规则

- Dispatcher 维护本地 `account_leases: project_id -> account_id -> intent_id`。
- Dispatcher 维护本地 `authenticated_wait_queues: project_id -> deque[intent_id]`，按 intent 创建时间 FIFO 入队。
- authenticated SRC explore 派发前先领取空闲账号；领取失败则不 claim worker，不提交任务，只入队。
- `_reap_futures()` 对成功、失败、取消、异常完成的任务统一释放账号租约。
- 每轮调度会先清理非 active/authenticated 项目的队列和租约，再优先检查 waiting queue。
- 实际并发上限为 `min(账号数, max_project_workers, max_workers, worker 可用量)`。

## Prompt约定

- SRC reason 根据 `project.auth_mode` 规划方向：anonymous 只规划未登录攻击面，authenticated 只规划登录态攻击面。
- authenticated explore prompt 包含 `account_id`、`label`、`username`、`password` 和 `isolated_session_dir`。
- 隔离目录固定为 `/home/kali/workspace/auth/{project_id}/{account_id}`。

## 验证命令

- 语法检查：`python3 -m compileall -q cairn/src/cairn cairn/tests`
- 完整测试：`PYTHONPATH=src pytest -q tests`
