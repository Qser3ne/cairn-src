# Cairn SRC-only Server 协议规范

## 范围

Cairn Server 是 projects、facts、intents、hints、findings、snapshots、reports、account pools 和 ephemeral jobs 的事实来源。Server 不运行模型推理，只负责保持 graph 状态一致，并暴露 UI 与 Dispatcher 使用的 API。

当前协议是 SRC-only：

- `project_kind="recon"` 用于收集攻击面知识。
- `project_kind="vuln"` 用于基于 recon snapshot 验证漏洞。
- 新项目默认是 recon。
- 新 vuln 项目必须引用 parent recon project 和 snapshot。
- Standard mode 与 bootstrap auto-completion 已移除。
- `/projects/{id}/complete` 与 `/projects/{id}/reopen` 返回 `410 Gone`。
- `completed` 是人工归档状态，归档后不可恢复为 `active`。

## 核心概念

### Project

Project fields：

```text
id
title
status                 # active | stopped | completed
project_kind           # recon | vuln
auth_mode              # anonymous | authenticated | dual
parent_project_id      # only for vuln; legacy migrated vuln may be null
parent_snapshot_id     # only for vuln; legacy migrated vuln may be null
created_at
reason                 # project-level reason lease, not graph data
recon_max_reason_rounds
recon_reason_rounds
recon_explore_rounds
recon_stable_rounds
judge_status           # not_judged | ready | not_ready | blocked
judged_at
```

Status 规则：

- `active` 允许 graph write operations。
- `stopped` 拒绝 graph write operations，清理 open intent workers 和 reason lease，可恢复为 `active`。
- `completed` 拒绝 graph write operations，不能恢复为 `active` 或 `stopped`。

### Fact

Facts 是不可变 graph nodes。每个项目从以下 fact 开始：

- `origin`

普通 facts 使用 scoped IDs，例如 `f001`。

### Intent

Intent fields：

```text
id
from                  # source fact IDs, at least one
to                    # produced fact ID; null while open
description
creator
worker                # current claimant while open; final producer after conclude
last_heartbeat_at
created_at
concluded_at
intent_kind           # explore | report
finding_id            # required for report intents
auth_scope            # anonymous | authenticated for explore intents; null for report intents
```

服务端重复保护刻意保持窄范围：同一 project、规范化后相同 `description`、相同 `auth_scope`、相同 `from` 集合时返回 `409`。

### Hint

Hints 是 graph-adjacent strategy notes。项目处于 `active`、`stopped` 或 `completed` 时都可以追加 hints。

### Finding

Findings 属于 vuln exploration results，包含 lifecycle fields：

```text
research_value        # unknown | high | medium | low | none
next_action           # triage | follow_up | report | close
followup_reason
followup_intent_description
followup_intent_id
report_status         # not_started | queued | drafted | submitted | closed
report_intent_id
triaged_at
```

`next_action="follow_up"` 要求 `followup_intent_description`。Conclude 会自动创建 follow-up explore intent。`next_action="report"` 会创建 report intent，并设置 `report_status="queued"`。

### Snapshot

Recon snapshots 存储 YAML export、selected fact IDs 和 stats。它们是新 vuln 项目的唯一支持来源。

### Ephemeral Job

Ephemeral jobs 是不写 graph data 的临时任务。当前 judge 使用这条路径。

## 数据库与迁移

新 schema 不再声明 `projects.mode` 或 `projects.bootstrap_enabled`。

启动迁移顺序：

1. 如果 legacy `projects.mode` 存在，检测 `mode='standard'`。
2. 如果存在 legacy standard project，抛出 `RuntimeError`，要求先导出或删除。
3. 如果只存在 legacy `mode='src'` projects，将它们迁移为 `project_kind='vuln'`。
4. Legacy migrated vuln projects 可以没有 parent/snapshot；新 vuln projects 不允许缺失。
5. 表重建迁移中移除旧 `session_lock_enabled` 和 `session_lock` 列。
6. 保留 `project_accounts`。
7. 将 legacy recon projects 回填为 `auth_mode='dual'`。
8. 从 project auth mode 回填 legacy explore intent 的 `auth_scope`，默认使用 `anonymous`。

## Project APIs

### GET /projects

返回 project summaries，包括 graph counts、kind/auth 字段、recon counters 和 judge status。

### POST /projects

创建项目。Body：

```json
{
  "title": "Recon target",
  "origin": "https://target.example",
  "project_kind": "recon",
  "recon_max_reason_rounds": 8,
  "hints": [{"content": "stay in scope", "creator": "human"}],
  "accounts": [{"label": "alice", "username": "alice", "password": "secret"}]
}
```

规则：

- `project_kind` 默认是 `recon`。
- `mode`、`bootstrap_enabled` 和 legacy `goal` 属于 forbidden extra fields，会返回 422。
- Recon projects 不能有 parent fields，不能显式选择 `auth_mode`，并固定存储为 `auth_mode="dual"`。
- 新 recon projects 至少需要一个 account。
- 新 vuln projects 需要 `parent_project_id` 和 `parent_snapshot_id`。
- Vuln parent 必须是 recon。
- Vuln snapshot 必须属于该 parent。
- Authenticated vuln projects 至少需要一个 account。
- Anonymous vuln projects 不能包含 accounts。

### GET /projects/{project_id}

返回 project meta、facts、intents、hints、findings 和 accounts。

### DELETE /projects/{project_id}

删除项目。如果删除的 recon project 存在 child vuln projects，返回 409。

### PUT /projects/{project_id}/title

重命名项目，不改变 graph data。

### PUT /projects/{project_id}/status

Body：

```json
{"status": "stopped"}
```

允许的 status 为 `active`、`stopped`、`completed`。

规则：

- `active <-> stopped` 允许互相切换。
- 可以设置为 `completed`，表示人工归档。
- 一旦进入 `completed`，任何非 completed status 都返回 409。
- 设置为 `stopped` 或 `completed` 会清理 open intent workers 和 reason lease。

### POST /projects/{project_id}/complete

返回 `410 Gone`。

### POST /projects/{project_id}/reopen

返回 `410 Gone`。

## Reason Lease APIs

Reason lease 是 project-level coordination state，不是 graph data。

### POST /projects/{project_id}/reason/claim

Body：

```json
{"worker": "worker-a", "trigger": "initial"}
```

只有 `active` projects 允许 claim。被其他 active claimant 持有时返回 409。

### POST /projects/{project_id}/reason/heartbeat

Body：

```json
{"worker": "worker-a"}
```

只有当前 claimant 可以 heartbeat。

### POST /projects/{project_id}/reason/release

Body：

```json
{"worker": "worker-a"}
```

如果 reason lease 由该 worker 持有，则释放 lease。

## Recon APIs

### POST /projects/{project_id}/recon/reason-round

Body：

```json
{"stable": true}
```

仅 recon 可用。递增 `recon_reason_rounds`。`stable=true` 时递增 stable rounds，否则把 `recon_stable_rounds` 重置为 0。达到 `recon_max_reason_rounds` 时，项目自动变为 `stopped` 并清理 reason lease。

### POST /projects/{project_id}/recon/explore-round

仅 recon 可用。递增 `recon_explore_rounds`。Recon explore intent conclude 时 server 也会记录该 round。

### POST /projects/{project_id}/recon/judgements

仅 recon 可用。使用当前 YAML export 创建 queued judge ephemeral job。

### GET /projects/{project_id}/recon/judgements

仅 recon 可用。返回当前 recon project 的 judgement result 列表，按创建时间倒序排列。该接口返回轻量结果，不返回大体积 `input_snapshot_yaml`。

### GET /projects/{project_id}/recon/judgements/{job_id}

返回指定 judge ephemeral job，包含 `input_snapshot_yaml`。

## Snapshot 与 Fork APIs

### POST /projects/{project_id}/snapshots

仅 recon 可用。Body：

```json
{
  "snapshot_type": "recon_fork",
  "selected_fact_ids": ["f001", "f003"]
}
```

存储 `summary_yaml`、selected fact IDs 和 stats。

### GET /projects/{project_id}/snapshots

列出项目 snapshots。

### POST /projects/{project_id}/fork-vuln

仅 recon 可用。Body：

```json
{
  "title": "Validate upload candidates",
  "snapshot_id": "snap_001",
  "auth_mode": "anonymous",
  "candidate_limit": 10,
  "accounts": null
}
```

规则：

- Parent project 必须是 recon。
- Snapshot 必须属于 parent。
- Child 创建为 `project_kind="vuln"`。
- Child 记录 `parent_project_id` 和 `parent_snapshot_id`。
- Child 获得 `origin` 和包含 `recon_snapshot` 的 `f001`。
- Selected parent facts 可以复制到 child。
- Authenticated child vuln 需要在 fork request 中提供 accounts。

### GET /projects/{project_id}/children

列出 child vuln project summaries。

## Ephemeral Job APIs

### GET /ephemeral-jobs/queued?job_type=judge

列出指定类型的 queued jobs。过期的 queued/running jobs 会被标记为 `expired`。

### POST /ephemeral-jobs/{job_id}/claim

Body：

```json
{"worker": "judge-worker"}
```

把 queued job 移动到 `running`。

### POST /ephemeral-jobs/{job_id}/finish

Body：

```json
{"worker": "judge-worker", "result": {"verdict": "ready"}}
```

把 job 移动到 `succeeded`。对 judge jobs，`ready|not_ready|blocked` verdict 会更新 project 的 `judge_status` 和 `judged_at`。

### POST /ephemeral-jobs/{job_id}/fail

Body：

```json
{"worker": "judge-worker", "error": "parse failed"}
```

把 queued/running job 移动到 `failed`。如果 job 已有 worker，只允许同一 worker fail。

## Hint APIs

### POST /projects/{project_id}/hints

Body：

```json
{"content": "Prioritize authenticated API surface", "creator": "human"}
```

允许在 `active`、`stopped` 和 `completed` 状态下追加 hint。

## Intent APIs

### POST /projects/{project_id}/intents

Body：

```json
{
  "from": ["f001"],
  "description": "Test upload extension bypass",
  "creator": "reason-worker",
  "worker": null,
  "intent_kind": "explore",
  "finding_id": null,
  "auth_scope": "anonymous"
}
```

规则：

- 只有 `active` projects 允许创建 intent。
- `from` 必须引用已有 facts。
- `worker` 必须为空，或与 `creator` 相同。
- Recon explore intents 应包含 `auth_scope`；dispatcher 会拒绝 reason 输出中缺失该字段的 payload。
- Vuln explore intents 省略 `auth_scope` 时继承 project `auth_mode`；显式值必须与 project `auth_mode` 一致。
- `intent_kind="report"` 要求 `finding_id`。
- Report intents 忽略 `auth_scope` 并存储为 null。

### POST /projects/{project_id}/intents/{intent_id}/heartbeat

Body：

```json
{"worker": "explore-worker"}
```

Claim 或续租 open intent。

### POST /projects/{project_id}/intents/{intent_id}/release

Body：

```json
{"worker": "explore-worker"}
```

释放由该 worker 持有的 open intent。

### POST /projects/{project_id}/intents/{intent_id}/conclude

Body：

```json
{
  "worker": "explore-worker",
  "description": "Upload endpoint rejects double extensions",
  "findings": []
}
```

创建一个新 fact 并 conclude intent。在 recon projects 上，该操作会递增 `recon_explore_rounds`。

Finding 示例：

```json
{
  "title": "Order IDOR",
  "vulnerability_type": "idor",
  "severity": "high",
  "target": "https://target.example",
  "location": "/api/orders/{id}",
  "impact": "A user can read another user's order",
  "evidence": "See /home/kali/evidence/order-idor.txt",
  "reproduction": "Login as user A and request user B's order ID",
  "remediation": "Check ownership on every order read",
  "status": "open",
  "research_value": "high",
  "next_action": "report",
  "followup_reason": "",
  "followup_intent_description": ""
}
```

### POST /projects/{project_id}/intents/{intent_id}/report

Body：

```json
{
  "worker": "report-worker",
  "report_markdown": "# Order IDOR\n\n...",
  "report_json": {"severity": "high"}
}
```

只接受 `intent_kind="report"` 的 intent。Server 创建 `finding_reports` record，conclude report intent，并把 finding `report_status` 设为 `drafted`。

## Export APIs

### GET /projects/{project_id}/export?format=yaml

YAML 包含：

- `project.project_kind`
- `project.auth_mode`
- `project.parent_project_id`
- `project.parent_snapshot_id`
- recon projects 的 recon block。
- hints。
- facts。
- findings 及 lifecycle fields。
- accounts。
- intents 及 `intent_kind`、`finding_id`、`auth_scope`。
- reports。

YAML 不包含 `project.mode`、`project.bootstrap_enabled`、`session_lock_enabled` 或 `session_lock`。

### GET /projects/{project_id}/export?format=timeline

Timeline 使用 kind/auth 语义和 finding/report lifecycle events，不描述 Standard 或 bootstrap flows。
