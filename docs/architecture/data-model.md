# 数据模型与持久化

Server 使用 Pydantic 定义请求/响应模型，使用 SQLite 持久化项目 graph 和调度状态。

## 默认数据库

默认路径：

```text
~/.local/share/cairn/cairn.db
```

`db.configure(path)` 会创建父目录、初始化 schema、执行迁移，并设置模块级 `_db_path`。配置后再次调用不会覆盖已有路径。

每次 `get_conn()`：

- 创建 SQLite 连接。
- 设置 `row_factory = sqlite3.Row`。
- 开启 WAL。
- 开启 foreign keys。
- 正常退出提交，异常时回滚。

## 主要枚举

| 类型 | 取值 |
| --- | --- |
| `ProjectKind` | `vuln` |
| `AuthMode` | `anonymous | authenticated | dual` |
| `ProjectStatus` | `active | stopped | completed` |
| `JudgeStatus` | `not_judged | ready | not_ready | blocked`，legacy only |
| `IntentKind` | `explore | report` |
| `AuthScope` | `anonymous | authenticated` |
| `FactType` | `observation | feature_surface` |
| `EphemeralJobStatus` | `queued | running | succeeded | failed | expired` |
| `FindingNextAction` | `triage | follow_up | report | close` |
| `ReportStatus` | `not_started | queued | drafted | submitted | closed` |

## Project

Project 保存项目元信息和调度摘要：

- `id`：`proj_###`。
- `title`。
- `status`。
- `project_kind`。
- `auth_mode`。
- `parent_project_id`、`parent_snapshot_id`：legacy/migration 关联字段。
- `reasons`：按 `task_mode` 返回的 reason lease 摘要。
- `reason_pending`。
- collection round counters。
- judge status 摘要：legacy field，新 workflow 不依赖。

状态规则：

- `active`：允许 graph write。
- `stopped`：拒绝 graph write，可恢复为 `active`。
- `completed`：人工归档，不能恢复。

Project ID 由当前 `projects.id` 中最大 `proj_###` 后缀加 1 生成。删除当前最大编号后，下一个项目可能复用该编号；删除中间编号不填补空洞。

## Fact

每个项目从 `origin` fact 开始。普通 fact 使用项目内 scoped ID，例如 `f001`。

字段：

- `id`
- `description`
- `fact_type`
- `title`
- `summary`
- `details`

`feature_surface` 用于 collection 功能理解事实或 validation seed fact。常见 `details` 字段：

- `page_url`
- `visible_features`
- `user_actions`
- `routes`
- `apis`
- `auth_scope`
- `evidence_refs`
- `screenshot_refs`
- `dom_refs`
- `feature_summary`
- `vuln_validation_focus`
- `known_constraints`

旧 fact 通过迁移补齐 `fact_type="observation"`、`title=NULL`、`summary=NULL`、`details_json="{}"`。

## Intent

Intent 表示一个待执行方向。

字段：

- `id`
- `from`
- `to`
- `description`
- `creator`
- `worker`
- `last_heartbeat_at`
- `created_at`
- `concluded_at`
- `intent_kind`
- `task_mode`
- `finding_id`
- `auth_scope`

规则：

- `from` 必须引用已有 facts。
- `to` 在 intent open 时为 null，conclude 后指向产生的 fact ID。
- `intent_kind="report"` 必须绑定 `finding_id`，且 `auth_scope` 为 null。
- `task_mode` 合法值为 `collection`、`validation`、`report`；report intent 固定使用 `task_mode="report"`。
- Collection explore intent 必须显式提供 `auth_scope`；`auth_scope="authenticated"` 需要项目存在 accounts。
- Validation explore intent 在非 `dual` vuln 项目中必须匹配 project `auth_mode`。
- Report intent 不走 explore prompt。

## Hint

Hint 是 graph-adjacent strategy note，不是 fact。项目处于 `active`、`stopped` 或 `completed` 时都允许追加 hint。若任一 task mode 的 reason lease 正在运行，新增 hint 会设置 `reason_pending=true`。

## Reason Lease

`project_reason_leases` 保存 reason task 的短租约：

- `project_id`
- `task_mode`
- `worker`
- `trigger`
- `started_at`
- `last_heartbeat_at`

主键为 `(project_id, task_mode)`。Collection 和 validation reason 可在同一项目内独立 claim、heartbeat 和 release，不再共享单个项目级 reason lease。

## Account 与 Cookie Session

`project_accounts` 保存项目 Cookie session：

- `id`：项目内 scoped ID，例如 `a001`。
- `label`。
- `cookies_json`：`[{"name": "...", "value": "..."}]`。

同一个 account 内 cookie name 必须唯一。`auth_mode="authenticated"` 和 `auth_mode="dual"` 项目必须有 accounts，`auth_mode="anonymous"` 项目不能有 accounts。

## Finding 与 Report

Finding 只属于 vuln 项目。

主要字段：

- `title`
- `vulnerability_type`
- `severity`
- `target`
- `location`
- `impact`
- `evidence`
- `reproduction`
- `remediation`
- `status`
- `research_value`
- `next_action`
- `followup_reason`
- `followup_intent_description`
- `followup_intent_id`
- `report_status`
- `report_intent_id`
- `triaged_at`

`next_action="follow_up"` 要求 `followup_intent_description`，Server 会自动创建 follow-up explore intent。

`next_action="report"` 会自动创建 report intent，并把 `report_status` 置为 `queued`。

Report task 成功后写入 `finding_reports`，并把 finding `report_status` 改为 `drafted`。

## Legacy Snapshot

Snapshot 表保留用于旧 `recon -> snapshot -> fork vuln` 数据读取和迁移，不是 active workflow 的入口。Snapshot 保存：

- `summary_yaml`
- `selected_fact_ids_json`
- `stats_json`
- `created_at`

新流程使用 collection facts 和 validation seed intents 进入 validation，不创建 recon snapshot。`selected_fact_ids` 保留给 legacy/manual copy fork。

## Legacy Ephemeral Job

Ephemeral jobs 是旧 judge/fork_seed 临时任务，不直接写 graph data。当前 Dispatcher 会把 legacy queued judge/fork_seed job 标记为 retired failure；新 workflow 不创建这些 job。

遗留类型：

- `judge`
- `fork_seed`

字段包括：

- `id`
- `project_id`
- `job_type`
- `status`
- `input_snapshot_yaml`
- `input_json`
- `result_json`
- `error`
- `worker`
- timestamps

Judge job ID 使用 `judge_###`，由当前 `ephemeral_jobs.id` 最大后缀加 1 生成；仅用于历史数据。

## SQLite 表

| 表 | 内容 |
| --- | --- |
| `settings` | server timeout settings。 |
| `projects` | 项目元信息、状态、collection counters、legacy judge 摘要。 |
| `facts` | 项目 facts。 |
| `intents` | intents 主表。 |
| `intent_sources` | intent -> source facts 多对多关系。 |
| `hints` | 用户 hints。 |
| `project_accounts` | Cookie session 池。 |
| `findings` | vuln findings。 |
| `project_snapshots` | legacy snapshots。 |
| `ephemeral_jobs` | legacy judge 和 fork_seed jobs。 |
| `project_reason_leases` | 按 `(project_id, task_mode)` 保存 reason lease。 |
| `finding_reports` | report task 输出。 |
| `counters` | legacy/global counter 支撑。 |
| `scoped_counters` | 项目内 fact/intent/hint/account/snapshot/report ID 计数。 |

## 主要索引

Schema 初始化会创建项目详情、队列和导出常用路径的索引：

- `idx_facts_project`
- `idx_intents_project_open_worker`
- `idx_intent_sources_project_intent`
- `idx_hints_project_created`
- `idx_findings_project_created`
- `idx_project_accounts_project`
- `idx_project_snapshots_project_created`
- `idx_ephemeral_jobs_queue`
- `idx_finding_reports_project_created`

## Legacy 迁移

启动时迁移会处理旧 schema：

1. 如果存在 legacy `projects.mode`，先检查 `mode="standard"`。
2. 存在 legacy standard project 时抛出 `RuntimeError`，要求先导出或删除。
3. legacy `mode="src"` 和 legacy `project_kind="recon"` 都迁移为 `project_kind="vuln"`。
4. parent/snapshot 字段只用于旧数据读取；新建 vuln 不需要 parent/snapshot。
5. 移除旧 `session_lock_enabled` 和 `session_lock` 列。
6. 保留 `project_accounts`。
7. legacy recon projects 回填为 `auth_mode="dual"` 后迁移为 vuln project。
8. legacy explore intent 从 project auth mode 回填 `auth_scope`，默认 anonymous。
9. 删除 legacy `goal` facts 和对应 intent source。
10. 为 facts 补齐结构化字段。
