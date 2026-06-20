# AuthMode 账号池调度

## 技术入口

- 数据模型：`cairn/src/cairn/server/models.py`
- 数据库迁移：`cairn/src/cairn/server/db.py`
- 项目 API：`cairn/src/cairn/server/routers/projects.py`
- 导出 API：`cairn/src/cairn/server/routers/export.py`
- 调度循环：`cairn/src/cairn/dispatcher/scheduler/loop.py`
- Explore prompt 注入：`cairn/src/cairn/dispatcher/tasks/explore.py`
- 默认 prompt：`cairn/src/cairn/dispatcher/prompts/default/recon/`、`cairn/src/cairn/dispatcher/prompts/default/vuln/`

## 数据契约

- `projects.auth_mode TEXT NOT NULL DEFAULT 'anonymous'`，取值为 `anonymous|authenticated|dual`；`dual` 只用于 recon。
- `intents.auth_scope TEXT`，explore intent 取值为 `anonymous|authenticated`，report intent 为 null。
- `project_accounts(id, project_id, label, username, password)` 保存项目账号。
- `CreateProjectRequest` 在 `project_kind="recon"` 时固定写入 `auth_mode="dual"`，拒绝显式 anonymous/authenticated，且要求至少一个 account。
- `CreateProjectRequest` 和 `ForkVulnRequest` 在 vuln `auth_mode="authenticated"` 时要求至少一个 account。
- vuln `auth_mode!="authenticated"` 时提交 accounts 返回 422。
- `Intent` 和 `CreateIntentRequest` 包含 `auth_scope`；recon reason 必须显式写入，vuln intent 缺省继承项目 `auth_mode`。
- `projects.session_lock_enabled` 和 `intents.session_lock` 不属于新 schema；迁移兼容代码只用于删除旧列和读取旧库。

## 调度规则

- Dispatcher 维护本地 `account_leases: project_id -> account_id -> intent_id`。
- Dispatcher 维护本地 `authenticated_wait_queues: project_id -> deque[intent_id]`，按 intent 创建时间 FIFO 入队。
- `auth_scope="authenticated"` explore 派发前先领取空闲账号；领取失败则不 claim worker，不提交任务，只入队。
- `auth_scope="anonymous"` explore 明确不领取账号，不进入账号等待队列。
- report intent 不领取账号；judge 是 ephemeral job，也不领取账号。
- `_reap_futures()` 对成功、失败、取消、异常完成的任务统一释放账号租约。
- 每轮调度会先清理非 active 或 anonymous 项目的队列和租约，再优先检查 waiting queue。
- 实际 authenticated explore 并发上限为 `min(账号数, max_project_workers, max_workers, worker 可用量)`。

## Prompt 约定

- recon reason 根据图约束持续维护 anonymous/authenticated 两条信息收集线路；初始 `origin` 图必须创建两条 baseline intent。
- vuln reason 根据项目 `auth_mode` 规划漏洞挖掘范围，服务端可按项目 auth mode 给 intent 补默认 `auth_scope`。
- authenticated explore prompt 包含 `account_id`、`label`、`username`、`password` 和 `isolated_session_dir`；anonymous explore prompt 明确禁止登录和使用账号。
- 隔离目录固定为 `/home/kali/workspace/auth/{project_id}/{account_id}`。

## 验证命令

- 语法检查：`python3 -m compileall -q cairn/src/cairn cairn/tests`
- 完整测试：`cd cairn && pytest -q tests`
