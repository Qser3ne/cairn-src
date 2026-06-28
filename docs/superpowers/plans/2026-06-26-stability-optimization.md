# Stability Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix Cairn SRC stability issues outside the intentionally accepted direct deploy overwrite behavior.

**Architecture:** Keep the current FastAPI/SQLite server, dispatcher, runtime, and static UI boundaries. Apply small behavior fixes with regression tests first, avoid new runtime dependencies, and do not change `scripts/deploy.sh` or its tests in this round.

**Tech Stack:** Python >=3.12, FastAPI, SQLite via `sqlite3`, Pydantic, pytest, uv, static HTML/Alpine UI, GitHub Actions YAML.

## Global Constraints

- Do not modify `scripts/deploy.sh` or `cairn/tests/test_deploy_script.py`; the direct `rsync -a --delete` production overwrite behavior is accepted.
- Do not introduce new runtime dependencies.
- Keep public route names, CLI commands, and persisted active workflow names unchanged.
- Use TDD for behavior changes: add a failing regression test before production code.
- Do not revert unrelated existing working-tree changes.
- Commit only if the user explicitly asks for a commit.

---

### Task 1: DB Migration Safety

**Files:**
- Modify: `cairn/src/cairn/server/db.py`
- Test: `cairn/tests/test_db_migrations.py`

**Interfaces:**
- Produces: `configure(path: Path) -> None` only caches `_db_path` after migration succeeds.
- Produces: safe table rebuild behavior that preserves target foreign keys and validates `PRAGMA foreign_key_check`.

- [ ] Add failing tests for configure retry after migration failure, project self-FK preservation after rebuild, and cleanup of legacy-only project columns.
- [ ] Run targeted migration tests and verify they fail for the expected reasons.
- [ ] Update `db.py` with safe configure state handling and safe project rebuild schema.
- [ ] Run `cd cairn && uv run --group dev pytest -s tests/test_db_migrations.py`.

### Task 2: Server Intent Input Stability

**Files:**
- Modify: `cairn/src/cairn/server/models.py`
- Modify: `cairn/src/cairn/server/routers/intents.py`
- Modify: `cairn/src/cairn/server/routers/projects.py`
- Test: `cairn/tests/test_server_api.py`

**Interfaces:**
- Produces: `CreateIntentRequest.from_` rejects duplicate fact ids.
- Produces: intent creation duplicate check and insert run inside one SQLite write transaction.
- Produces: retired ephemeral job fail endpoint returns `410` like claim/finish.

- [ ] Add failing tests for duplicate `from`, transaction guard during duplicate check, and retired job fail.
- [ ] Run selected server tests and verify they fail for expected reasons.
- [ ] Implement the minimal API/model/router changes.
- [ ] Run `cd cairn && uv run --group dev pytest -s tests/test_server_api.py`.

### Task 3: Heartbeat Timeout Boundary

**Files:**
- Modify: `cairn/src/cairn/dispatcher/scheduler/loop.py`
- Test: `cairn/tests/test_scheduler_logic.py`

**Interfaces:**
- Consumes: dispatcher runtime interval and server `intent_timeout` / `reason_timeout` settings.
- Produces: `_validate_server_settings()` rejects timeout values that are not greater than the heartbeat transient failure grace window.

- [ ] Add failing scheduler tests for timeouts equal to or below `interval * 2`.
- [ ] Run selected scheduler tests and verify they fail.
- [ ] Update `_validate_server_settings()` to raise with a clear message.
- [ ] Run `cd cairn && uv run --group dev pytest -s tests/test_scheduler_logic.py`.

### Task 4: Dispatcher Lease Release Fallbacks

**Files:**
- Modify: `cairn/src/cairn/dispatcher/scheduler/loop.py`
- Modify: `cairn/src/cairn/dispatcher/tasks/report.py`
- Test: `cairn/tests/test_scheduler_logic.py`
- Test: `cairn/tests/test_worker_tasks.py`

**Interfaces:**
- Produces: crashed intent tasks call `_best_effort_release(project_id, intent_id, worker_name)`.
- Produces: crashed reason tasks call `_best_effort_release_reason(project_id, worker_name, task_mode)`.
- Produces: `run_report_task()` releases report intents on unexpected exceptions.

- [ ] Add failing tests for scheduler crash lease release and report unexpected exception release.
- [ ] Run selected tests and verify they fail.
- [ ] Implement minimal release fallback logic.
- [ ] Run `cd cairn && uv run --group dev pytest -s tests/test_scheduler_logic.py tests/test_worker_tasks.py`.

### Task 5: Docker Exec Kill Race

**Files:**
- Modify: `cairn/src/cairn/dispatcher/runtime/process.py`
- Test: `cairn/tests/test_runtime_logic.py`

**Interfaces:**
- Produces: `ManagedProcess.kill()` retries short-lived inspect states before giving up.

- [ ] Add a failing runtime test where `exec_inspect()` first returns not running and then running with a pid.
- [ ] Run the selected test and verify it fails.
- [ ] Implement bounded inspect/kill retry without changing public process APIs.
- [ ] Run `cd cairn && uv run --group dev pytest -s tests/test_runtime_logic.py`.

### Task 6: Container Cleanup Exception Isolation

**Files:**
- Modify: `cairn/src/cairn/dispatcher/scheduler/loop.py`
- Test: `cairn/tests/test_scheduler_logic.py`

**Interfaces:**
- Produces: cleanup precheck exceptions are logged and skipped for the current scheduler round.

- [ ] Add failing scheduler tests for completed/stopped/orphan cleanup precheck exceptions.
- [ ] Run selected scheduler tests and verify they fail.
- [ ] Wrap cleanup prechecks in safe helpers.
- [ ] Run `cd cairn && uv run --group dev pytest -s tests/test_scheduler_logic.py`.

### Task 7: Worker Output JSON Selection

**Files:**
- Modify: `cairn/src/cairn/dispatcher/output_parser.py`
- Test: `cairn/tests/test_contracts_and_drivers.py`

**Interfaces:**
- Produces: explicit fenced JSON blocks are preferred over earlier prose/example JSON.
- Preserves: raw mixed-text JSON extraction when no fenced block exists.

- [ ] Add a failing parser test with an earlier example JSON and a later final fenced JSON.
- [ ] Run selected contract test and verify it fails.
- [ ] Reorder candidate segment selection.
- [ ] Run `cd cairn && uv run --group dev pytest -s tests/test_contracts_and_drivers.py`.

### Task 8: Protocol GET Parse Errors

**Files:**
- Modify: `cairn/src/cairn/dispatcher/protocol/client.py`
- Modify: `cairn/src/cairn/dispatcher/scheduler/loop.py`
- Test: `cairn/tests/test_protocol_and_startup.py`
- Test: `cairn/tests/test_scheduler_logic.py`

**Interfaces:**
- Produces: malformed JSON/schema from GET endpoints raises `ProtocolError` with status and response text.
- Produces: dispatcher main loop treats `ProtocolError` as retryable like request failures.

- [ ] Add failing protocol tests for malformed `GET /projects` JSON and schema drift.
- [ ] Add a scheduler test that `ProtocolError` is retryable in non-once mode.
- [ ] Run selected tests and verify they fail.
- [ ] Implement safe GET parsing and scheduler catch.
- [ ] Run `cd cairn && uv run --group dev pytest -s tests/test_protocol_and_startup.py tests/test_scheduler_logic.py`.

### Task 9: Static UI Auth Scope Alignment

**Files:**
- Modify: `cairn/src/cairn/server/static/index.html`
- Create: `cairn/tests/test_static_ui.py`

**Interfaces:**
- Produces: New Project UI exposes `dual` auth mode and sends accounts for `dual` and `authenticated` projects.
- Produces: validation intent form exposes auth scope selection when the current project auth mode is `dual`.

- [ ] Add static UI tests that assert dual UI labels and request body logic exist.
- [ ] Run the static UI tests and verify they fail.
- [ ] Update `index.html` with the minimal UI and request logic changes.
- [ ] Run `cd cairn && uv run --group dev pytest -s tests/test_static_ui.py`.

### Task 10: CI Documentation Consistency

**Files:**
- Create: `.github/workflows/python-ci.yml`
- Modify: `docs/development/testing.md` only if the workflow behavior differs from the documented command.

**Interfaces:**
- Produces: Python CI runs pytest and compileall for push and pull request.

- [ ] Add a test or file existence check only if an existing test pattern is present; otherwise create the workflow directly because this is configuration.
- [ ] Add `.github/workflows/python-ci.yml` with `uv` setup, `uv sync --group dev`, pytest, and compileall.
- [ ] Run `cd cairn && uv run --group dev pytest -s`.
- [ ] Run `python3 -m compileall -q cairn/src/cairn cairn/tests`.
- [ ] Inspect `git diff --stat`.
