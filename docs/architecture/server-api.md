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
| projects | `server/routers/projects.py` | 项目、状态、snapshot、fork、ephemeral jobs、recon judgement。 |
| hints | `server/routers/hints.py` | 创建 hint，并在 reason 运行中设置 `reason_pending`。 |
| intents | `server/routers/intents.py` | intent 创建、claim、release、conclude、finding/report 写入。 |
| export | `server/routers/export.py` | YAML 和 timeline export。 |

## Project APIs

| Endpoint | 语义 |
| --- | --- |
| `GET /projects` | 返回 project summaries。 |
| `POST /projects` | 创建 recon 或 vuln project。 |
| `GET /projects/{project_id}` | 返回 project detail，包括 facts、intents、hints、findings、accounts。 |
| `DELETE /projects/{project_id}` | 删除项目；有 child vuln 的 recon 返回 `409`。 |
| `PUT /projects/{project_id}/title` | 修改标题。 |
| `PUT /projects/{project_id}/status` | 设置 `active`、`stopped` 或 `completed`。 |
| `POST /projects/{project_id}/complete` | 兼容路由，返回 `410 Gone`。 |
| `POST /projects/{project_id}/reopen` | 兼容路由，返回 `410 Gone`。 |

创建 recon 的规则：

- `project_kind` 缺省为 `recon`。
- 不能传 legacy `mode`、`bootstrap_enabled`、`goal`。
- 不能有 parent fields。
- 不能显式选择 `auth_mode="anonymous"` 或 `auth_mode="authenticated"`。
- Server 固定写入 `auth_mode="dual"`。
- 必须提供至少一个 Cookie session。

创建 vuln 的规则：

- 必须提供 `parent_project_id` 和 `parent_snapshot_id`。
- parent 必须是 recon。
- snapshot 必须属于 parent。
- `auth_mode` 只能是 `anonymous` 或 `authenticated`。
- authenticated vuln 必须提供 accounts。
- anonymous vuln 不能提供 accounts。

## Reason Lease APIs

Reason lease 是项目级协调状态，不是 graph data。

| Endpoint | 语义 |
| --- | --- |
| `POST /projects/{project_id}/reason/claim` | claim project-level reason lease。 |
| `POST /projects/{project_id}/reason/heartbeat` | 刷新 reason lease。 |
| `POST /projects/{project_id}/reason/release` | 释放 reason lease。 |

`reason_pending` 是合并触发信号：reason 运行期间如有新 fact 或 hint 写入，Server 会置为 `true`。当前 reason release 后，Dispatcher 会立即再启动一轮 reason；新的 claim 成功时清除 pending。

## Recon APIs

| Endpoint | 语义 |
| --- | --- |
| `POST /projects/{project_id}/recon/reason-round` | 记录 recon reason round 和 stable round。 |
| `POST /projects/{project_id}/recon/explore-round` | 记录 recon explore round。 |
| `POST /projects/{project_id}/recon/judgements` | 创建 judge ephemeral job。 |
| `GET /projects/{project_id}/recon/judgements` | 返回轻量 judgement result 列表。 |
| `GET /projects/{project_id}/recon/judgements/{job_id}` | 返回指定 judgement，包含 input snapshot YAML。 |

Recon reason round 到达 `recon_max_reason_rounds` 时，项目自动变为 `stopped` 并清理 reason lease。

## Snapshot 与 Fork APIs

| Endpoint | 语义 |
| --- | --- |
| `POST /projects/{project_id}/snapshots` | 创建 recon snapshot。 |
| `GET /projects/{project_id}/snapshots` | 列出 snapshots。 |
| `POST /projects/{project_id}/fork-vuln/seed-jobs` | 创建 AI seeded fork job。 |
| `GET /projects/{project_id}/fork-vuln/seed-jobs` | 列出 fork seed job results。 |
| `POST /projects/{project_id}/fork-vuln` | Legacy/manual copy fork。 |
| `GET /projects/{project_id}/children` | 列出 child vuln projects。 |

默认 UI 使用 AI seeded fork：Server 创建 `job_type="fork_seed"`，Dispatcher 完成后调用 `finish-fork-seed`，Server 原子创建 child vuln project。

## Ephemeral Job APIs

| Endpoint | 语义 |
| --- | --- |
| `GET /ephemeral-jobs/queued?job_type=judge` | 拉取 queued judge jobs。 |
| `GET /ephemeral-jobs/queued?job_type=fork_seed` | 拉取 queued fork seed jobs。 |
| `POST /ephemeral-jobs/{job_id}/claim` | claim queued job。 |
| `POST /ephemeral-jobs/{job_id}/finish` | 完成 judge job。 |
| `POST /ephemeral-jobs/{job_id}/finish-fork-seed` | 完成 fork seed job，并创建 child vuln。 |
| `POST /ephemeral-jobs/{job_id}/fail` | 写入 job failure。 |

Ephemeral jobs 不直接写 graph。Judge 只更新 readiness judgement；fork seed 成功后由 Server 创建 child project。

## Intent 与 Finding APIs

| Endpoint | 语义 |
| --- | --- |
| `POST /projects/{project_id}/intents` | 创建 explore/report intent。 |
| `POST /projects/{project_id}/intents/{intent_id}/heartbeat` | claim 或刷新 intent worker。 |
| `POST /projects/{project_id}/intents/{intent_id}/release` | 释放 open intent。 |
| `POST /projects/{project_id}/intents/{intent_id}/conclude` | 写入 fact，并可在 vuln 中写 findings。 |
| `POST /projects/{project_id}/intents/{intent_id}/report` | 写入 finding report draft。 |

服务端重复保护使用窄范围规则：同一 project、相同 `from` 集合、规范化后相同 `description`、相同 `auth_scope` 时返回 `409`。

## Export APIs

| Endpoint | 语义 |
| --- | --- |
| `GET /projects/{project_id}/export?format=yaml` | 导出项目 YAML。 |
| `GET /projects/{project_id}/export?format=timeline` | 导出 timeline。 |

YAML export 会包含 project kind、auth mode、parent/snapshot、recon counters、facts、intents、hints、findings、reports 和 accounts。Accounts 可能包含明文 Cookie，不应公开。
