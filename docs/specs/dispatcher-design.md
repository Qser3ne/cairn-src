# Cairn SRC-only Dispatcher Design

## Scope

Dispatcher is the only protocol writer for model workers. Workers receive prompts and return JSON. They do not call Cairn APIs directly.

Supported task types:

- `reason`
- `explore`
- `judge`
- `report`

Removed task types and flows:

- no bootstrap task
- no bootstrap intent
- no Standard prompt branch
- no automatic complete/reopen workflow

## Architecture

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

Each project gets a worker container managed by `ContainerManager`. The dispatcher can run multiple tasks across projects, bounded by global and per-project limits.

## Configuration

Main config file: `dispatch.yaml`.

Important fields:

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

`TaskType` is `reason|explore|judge|report`. Worker configs containing `bootstrap` are invalid.

## Prompt Loading

Prompt loader signature:

```python
load_prompt(group, name, project_kind)
```

Default prompt layout:

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

The `mock` prompt group remains flat to keep tests lightweight.

## Task Contracts

### Reason

Input placeholders:

- `{graph_yaml}`
- `{fact_ids}`
- `{open_intents}`
- `{max_intents}`

Accepted outputs:

```json
{"accepted": true, "data": {"intents": [{"from": ["origin"], "description": "..."}]}}
```

```json
{"accepted": true, "data": {"decision": "noop", "intents": []}}
```

```json
{"accepted": true, "data": {"decision": "no_new_high_value", "intents": []}}
```

Rejected output:

```json
{"accepted": false, "reason": "policy_refusal"}
```

Invalid output:

```json
{"accepted": true, "data": {"complete": {"description": "done"}}}
```

Reason writeback:

- `intents`: create up to `tasks.reason.max_intents` explore intents.
- duplicate intent 409 is skipped.
- recon `intents`: record reason round with `stable=false`.
- recon `noop`: record reason round with `stable=false`.
- recon `no_new_high_value`: record reason round with `stable=true`.
- vuln reason does not affect recon counters.
- reason always releases the project reason lease at task end when possible.

### Explore

Input placeholders:

- `{graph_yaml}`
- `{intent_id}`
- `{intent_description}`
- `{auth_context}` for default recon/vuln prompts

Accepted output:

```json
{"accepted": true, "data": {"description": "..."}}
```

Vuln explore may include findings:

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

Explore writeback:

- heartbeat claims the intent before launching worker process.
- success calls `/intents/{intent_id}/conclude`.
- parse failure or timeout may run `explore_conclude` fallback when cancellation state allows it.
- recon conclude increments explore rounds on the server.
- authenticated explore leases one project account before claim and releases it in `_reap_futures`.

### Judge

Judge is an ephemeral job. It does not claim project reason lease and does not write graph data.

Input placeholder:

- `{graph_yaml}` from job `input_snapshot_yaml`

Accepted output:

```json
{
  "accepted": true,
  "data": {
    "verdict": "ready",
    "score": 86,
    "recommended_action": "create_vuln_project",
    "checklist": {},
    "blocking_gaps": [],
    "non_blocking_gaps": []
  }
}
```

Valid verdicts:

- `ready`
- `not_ready`
- `blocked`

Judge writeback:

- claim `/ephemeral-jobs/{job_id}/claim`.
- finish writes `result_json` and updates project `judge_status/judged_at`.
- fail writes job error.
- no facts, intents, findings, or reports are created.

### Report

Report consumes an `intent_kind="report"` intent.

Input placeholders:

- `{graph_yaml}`
- `{intent_id}`
- `{intent_description}`

Accepted output:

```json
{
  "accepted": true,
  "data": {
    "report_markdown": "# Title\n\n...",
    "report_json": {}
  }
}
```

Report writeback:

- heartbeat claims the report intent.
- success calls `/intents/{intent_id}/report`.
- server creates `finding_reports`.
- server sets finding `report_status="drafted"`.
- report does not use explore prompt and does not create a new fact.

## Scheduling

The main loop:

1. validate server timeout settings once.
2. reap completed task futures.
3. reap container cleanup futures.
4. list projects.
5. initialize reason checkpoints.
6. refresh active runtime projects.
7. clean inactive authenticated account queues.
8. cancel tasks for inactive/deleted projects.
9. queue container cleanup for stopped/completed projects.
10. dispatch available project tasks.
11. dispatch queued judge jobs.

Project dispatch rules:

- inactive projects are skipped.
- initial project means facts are exactly `origin`, and there are no intents.
- initial active project dispatches reason directly.
- there is no bootstrap branch.
- if authenticated explore wait queue has dispatchable item, dispatch it first.
- if reason trigger exists and no reason lease is held, dispatch reason.
- otherwise dispatch newest unclaimed intent.
- `intent_kind="report"` dispatches report; all other intents dispatch explore.

Reason trigger rules:

- initial project returns `initial`.
- more facts since checkpoint triggers reason.
- more hints since checkpoint triggers reason.
- open intent count dropping from nonzero to zero triggers reason.
- unchanged graph does not dispatch reason.

## Worker Selection

Worker selection filters by:

- task type support
- `worker.max_running`
- temporary unhealthy window
- temporary rejected window

Candidates are ordered by priority, then current running count, then the existing selector tie-break behavior.

## Account Pool Scheduling

Authenticated projects use account leases for explore only:

```text
account_leases: project_id -> account_id -> intent_id
authenticated_wait_queues: project_id -> deque[intent_id]
```

Rules:

- reason, judge, and report do not lease accounts.
- authenticated explore requires at least one account.
- if no account is free, the intent enters FIFO wait queue and is not claimed.
- leases are released when futures finish, fail, cancel, or crash.
- inactive or anonymous projects have queues and leases cleared.

The practical authenticated explore concurrency cap is:

```text
min(accounts, runtime.max_project_workers, runtime.max_workers, available workers)
```

## Container Lifecycle

Dispatcher treats non-active projects as hard stops:

- no new tasks are dispatched.
- local running tasks are cancelled.
- stopped projects get stopped-container cleanup.
- completed projects get cleanup according to `container.completed_action`.
- deleted projects are treated as orphan cleanup targets.

`completed_action` remains a container policy even though project completion is now manual archive.

## Server APIs Used By Dispatcher

Reason:

- `GET /projects`
- `GET /projects/{id}`
- `GET /projects/{id}/export?format=yaml`
- `POST /projects/{id}/reason/claim`
- `POST /projects/{id}/reason/heartbeat`
- `POST /projects/{id}/reason/release`
- `POST /projects/{id}/intents`
- `POST /projects/{id}/recon/reason-round`

Explore:

- `POST /projects/{id}/intents/{intent_id}/heartbeat`
- `POST /projects/{id}/intents/{intent_id}/conclude`
- `POST /projects/{id}/intents/{intent_id}/release`

Judge:

- `GET /ephemeral-jobs/queued?job_type=judge`
- `POST /ephemeral-jobs/{job_id}/claim`
- `POST /ephemeral-jobs/{job_id}/finish`
- `POST /ephemeral-jobs/{job_id}/fail`

Report:

- `POST /projects/{id}/intents/{intent_id}/heartbeat`
- `POST /projects/{id}/intents/{intent_id}/report`
- `POST /projects/{id}/intents/{intent_id}/release`

## Observability

Dispatcher logs should emphasize state changes:

- container creation and cleanup
- worker healthcheck failures
- task dispatch
- parse failure
- timeout
- claim conflict
- rejected worker cooldown
- account lease wait/release
- judge claim/finish/fail
- report draft writeback

Stable polling and routine heartbeat success should stay quiet.

## Verification

Recommended commands:

```bash
python3 -m compileall -q cairn/src/cairn cairn/tests
cd cairn && pytest -q tests
```

If `uv` is available:

```bash
cd cairn && uv run pytest
```
