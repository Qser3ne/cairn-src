# Worker Scheduling Settings Design

## Context

Cairn already models the active SRC workflow as five dispatcher task types:

- `collection_reason`
- `collection_explore`
- `validation_reason`
- `validation_explore`
- `report`

`dispatch.example.yaml`, prompt contracts, dispatcher config validation, task runners, and tests already understand these names. The missing behavior is scheduling policy: new projects should spend an initial period on information collection before validation/report work starts, and collection-related work should have its own small global parallelism cap.

The server already has a global `/settings` API, SQLite `settings` table, and browser UI for runtime settings. That is the right place to add user-facing controls because the requested values are product behavior, not worker backend credentials.

## Decisions

Add two independent global settings:

- `initial_collection_rounds`, default `5`: number of completed collection executions required before validation/report work is allowed for a project.
- `collection_worker_limit`, default `1`: global cap for currently running `collection_reason` plus `collection_explore` tasks.

Use `collection_explore_rounds` as the warmup counter because it records actual information-collection execution. `collection_reason_rounds` alone only proves planning ran; it does not prove collection work was performed.

Allow early transition out of warmup if collection cannot produce or run more work. The dispatcher should not leave a project permanently blocked just because fewer than `initial_collection_rounds` collection explore tasks were available.

## Settings And Persistence

Extend the existing settings surface:

- `cairn.server.models.Settings` gains `initial_collection_rounds: int = Field(ge=0)` and `collection_worker_limit: int = Field(ge=1)`.
- SQLite `settings` gains matching columns with defaults `5` and `1`.
- DB configuration migrates existing settings tables by adding missing columns.
- `GET /settings` and `PUT /settings` read and write all four fields: existing timeouts plus the two new scheduling settings.
- The static UI `Server Settings` modal exposes both fields and preserves strict unknown-field behavior.

`initial_collection_rounds=0` disables the warmup gate and immediately allows validation/report scheduling. `collection_worker_limit` must remain at least `1` so collection can make progress.

## Dispatcher Scheduling

The dispatcher should keep the five task types as the scheduling unit. Worker selection remains based on worker `task_types`, `priority`, running count, health, and rejection cooldown.

On each scheduler iteration, the dispatcher already calls `_validate_server_settings()` once at startup. The scheduling values should be loaded from `/settings` for dispatch decisions so UI changes can take effect without dispatcher restart. To avoid unnecessary API traffic, this can be cached per scheduler tick rather than fetched before every project.

Collection cap logic:

- Count running tasks whose `task_type` is `collection_reason` or `collection_explore`.
- If the count is greater than or equal to `settings.collection_worker_limit`, skip dispatching additional collection reason/explore tasks.
- The cap is global across projects and workers.
- Existing `runtime.max_workers`, `runtime.max_project_workers`, and worker `max_running` still apply.

Warmup gate logic:

- A project is in collection warmup when `project.project.collection_explore_rounds < settings.initial_collection_rounds` and collection has not converged.
- While in warmup, the dispatcher should not dispatch report intents, validation explore intents, or validation reason.
- While in warmup, collection reason and collection explore remain eligible, subject to the collection cap and existing global/project/worker limits.
- Once `collection_explore_rounds >= initial_collection_rounds`, validation and report can run in parallel with collection.
- If collection has no open collection intent and collection reason has already recorded at least one stable/no-op-or-intent round, warmup is considered converged and validation/report can start early.

The early transition avoids a deadlock where collection produces fewer than the requested number of explore intents. It is intentionally project-local and does not disable future collection: later facts/hints can still trigger collection reason and collection explore after validation begins.

## Task Ordering

Existing post-warmup order remains:

1. Authenticated collection/validation wait queue items are considered first when a cookie session is available.
2. Unclaimed report intents are preferred over validation explore.
3. Validation explore is preferred over collection explore.
4. Collection explore runs when no higher-priority unclaimed post-warmup work is chosen.
5. Reason triggers run after currently available executable intents.

During warmup, report and validation candidates are filtered out before this ordering is applied, so collection has exclusive scheduling priority until the gate opens or converges.

## Worker Configuration Example

`dispatch.example.yaml` should show the five logical worker roles clearly. This can be represented as five worker entries, each with one task type:

- collection intent producer: `task_types: [collection_reason]`
- collection executor: `task_types: [collection_explore]`
- validation intent producer: `task_types: [validation_reason]`
- validation executor: `task_types: [validation_explore]`
- report executor: `task_types: [report]`

This is an example and not a schema restriction. Operators may still assign multiple compatible task types to one backend worker if desired.

## UI And Documentation

Update the browser `Server Settings` modal with:

- Initial collection rounds
- Collection worker limit

Update README and architecture/user docs to describe:

- The five task types as separate logical worker roles.
- Startup collection warmup before validation/report.
- Collection-related global parallelism cap.
- The two settings and their defaults.

## Testing

Add or update tests for:

- New databases include both settings columns and default values.
- Existing settings tables migrate missing columns.
- `GET /settings` returns all four fields.
- `PUT /settings` updates all four fields and rejects unknown fields.
- Static UI includes the new settings controls and loads/saves both fields.
- Scheduler blocks validation/report while `collection_explore_rounds` is below `initial_collection_rounds`.
- Scheduler allows validation/report once `collection_explore_rounds` reaches the threshold.
- Scheduler allows early validation/report when collection warmup converged before reaching the threshold.
- Scheduler enforces `collection_worker_limit` globally across `collection_reason` and `collection_explore`.
- Scheduler does not let the collection cap block validation/report tasks after warmup.

## Non-Goals

- Do not introduce a sixth task type or a separate worker backend class.
- Do not remove support for workers that handle multiple task types.
- Do not make these settings per-project.
- Do not change report writeback, finding lifecycle, prompt contracts, or worker JSON contracts beyond documentation/examples.
