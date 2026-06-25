# Overall Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve Cairn SRC stability, performance, and maintainability without changing the explicitly excluded security-sensitive behavior.

**Architecture:** Keep the current FastAPI/SQLite server, dispatcher scheduler, worker task, and static UI boundaries. Make targeted changes inside existing modules, add regression tests before production code, and avoid broad rewrites.

**Tech Stack:** Python >=3.12, FastAPI, SQLite via `sqlite3`, Pydantic, pytest, uv, static HTML/Alpine UI, GitHub Actions.

## Global Constraints

- Do not change Cookie, account, export redaction, credential, Docker context, or port exposure behavior in this round.
- Do not introduce new runtime dependencies.
- Use TDD for behavior changes: add a failing regression test before production code.
- Keep existing public route names and CLI commands unchanged.
- Do not split the static UI into a build pipeline in this round.
- Do not commit unless the user explicitly asks for a commit.

---

### Task 1: Report Task Cancellation Release

**Files:**
- Modify: `cairn/src/cairn/dispatcher/tasks/report.py`
- Test: `cairn/tests/test_worker_tasks.py`

**Interfaces:**
- Consumes: `best_effort_release(client, project_id, intent_id, worker_name)` from `cairn.dispatcher.tasks.common`.
- Produces: `run_report_task(config, client, container_manager, project, export_yaml, intent, worker, cancellation) -> str` returning `"cancelled"`, `"failed"`, `"unhealthy"`, `"rejected"`, or `"success"` with release behavior matching explore tasks.

- [ ] Add a test where report healthcheck sees cancellation and assert `FakeClient.released == [(project_id, intent_id, worker)]`.
- [ ] Run `cd cairn && uv run --group dev pytest -s tests/test_worker_tasks.py::test_report_task_releases_intent_when_healthcheck_is_cancelled` and confirm it fails because release is missing.
- [ ] Update `report.py` so the cancellation branch calls `best_effort_release()` before returning `"cancelled"`.
- [ ] Add a test where `lease.failure` is set after healthcheck and assert release plus `"failed"`.
- [ ] Run `cd cairn && uv run --group dev pytest -s tests/test_worker_tasks.py` and confirm it passes.

### Task 2: Scheduler Orphan Cleanup And Cooldown Expiry

**Files:**
- Modify: `cairn/src/cairn/dispatcher/scheduler/loop.py`
- Test: `cairn/tests/test_scheduler_logic.py`

**Interfaces:**
- Consumes: `ContainerManager.managed_container_names()`, `ContainerManager.needs_orphan_cleanup(name)`, `ContainerManager.cleanup_orphan(name)`.
- Produces: `_queue_container_cleanups(summaries)` scheduling completed, stopped, and orphan cleanups; `_select_worker(project_id, task_type)` pruning expired cooldowns.

- [ ] Add scheduler tests for orphan cleanup submission and active-project orphan skip.
- [ ] Run the new scheduler tests and confirm they fail because orphan cleanup is not queued.
- [ ] Implement `_cleanup_orphan_containers()` and call it from `_queue_container_cleanups()` after completed/stopped cleanup.
- [ ] Add scheduler tests proving a successful concurrent task does not clear an existing `worker_unhealthy_until` or `worker_rejected_until` entry.
- [ ] Update `_reap_futures()` to stop popping cooldown entries on non-unhealthy/non-rejected outcomes.
- [ ] Update `_select_worker()` to remove expired cooldown entries when `until <= now`.
- [ ] Run `cd cairn && uv run --group dev pytest -s tests/test_scheduler_logic.py` and confirm it passes.

### Task 3: Worker Output Contract Validation

**Files:**
- Modify: `cairn/src/cairn/dispatcher/contracts.py`
- Test: `cairn/tests/test_contracts_and_drivers.py`
- Test: `cairn/tests/test_worker_tasks.py`

**Interfaces:**
- Consumes: `validate_judge_payload(payload: dict[str, Any])` and `validate_explore_payload(payload: dict[str, Any])`.
- Produces: strict validation for judge payloads and complete validation for explore finding payloads.

- [ ] Add tests that reject judge payloads missing `score`, `recommended_action`, `checklist`, `blocking_gaps`, or `non_blocking_gaps`.
- [ ] Add a positive judge validation test with the documented five checklist keys.
- [ ] Run the new contract tests and confirm failures show the validator is too permissive.
- [ ] Implement judge validation with allowed verdicts and recommended actions from `docs/architecture/worker-contracts.md`.
- [ ] Add tests that reject finding payloads missing each required Server finding field.
- [ ] Update `validate_explore_payload()` to validate finding objects without returning them in the normalized fact payload.
- [ ] Update existing worker task mock judge output to include the full valid judge shape.
- [ ] Run `cd cairn && uv run --group dev pytest -s tests/test_contracts_and_drivers.py tests/test_worker_tasks.py` and confirm it passes.

### Task 4: Server Query Stability And JSON Robustness

**Files:**
- Modify: `cairn/src/cairn/server/db.py`
- Modify: `cairn/src/cairn/server/services.py`
- Modify: `cairn/src/cairn/server/routers/export.py`
- Modify: `cairn/src/cairn/server/routers/projects.py`
- Test: `cairn/tests/test_db_migrations.py`
- Test: `cairn/tests/test_server_api.py`

**Interfaces:**
- Produces: `_ensure_indexes(conn: sqlite3.Connection) -> None` called by `configure()`.
- Produces: JSON helper functions in `services.py` for safe object/list decoding.
- Produces: `build_intents(conn, project_id) -> list[Intent]` with batched source loading.

- [ ] Add migration tests asserting representative index names exist after `db.configure()`.
- [ ] Add API tests for stable intent ordering when multiple rows share `created_at`.
- [ ] Add API tests that corrupt snapshot/job/report JSON rows and confirm API model conversion returns empty defaults instead of 500.
- [ ] Run the new tests and confirm they fail.
- [ ] Add `CREATE INDEX IF NOT EXISTS` statements through `_ensure_indexes()` and call it from `configure()`.
- [ ] Add safe JSON helper functions in `services.py` and use them for snapshot, ephemeral job, report, facts, and accounts where applicable.
- [ ] Change `build_intents()` to batch load `intent_sources` for all project intents.
- [ ] Update facts, intents, hints, projects, snapshots, reports, and export query ordering to include a stable secondary key.
- [ ] Run `cd cairn && uv run --group dev pytest -s tests/test_db_migrations.py tests/test_server_api.py` and confirm it passes.

### Task 5: Low-Risk Maintenance Gates

**Files:**
- Modify: `cairn/src/cairn/dispatcher/config.py`
- Modify: `cairn/src/cairn/server/models.py`
- Modify: `cairn/src/cairn/server/static/index.html`
- Create: `.github/workflows/python-ci.yml`
- Test: `cairn/tests/test_config_and_adapters.py`
- Test: `cairn/tests/test_server_api.py`

**Interfaces:**
- Produces: nested dispatcher config models that reject unknown fields.
- Produces: write request models that reject unknown fields while preserving alias behavior for `CreateIntentRequest.from_`.
- Produces: static UI polling that schedules the next poll only after the current poll completes.

- [ ] Add config tests proving unknown fields under `runtime`, `tasks.reason`, `tasks.explore`, and `container` are rejected.
- [ ] Add request model tests for representative unknown write fields returning 422.
- [ ] Run the new tests and confirm they fail.
- [ ] Add `ConfigDict(extra="forbid")` to nested dispatcher config models.
- [ ] Add `ConfigDict(extra="forbid")` to write request models while preserving `populate_by_name=True` where required.
- [ ] Update `index.html` polling from fixed `setInterval` to recursive `setTimeout` after each async poll finishes.
- [ ] Add `.github/workflows/python-ci.yml` running pytest and compileall.
- [ ] Run `cd cairn && uv run --group dev pytest -s tests/test_config_and_adapters.py tests/test_server_api.py` and confirm it passes.

### Task 6: Final Verification And Documentation Sync

**Files:**
- Modify docs only if code changes affect documented behavior.

**Interfaces:**
- Consumes all previous tasks.
- Produces a verified working tree with tests passing.

- [ ] Run `cd cairn && uv run --group dev pytest -s`.
- [ ] Run `python3 -m compileall -q cairn/src/cairn cairn/tests` from repository root.
- [ ] Read relevant docs under `docs/` and update them if this round changed documented behavior.
- [ ] Inspect `git status --short` and `git diff --stat`.
