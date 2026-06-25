# Server 与 API

Cairn Server 使用 FastAPI 提供 UI、API 和 SQLite 持久化。Server 不运行模型推理；Dispatcher 通过 API claim、heartbeat、conclude 和 writeback。

## 入口

`cairn/src/cairn/server/app.py` 创建 FastAPI app：

- lifespan 中调用 `db.configure(db.DEFAULT_DB)`。
- 注册 `settings`、`projects`、`hints`、`intents`、`export` routers。
- `/` 返回静态 UI `index.html`。
- `/static` 挂载本地静态资源。

CLI 入口在 `cairn/src/cairn/cli.py`：

```bash
uv run --project cairn cairn serve --host 127.0.0.1 --port 8000
```

常用参数：

- `--host`，默认 `127.0.0.1`。
- `--port`，默认 `8000`。
- `--db-path`，默认 `~/.local/share/cairn/cairn.db`。
- `--log-level`，默认 `info`。
- `--access-log / --no-access-log`。

## Router 分组

| Router | 文件 | 职责 |
| --- | --- | --- |
| settings | `server/routers/settings.py` | 读取和更新 server timeout settings。 |
| projects | `server/routers/projects.py` | 项目、状态、legacy snapshot/fork/job 迁移接口。 |
| hints | `server/routers/hints.py` | 创建 hint，并在 reason 运行中设置 `reason_pending`。 |
| intents | `server/routers/intents.py` | intent 创建、claim、release、conclude、finding/report 写入。 |
| export | `server/routers/export.py` | YAML 和 timeline export。 |

## Project APIs

| Endpoint | 语义 |
| --- | --- |
| `GET /projects` | 返回 project summaries。 |
| `POST /projects` | 创建 vuln project。 |
| `GET /projects/{project_id}` | 返回 project detail，包括 facts、intents、hints、findings、accounts。 |
| `DELETE /projects/{project_id}` | 删除项目；legacy child 关系仍按外键/兼容规则处理。 |
| `PUT /projects/{project_id}/title` | 修改标题。 |
| `PUT /projects/{project_id}/status` | 设置 `active`、`stopped` 或 `completed`。 |
| `POST /projects/{project_id}/complete` | 兼容路由，返回 `410 Gone`。 |
| `POST /projects/{project_id}/reopen` | 兼容路由，返回 `410 Gone`。 |

创建 vuln project 的规则：

- `project_kind` 固定为 `vuln`，缺省也是 `vuln`。
- 必须提供 `title` 和 `origin`。
- 不能传 legacy `mode`、`bootstrap_enabled`、`goal`。
- `auth_mode` 缺省规则：有 accounts 时为 `dual`，无 accounts 时为 `anonymous`。
- `auth_mode="anonymous"` 不能提供 accounts。
- `auth_mode="authenticated"` 或 `auth_mode="dual"` 必须提供至少一个 Cookie session。
- `parent_project_id` 与 `parent_snapshot_id` 只作为 legacy/migration 关联字段保留；新主流程不要求从 snapshot 创建 child project。

写请求校验规则：

- 创建/更新类请求使用严格 Pydantic model，未知字段返回 `422`。
- 严格校验包含嵌套结构，例如 project account cookie 只接受 `name` 和 `value`。
- Legacy job API 读取损坏 JSON 时返回受控 `422`，不暴露未捕获解析异常。

## Reason Lease APIs

Reason lease 是按 task mode 分离的项目协调状态，不是 graph data。

| Endpoint | 语义 |
| --- | --- |
| `POST /projects/{project_id}/reason/claim` | 使用 `worker`、`trigger`、`task_mode` claim per-mode reason lease。 |
| `POST /projects/{project_id}/reason/heartbeat` | 使用 `worker`、`task_mode` 刷新 reason lease。 |
| `POST /projects/{project_id}/reason/release` | 使用 `worker`、`task_mode` 释放 reason lease。 |

`task_mode` 合法值为 `collection`、`validation`、`report`；当前 dispatcher 只为 `collection` 与 `validation` 调度 reason。Collection 和 validation reason 可在同一项目内独立持有 lease，不会通过单个项目级 lease 互相阻塞。

`reason_pending` 是合并触发信号：任一 reason 运行期间如有新 fact 或 hint 写入，Server 会置为 `true`。新的 reason claim 成功时清除 pending。

## Collection Round APIs

| Endpoint | 语义 |
| --- | --- |
| `POST /projects/{project_id}/recon/reason-round` | 兼容路径；记录 collection reason round 和 stable round。 |
| `POST /projects/{project_id}/recon/explore-round` | 兼容路径；记录 collection explore round。 |

Collection/validation 收敛不会自动停止项目；项目停止或完成仍由显式状态变更控制，并会清理 reason leases。

## Legacy Snapshot 与 Fork APIs

以下接口只服务迁移、历史数据读取或旧数据兼容，不属于新项目的 active workflow。新流程通过 collection facts 和 validation seed intents 在同一 vuln project 内进入 validation。

| Endpoint | 语义 |
| --- | --- |
| `POST /projects/{project_id}/snapshots` | Legacy snapshot creation path。 |
| `GET /projects/{project_id}/snapshots` | 列出 legacy snapshots。 |
| `POST /projects/{project_id}/fork-vuln/seed-jobs` | Legacy fork seed job path。 |
| `GET /projects/{project_id}/fork-vuln/seed-jobs` | 列出 legacy fork seed job results。 |
| `POST /projects/{project_id}/fork-vuln` | Legacy/manual copy fork。 |
| `GET /projects/{project_id}/children` | 列出 legacy child projects。 |

Dispatcher 对新的 fork_seed jobs 只写 retired failure；不要把这些接口作为新流程入口。

## Legacy Ephemeral Job APIs

Judge 和 fork_seed jobs 已从 active workflow 退休。接口保留用于旧 queued jobs、迁移检查和历史结果读取。

| Endpoint | 语义 |
| --- | --- |
| `GET /ephemeral-jobs/queued?job_type=judge` | 拉取 legacy queued judge jobs。 |
| `GET /ephemeral-jobs/queued?job_type=fork_seed` | 拉取 legacy queued fork seed jobs。 |
| `POST /ephemeral-jobs/{job_id}/claim` | claim queued job。 |
| `POST /ephemeral-jobs/{job_id}/finish` | Legacy finish path。 |
| `POST /ephemeral-jobs/{job_id}/finish-fork-seed` | Legacy fork seed finish path。 |
| `POST /ephemeral-jobs/{job_id}/fail` | 写入 job failure。 |

Retired job handlers 不直接写 graph；Dispatcher 会把旧 queued jobs 标记失败，避免卡住队列。

## Intent 与 Finding APIs

| Endpoint | 语义 |
| --- | --- |
| `POST /projects/{project_id}/intents` | 创建 explore/report intent。 |
| `POST /projects/{project_id}/intents/{intent_id}/heartbeat` | claim 或刷新 intent worker。 |
| `POST /projects/{project_id}/intents/{intent_id}/release` | 释放 open intent。 |
| `POST /projects/{project_id}/intents/{intent_id}/conclude` | 写入 fact，并可在 vuln 中写 findings。 |
| `POST /projects/{project_id}/intents/{intent_id}/report` | 写入 finding report draft。 |

`task_mode="collection"` 的 `/conclude` 请求只能写 facts，包含 `findings` 时返回 `400`。`task_mode="validation"` 的 explore intent 可以通过 `/conclude` 写 findings。`report` intent 必须通过 `/report` endpoint 完成，不能通过普通 `/conclude` 写成 fact。

服务端重复保护使用窄范围规则：同一 project、相同 `from` 集合、规范化后相同 `description`、相同 `auth_scope` 时返回 `409`。

## Export APIs

| Endpoint | 语义 |
| --- | --- |
| `GET /projects/{project_id}/export?format=yaml` | 导出项目 YAML。 |
| `GET /projects/{project_id}/export?format=timeline` | 导出 timeline。 |

YAML export 会包含 project kind、auth mode、legacy parent/snapshot fields、collection counters、facts、intents、hints、findings、reports 和 accounts。Accounts 可能包含明文 Cookie，不应公开。
