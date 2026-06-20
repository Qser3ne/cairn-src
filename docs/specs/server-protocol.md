# Cairn SRC-only Server Protocol

## Scope

Cairn Server is the source of truth for projects, facts, intents, hints, findings, snapshots, reports, account pools, and ephemeral jobs. It does not run model reasoning; it keeps graph state consistent and exposes APIs used by the UI and Dispatcher.

The current protocol is SRC-only:

- `project_kind="recon"` collects attack-surface knowledge.
- `project_kind="vuln"` validates vulnerabilities from a recon snapshot.
- New projects default to recon.
- New vuln projects must reference a parent recon project and a snapshot.
- Standard mode and bootstrap auto-completion are removed.
- `/projects/{id}/complete` and `/projects/{id}/reopen` return `410 Gone`.
- `completed` is a manual archive state. It is terminal and cannot be restored to `active`.

## Core Concepts

### Project

Project fields:

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

Status rules:

- `active` accepts graph write operations.
- `stopped` rejects graph write operations, clears open intent workers and reason lease, and can be changed back to `active`.
- `completed` rejects graph write operations and cannot change back to `active` or `stopped`.

### Fact

Facts are immutable graph nodes. Each project starts with:

- `origin`

Regular facts use scoped IDs such as `f001`.

### Intent

Intent fields:

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

Service-side duplicate protection is intentionally narrow: same project, same normalized `description`, same `auth_scope`, and same exact `from` set returns `409`.

### Hint

Hints are graph-adjacent strategy notes. They can be added while a project is `active`, `stopped`, or `completed`.

### Finding

Findings belong to vuln exploration results and include lifecycle fields:

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

`next_action="follow_up"` requires `followup_intent_description`. Conclude creates a follow-up explore intent automatically. `next_action="report"` creates a report intent and sets `report_status="queued"`.

### Snapshot

Recon snapshots store a YAML export plus selected fact IDs and stats. They are the only supported source for new vuln projects.

### Ephemeral Job

Ephemeral jobs are temporary tasks that do not write graph data. Judge uses this path.

## Database And Migration

New schemas do not declare `projects.mode` or `projects.bootstrap_enabled`.

Startup migration order:

1. If legacy `projects.mode` exists, detect `mode='standard'`.
2. If any legacy standard project exists, raise `RuntimeError` and require export or deletion.
3. If only legacy `mode='src'` projects exist, migrate them to `project_kind='vuln'`.
4. Legacy migrated vuln projects may have null parent/snapshot. New vuln projects may not.
5. Remove old `session_lock_enabled` and `session_lock` columns during table rebuild migrations.
6. Preserve `project_accounts`.
7. Migrate legacy recon projects to `auth_mode='dual'`.
8. Backfill legacy explore intent `auth_scope` from project auth mode, defaulting to `anonymous`.

## Project APIs

### GET /projects

Returns project summaries, including graph counts, kind/auth fields, recon counters, and judge status.

### POST /projects

Creates a project. Body:

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

Rules:

- `project_kind` defaults to `recon`.
- `mode`, `bootstrap_enabled`, and legacy `goal` are forbidden extra fields and return 422.
- recon projects cannot have parent fields, cannot explicitly choose `auth_mode`, and are stored as `auth_mode="dual"`.
- new recon projects require at least one account.
- new vuln projects require `parent_project_id` and `parent_snapshot_id`.
- vuln parent must be recon.
- vuln snapshot must belong to that parent.
- authenticated vuln projects require at least one account.
- anonymous vuln projects cannot include accounts.

### GET /projects/{project_id}

Returns project meta, facts, intents, hints, findings, and accounts.

### DELETE /projects/{project_id}

Deletes a project. Deleting a recon project with child vuln projects returns 409.

### PUT /projects/{project_id}/title

Renames a project without changing graph data.

### PUT /projects/{project_id}/status

Body:

```json
{"status": "stopped"}
```

Allowed statuses are `active`, `stopped`, and `completed`.

Rules:

- `active <-> stopped` is allowed.
- setting `completed` is allowed as manual archive.
- once `completed`, any non-completed status returns 409.
- setting `stopped` or `completed` clears open intent workers and reason lease.

### POST /projects/{project_id}/complete

Returns `410 Gone`.

### POST /projects/{project_id}/reopen

Returns `410 Gone`.

## Reason Lease APIs

Reason lease is project-level coordination state, not graph data.

### POST /projects/{project_id}/reason/claim

Body:

```json
{"worker": "worker-a", "trigger": "initial"}
```

Only `active` projects allow claim. A different active claimant returns 409.

### POST /projects/{project_id}/reason/heartbeat

Body:

```json
{"worker": "worker-a"}
```

Only the current claimant can heartbeat.

### POST /projects/{project_id}/reason/release

Body:

```json
{"worker": "worker-a"}
```

Releases the reason lease if held by the worker.

## Recon APIs

### POST /projects/{project_id}/recon/reason-round

Body:

```json
{"stable": true}
```

Recon only. Increments `recon_reason_rounds`. Stable rounds increment when `stable=true`; otherwise `recon_stable_rounds` resets to 0. If `recon_max_reason_rounds` is reached, the project is automatically set to `stopped` and the reason lease is cleared.

### POST /projects/{project_id}/recon/explore-round

Recon only. Increments `recon_explore_rounds`. The server also records this when a recon explore intent is concluded.

### POST /projects/{project_id}/recon/judgements

Recon only. Creates a queued judge ephemeral job using the current YAML export.

### GET /projects/{project_id}/recon/judgements/{job_id}

Returns a judge ephemeral job for the project.

## Snapshot And Fork APIs

### POST /projects/{project_id}/snapshots

Recon only. Body:

```json
{
  "snapshot_type": "recon_fork",
  "selected_fact_ids": ["f001", "f003"]
}
```

Stores `summary_yaml`, selected fact IDs, and stats.

### GET /projects/{project_id}/snapshots

Lists snapshots for a project.

### POST /projects/{project_id}/fork-vuln

Recon only. Body:

```json
{
  "title": "Validate upload candidates",
  "snapshot_id": "snap_001",
  "auth_mode": "anonymous",
  "candidate_limit": 10,
  "accounts": null
}
```

Rules:

- parent project must be recon.
- snapshot must belong to the parent.
- child is created as `project_kind="vuln"`.
- child records `parent_project_id` and `parent_snapshot_id`.
- child gets `origin` and `f001` containing `recon_snapshot`.
- selected parent facts may be copied into the child.
- authenticated child vuln requires accounts in the fork request.

### GET /projects/{project_id}/children

Lists child vuln project summaries.

## Ephemeral Job APIs

### GET /ephemeral-jobs/queued?job_type=judge

Lists queued jobs of the requested type. Expired queued/running jobs are marked `expired`.

### POST /ephemeral-jobs/{job_id}/claim

Body:

```json
{"worker": "judge-worker"}
```

Moves a queued job to `running`.

### POST /ephemeral-jobs/{job_id}/finish

Body:

```json
{"worker": "judge-worker", "result": {"verdict": "ready"}}
```

Moves a job to `succeeded`. For judge jobs, verdict `ready|not_ready|blocked` updates project `judge_status` and `judged_at`.

### POST /ephemeral-jobs/{job_id}/fail

Body:

```json
{"worker": "judge-worker", "error": "parse failed"}
```

Moves a job to `failed`.

## Hint APIs

### POST /projects/{project_id}/hints

Body:

```json
{"content": "Prioritize authenticated API surface", "creator": "human"}
```

Allowed for `active`, `stopped`, and `completed`.

## Intent APIs

### POST /projects/{project_id}/intents

Body:

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

Rules:

- only `active` projects allow creation.
- `from` must reference existing facts.
- `worker` must be null or equal to `creator`.
- recon explore intents should include `auth_scope`; reason payloads without it are rejected by the dispatcher.
- vuln explore intents inherit project `auth_mode` when `auth_scope` is omitted; an explicit value must match project `auth_mode`.
- `intent_kind="report"` requires `finding_id`.
- report intents ignore `auth_scope` and store null.

### POST /projects/{project_id}/intents/{intent_id}/heartbeat

Body:

```json
{"worker": "explore-worker"}
```

Claims or renews an open intent.

### POST /projects/{project_id}/intents/{intent_id}/release

Body:

```json
{"worker": "explore-worker"}
```

Releases an open intent held by that worker.

### POST /projects/{project_id}/intents/{intent_id}/conclude

Body:

```json
{
  "worker": "explore-worker",
  "description": "Upload endpoint rejects double extensions",
  "findings": []
}
```

Creates a new fact and concludes the intent. On recon projects, this increments `recon_explore_rounds`.

Finding example:

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

Body:

```json
{
  "worker": "report-worker",
  "report_markdown": "# Order IDOR\n\n...",
  "report_json": {"severity": "high"}
}
```

Only `intent_kind="report"` intents are accepted. The server creates a `finding_reports` record, concludes the report intent, and sets the finding `report_status` to `drafted`.

## Export APIs

### GET /projects/{project_id}/export?format=yaml

YAML includes:

- `project.project_kind`
- `project.auth_mode`
- `project.parent_project_id`
- `project.parent_snapshot_id`
- recon block for recon projects
- hints
- facts
- findings with lifecycle fields
- accounts
- intents with `intent_kind`, `finding_id`, and `auth_scope`
- reports

It does not include `project.mode`, `project.bootstrap_enabled`, `session_lock_enabled`, or `session_lock`.

### GET /projects/{project_id}/export?format=timeline

Timeline uses kind/auth wording and finding/report lifecycle events. It does not describe Standard or bootstrap flows.
