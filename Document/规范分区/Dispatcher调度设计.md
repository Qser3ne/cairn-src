# Cairn SRC-only Dispatcher 调度设计

## 范围

Dispatcher 是 model worker 的唯一协议写入方。Worker 只接收 prompt 并返回 JSON，不直接调用 Cairn API。

当前支持的任务类型：

- `reason`
- `explore`
- `judge`
- `report`

已移除的任务类型和流程：

- 无 `bootstrap` task。
- 无 `bootstrap` intent。
- 无 Standard prompt 分支。
- 无自动 complete/reopen 工作流。

## 架构

```text
Cairn Server
  Projects / Facts / Intents / Hints / Findings / Snapshots / Jobs
        ^
        | HTTP API
        v
Dispatcher
  scheduling, leases, worker selection, containers, writeback
        ^
        | prompts and JSON outputs
        v
Worker CLI in project container
```

每个项目由 `ContainerManager` 管理一个 project worker container。Dispatcher 可以跨项目并发运行多个任务，并受全局并发和单项目并发限制约束。

## 配置

主配置文件为 `dispatch.yaml`。关键字段：

```yaml
runtime:
  interval: 3
  max_workers: 8
  max_running_projects: 3
  max_project_workers: 4
  healthcheck_timeout: 20
  worker_healthcheck: startup_only
  prompt_group: default

tasks:
  reason:
    timeout: 300
    max_intents: 2
  explore:
    timeout: 300
    conclude_timeout: 90
  judge:
    timeout: 120
  report:
    timeout: 180

workers:
  - name: codex-worker
    type: codex
    task_types: [reason, explore, judge, report]
    max_running: 2
    priority: 0
```

`TaskType` 只允许 `reason|explore|judge|report`。Worker 配置中出现 `bootstrap` 属于非法配置。

## Prompt 加载

Prompt loader 签名：

```python
load_prompt(group, name, project_kind)
```

默认 prompt 目录布局：

```text
cairn/src/cairn/dispatcher/prompts/default/
  recon/
    reason.md
    explore.md
    explore_conclude.md
    judge.md
  vuln/
    reason.md
    explore.md
    explore_conclude.md
    report.md
```

`mock` prompt group 仍保持平铺目录，用于降低测试成本。

## 任务契约

### Reason

输入占位符：

- `{graph_yaml}`
- `{fact_ids}`
- `{open_intents}`
- `{max_intents}`

可接受输出：

```json
{"accepted": true, "data": {"intents": [{"from": ["origin"], "description": "..."}]}}
```

```json
{"accepted": true, "data": {"decision": "noop", "intents": []}}
```

```json
{"accepted": true, "data": {"decision": "no_new_high_value", "intents": []}}
```

拒绝输出：

```json
{"accepted": false, "reason": "policy_refusal"}
```

非法输出：

```json
{"accepted": true, "data": {"complete": {"description": "done"}}}
```

Reason 写回规则：

- `intents`：最多创建 `tasks.reason.max_intents` 个 explore intents。
- 重复 intent 返回 409 时跳过，不把 reason 任务视为失败。
- Recon `intents`：记录 reason round，`stable=false`。
- Recon `noop`：记录 reason round，`stable=false`。
- Recon `no_new_high_value`：记录 reason round，`stable=true`。
- Vuln reason 不影响 recon counters。
- Reason 任务结束时尽量释放 project reason lease。

### Explore

输入占位符：

- `{graph_yaml}`
- `{intent_id}`
- `{intent_description}`
- `{auth_context}`，用于默认 recon/vuln prompts。

可接受输出：

```json
{"accepted": true, "data": {"description": "..."}}
```

Vuln explore 可以包含 findings：

```json
{
  "accepted": true,
  "data": {
    "description": "...",
    "findings": [
      {
        "title": "Order IDOR",
        "vulnerability_type": "idor",
        "severity": "high",
        "target": "https://target.example",
        "location": "/api/orders/{id}",
        "impact": "...",
        "evidence": "...",
        "reproduction": "...",
        "remediation": "...",
        "status": "open",
        "research_value": "high",
        "next_action": "report",
        "followup_reason": "",
        "followup_intent_description": ""
      }
    ]
  }
}
```

Explore 写回规则：

- 启动 worker process 前先通过 heartbeat claim intent。
- 成功后调用 `/intents/{intent_id}/conclude`。
- 解析失败或超时时，如果取消状态允许，可以运行 `explore_conclude` fallback。
- Recon explore 即使模型越界返回 `findings`，dispatcher 也会在写回前丢弃，只保留 `description` 写成 fact；vuln explore 仍允许把合法 findings 传给 server。
- Recon conclude 会在 server 上增加 explore rounds。
- 只有当 `intent.auth_scope == "authenticated"` 时，explore 才领取一个项目账号；账号租约在 `_reap_futures` 中释放。

### Judge

Judge 是 ephemeral job，不 claim project reason lease，也不写 graph data。

输入占位符：

- `{graph_yaml}`，来自 job 的 `input_snapshot_yaml`。

Judge 输出用于 UI 展示和 fork 决策，是临时 readiness judgement，不代表 project completed。

可接受输出：

```json
{
  "accepted": true,
  "data": {
    "verdict": "ready",
    "score": 86,
    "recommended_action": "create_vuln_project",
    "checklist": {
      "scope_clarity": {"score": 18, "evidence": "scope and origin are explicit"},
      "asset_coverage": {"score": 17, "evidence": "primary and related assets have been sampled"},
      "endpoint_coverage": {"score": 16, "evidence": "concrete paths and parameters are present"},
      "auth_boundary_coverage": {"score": 18, "evidence": "anonymous and authenticated surfaces are separated"},
      "candidate_surface_quality": {"score": 17, "evidence": "candidate surfaces are specific enough for vuln validation"}
    },
    "blocking_gaps": [],
    "non_blocking_gaps": []
  }
}
```

合法 verdict：

- `ready`
- `not_ready`
- `blocked`

Checklist 固定字段：

- `scope_clarity`
- `asset_coverage`
- `endpoint_coverage`
- `auth_boundary_coverage`
- `candidate_surface_quality`

每项包含 0-20 的整数 `score` 和可展示的 `evidence`。总分为 0-100，建议等于五项相加。

合法 recommended action：

- `create_vuln_project`
- `continue_anonymous_recon`
- `continue_authenticated_recon`
- `clarify_scope`
- `fix_account_access`
- `stop_or_archive`

Verdict 规则：

- `ready`：`score >= 75`，没有 blocking gaps，且至少存在可用于 vuln fork 的具体候选攻击面。
- `not_ready`：`score < 75`，但可以通过继续 recon 补齐。
- `blocked`：目标不可访问、授权/范围不清、账号不可用、graph 明显缺少 origin 以外有效事实，或存在安全/合规阻断。

`blocking_gaps` 和 `non_blocking_gaps` 都是字符串数组，内容应能转化为后续 recon intent。

Judge 写回规则：

- 通过 `/ephemeral-jobs/{job_id}/claim` claim job。
- 成功完成时写入 `result_json`，并更新 project 的 `judge_status/judged_at`。
- 失败时写入 job error。
- 不创建 facts、intents、findings 或 reports。

### Report

Report 消费 `intent_kind="report"` 的 intent。

输入占位符：

- `{graph_yaml}`
- `{intent_id}`
- `{intent_description}`

可接受输出：

```json
{
  "accepted": true,
  "data": {
    "report_markdown": "# Title\n\n...",
    "report_json": {}
  }
}
```

Report 写回规则：

- 通过 heartbeat claim report intent。
- 成功后调用 `/intents/{intent_id}/report`。
- Server 创建 `finding_reports`。
- Server 设置 finding 的 `report_status="drafted"`。
- Report 不使用 explore prompt，也不创建新 fact。

## 调度规则

主循环：

1. 启动后验证一次 server timeout settings。
2. 回收已完成的 task futures。
3. 回收 container cleanup futures。
4. 拉取 projects。
5. 初始化 reason checkpoints。
6. 刷新 active runtime projects。
7. 清理 inactive 项目的 authenticated account queues。
8. 取消 inactive/deleted projects 上的本地运行任务。
9. 为 stopped/completed projects 排队 container cleanup。
10. 调度可执行的 project tasks。
11. 调度 queued judge jobs。

项目调度规则：

- inactive projects 不调度新任务。
- 初始项目指 facts 只有 `origin` 且没有 intents。
- 初始 active project 直接 dispatch reason。
- 不存在 bootstrap 分支。
- 如果 authenticated explore 等待队列中有可调度项，优先调度它。
- 否则调度最新的未 claim intent。
- 如果没有可调度的未 claim intent，且存在 reason trigger 并且 reason lease 未被持有，则 dispatch reason。
- `intent_kind="report"` dispatch report；其他 intents dispatch explore。

Reason trigger 规则：

- 初始项目返回 `initial`。
- checkpoint 后新增 facts 会触发 reason。
- checkpoint 后新增 hints 会触发 reason。
- open intent count 从非零变为零会触发 reason。
- graph 未变化时不 dispatch reason。

## Worker 选择

Worker selection 过滤条件：

- 支持当前 task type。
- 未超过 `worker.max_running`。
- 不在临时 unhealthy window 中。
- 不在临时 rejected window 中。

候选 worker 排序依据为 priority、当前运行数，再使用既有 selector tie-break 行为。

## 账号池调度

`auth_scope="authenticated"` 的 explore intents 使用账号租约：

```text
account_leases: project_id -> account_id -> intent_id
authenticated_wait_queues: project_id -> deque[intent_id]
```

规则：

- Reason、judge、report 和 anonymous explore 不领取账号。
- Authenticated explore 至少需要一个项目账号。
- 如果没有空闲账号，intent 进入 FIFO 等待队列，不会被 claim。
- Future finish/fail/cancel/crash 时释放账号租约。
- Inactive 或 anonymous projects 的账号队列和租约会被清理。

Authenticated explore 的实际并发上限为：

```text
min(accounts, runtime.max_project_workers, runtime.max_workers, available workers)
```

## 容器生命周期

Dispatcher 把非 active projects 视为硬停止：

- 不再调度新任务。
- 取消本地正在运行的任务。
- Stopped projects 执行 stopped-container cleanup。
- Completed projects 按 `container.completed_action` 执行 cleanup。
- Deleted projects 作为 orphan cleanup targets 处理。

`completed_action` 仍然只是容器策略，即使 project completion 现在是人工归档状态。

## Dispatcher 使用的 Server API

Reason：

- `GET /projects`
- `GET /projects/{id}`
- `GET /projects/{id}/export?format=yaml`
- `POST /projects/{id}/reason/claim`
- `POST /projects/{id}/reason/heartbeat`
- `POST /projects/{id}/reason/release`
- `POST /projects/{id}/intents`
- `POST /projects/{id}/recon/reason-round`

Explore：

- `POST /projects/{id}/intents/{intent_id}/heartbeat`
- `POST /projects/{id}/intents/{intent_id}/conclude`
- `POST /projects/{id}/intents/{intent_id}/release`

Judge：

- `GET /ephemeral-jobs/queued?job_type=judge`
- `POST /ephemeral-jobs/{job_id}/claim`
- `POST /ephemeral-jobs/{job_id}/finish`
- `POST /ephemeral-jobs/{job_id}/fail`

Report：

- `POST /projects/{id}/intents/{intent_id}/heartbeat`
- `POST /projects/{id}/intents/{intent_id}/report`
- `POST /projects/{id}/intents/{intent_id}/release`

## 可观测性

Dispatcher 日志应重点记录状态变化：

- container creation 和 cleanup。
- worker healthcheck failures。
- task dispatch。
- parse failure。
- timeout。
- claim conflict。
- rejected worker cooldown。
- account lease wait/release。
- judge claim/finish/fail。
- report draft writeback。

稳定轮询和 routine heartbeat success 应保持安静。

## 验证

推荐命令：

```bash
python3 -m compileall -q cairn/src/cairn cairn/tests
cd cairn && pytest -q tests
```

如果 `uv` 可用：

```bash
cd cairn && uv run pytest
```
