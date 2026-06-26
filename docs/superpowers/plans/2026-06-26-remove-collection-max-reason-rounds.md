# Remove Collection Max Reason Rounds Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the unused `collection_max_reason_rounds` setting from API, DB schema, YAML export, UI, tests, and fixtures.

**Architecture:** Keep only real collection counters on projects: `collection_reason_rounds`, `collection_explore_rounds`, and `collection_stable_rounds`. The server no longer accepts, stores, returns, or exports a collection max value; existing SQLite databases are rebuilt to drop the stale column when present.

**Tech Stack:** Python, FastAPI, Pydantic, SQLite, pytest, Alpine.js static HTML.

## Global Constraints

- Do not preserve compatibility for `collection_max_reason_rounds`; requests that send it should fail as an unknown field.
- Do not remove actual collection counters or change collection scheduling behavior.
- Do not touch unrelated dirty files: `docs/development/testing.md`, `docs/ops/deployment-release.md`, `scripts/deploy.sh`, or `cairn/tests/test_deploy_script.py` unless explicitly needed.
- Do not commit unless the user explicitly asks.

---

### Task 1: Remove API And DB Field

**Files:**
- Modify: `cairn/src/cairn/server/models.py`
- Modify: `cairn/src/cairn/server/routers/projects.py`
- Modify: `cairn/src/cairn/server/services.py`
- Modify: `cairn/src/cairn/server/db.py`
- Modify: `cairn/tests/test_db_migrations.py`

**Interfaces:**
- Consumes: existing `ProjectMeta`, `CreateProjectRequest`, project row mapping, and schema migration helpers.
- Produces: project models and DB schema without `collection_max_reason_rounds`.

- [ ] **Step 1: Update API tests first**

Change `cairn/tests/test_server_api.py::test_create_project_defaults_to_vuln_and_forbids_old_fields` so it asserts `collection_max_reason_rounds` is absent and that sending it returns `422`. Update `cairn/tests/test_db_migrations.py` so new databases do not expose the column and legacy databases containing only `collection_max_reason_rounds` are rebuilt without it while preserving collection counters.

```python
assert "collection_max_reason_rounds" not in payload["project"]

for field, value in (
    ("mode", "src"),
    ("bootstrap_enabled", False),
    ("goal", "finish"),
    ("recon_max_reason_rounds", 8),
    ("collection_max_reason_rounds", 8),
):
    response = client.post(
        "/projects",
        json={"title": "legacy", "origin": "start", field: value},
    )
    assert response.status_code == 422
```

- [ ] **Step 2: Run the focused failing test**

Run: `uv run --group dev pytest -s tests/test_server_api.py::test_create_project_defaults_to_vuln_and_forbids_old_fields tests/test_db_migrations.py`

Expected: FAIL because the response still includes `collection_max_reason_rounds`, create still accepts it, and legacy schemas still retain the column.

- [ ] **Step 3: Remove model fields and row mappings**

Remove `collection_max_reason_rounds` from `ProjectMeta` and `CreateProjectRequest` in `models.py`. Remove corresponding assignments in `projects.py::_summary_from_row`, `services.py::project_meta_from_row`, and `projects.py::create_project` insert columns/values.

- [ ] **Step 4: Remove schema column and migration copy**

In `db.py`, remove `collection_max_reason_rounds INTEGER` from the `SCHEMA` projects table and the `projects_new` rebuild table. Remove the migration branch that adds `collection_max_reason_rounds` and copies `recon_max_reason_rounds`. Ensure the rebuild path copies only retained columns.

- [ ] **Step 5: Run the focused test again**

Run: `uv run --group dev pytest -s tests/test_server_api.py::test_create_project_defaults_to_vuln_and_forbids_old_fields tests/test_db_migrations.py`

Expected: PASS.

### Task 2: Remove Export And Fixture Field

**Files:**
- Modify: `cairn/src/cairn/server/routers/export.py`
- Modify: `cairn/tests/test_server_api.py`
- Modify: `cairn/tests/test_collection_prompt_fixtures.py`
- Modify: `cairn/tests/fixtures/prompts/collection/initial_origin.yaml`
- Modify: `cairn/tests/fixtures/prompts/collection/ready_for_judge.yaml`
- Modify: `cairn/tests/fixtures/prompts/collection/with_open_intents.yaml`

**Interfaces:**
- Consumes: YAML export `collection` object and prompt fixture `collection` object.
- Produces: YAML collection objects without `max_reason_rounds`.

- [ ] **Step 1: Update export and fixture tests first**

In `test_collection_rounds_do_not_stop_project_at_reason_limit`, stop passing `collection_max_reason_rounds=2`, remove the `max_reason_rounds` assertion, and keep assertions for `reason_rounds` and `stable_rounds`.

In `test_collection_prompt_fixtures.py`, change the required collection keys to:

```python
assert {"reason_rounds", "explore_rounds", "stable_rounds"} <= data["collection"].keys()
assert "max_reason_rounds" not in data["collection"]
```

- [ ] **Step 2: Run focused failing tests**

Run: `uv run --group dev pytest -s tests/test_server_api.py::test_collection_rounds_do_not_stop_project_at_reason_limit tests/test_collection_prompt_fixtures.py`

Expected: FAIL until export code and fixtures stop including `max_reason_rounds`.

- [ ] **Step 3: Remove export field**

In `export.py::_export_yaml`, delete:

```python
"max_reason_rounds": proj["collection_max_reason_rounds"],
```

- [ ] **Step 4: Update fixtures**

Delete `max_reason_rounds: 5` from each collection prompt YAML fixture.

- [ ] **Step 5: Run focused tests again**

Run: `uv run --group dev pytest -s tests/test_server_api.py::test_collection_rounds_do_not_stop_project_at_reason_limit tests/test_collection_prompt_fixtures.py`

Expected: PASS.

### Task 3: Remove UI Input And Payload

**Files:**
- Modify: `cairn/src/cairn/server/static/index.html`
- Modify: `cairn/tests/test_mock_end_to_end.py`

**Interfaces:**
- Consumes: project API responses without `collection_max_reason_rounds`.
- Produces: UI that creates projects without the removed field and displays actual collection reason rounds only.

- [ ] **Step 1: Update integration test first**

In `test_mock_scheduler_collection_stable_does_not_stop_project`, stop passing `collection_max_reason_rounds=2` to `_create_project`. Keep the assertions that the project remains active and `collection_reason_rounds == 2`.

- [ ] **Step 2: Run the focused integration test**

Run: `uv run --group dev pytest -s tests/test_mock_end_to_end.py::test_mock_scheduler_collection_stable_does_not_stop_project`

Expected: PASS or FAIL only from existing implementation; this test should no longer rely on the removed request field.

- [ ] **Step 3: Remove New Project UI field**

In `index.html`, delete the label/input block for `Collection max reason rounds`, remove `collection_max_reason_rounds: 8` from `newProject`, remove reset/default occurrences, and remove the payload line:

```javascript
collection_max_reason_rounds: Number(this.newProject.collection_max_reason_rounds) || 8,
```

- [ ] **Step 4: Change collection round summary display**

Replace `collectionRoundSummary()` with:

```javascript
collectionRoundSummary() {
  const project = this.project?.project || {};
  return `${project.collection_reason_rounds || 0} reason rounds`;
},
```

- [ ] **Step 5: Search for stale UI references**

Run: `rg "collection_max_reason_rounds|Collection max reason rounds|max_reason_rounds" cairn/src/cairn/server/static/index.html`

Expected: no matches.

### Task 4: Update Documentation And Run Final Verification

**Files:**
- Modify: `docs/user/src-workflow.md`

**Interfaces:**
- Consumes: documentation statement that currently advertises `collection_max_reason_rounds`.
- Produces: docs that no longer mention a removed control.

- [ ] **Step 1: Remove user docs mention**

Delete this bullet from `docs/user/src-workflow.md`:

```markdown
- 可通过 `collection_max_reason_rounds` 控制 collection reason 的自动扩展轮次。
```

- [ ] **Step 2: Search for stale references**

Run: `rg "collection_max_reason_rounds|Collection max reason rounds|max_reason_rounds" cairn/src cairn/tests docs/user/src-workflow.md`

Expected: no UI/export/model/request references. Remaining matches are limited to DB legacy migration code that detects and drops the old column, plus tests that assert the old field is absent or rejected.

- [ ] **Step 3: Run targeted verification**

Run: `uv run --group dev pytest -s tests/test_server_api.py tests/test_mock_end_to_end.py tests/test_collection_prompt_fixtures.py`

Expected: PASS.

- [ ] **Step 4: Run prompt contract verification**

Run: `uv run --group dev pytest -s tests/test_prompt_contracts.py tests/test_collection_prompt_fixtures.py`

Expected: PASS.

- [ ] **Step 5: Inspect diff**

Run: `git diff -- cairn/src/cairn/server/models.py cairn/src/cairn/server/routers/projects.py cairn/src/cairn/server/services.py cairn/src/cairn/server/db.py cairn/src/cairn/server/routers/export.py cairn/src/cairn/server/static/index.html cairn/tests/test_server_api.py cairn/tests/test_mock_end_to_end.py cairn/tests/test_collection_prompt_fixtures.py cairn/tests/fixtures/prompts/collection/initial_origin.yaml cairn/tests/fixtures/prompts/collection/ready_for_judge.yaml cairn/tests/fixtures/prompts/collection/with_open_intents.yaml docs/user/src-workflow.md docs/superpowers/specs/2026-06-26-remove-collection-max-reason-rounds-design.md docs/superpowers/plans/2026-06-26-remove-collection-max-reason-rounds.md`

Expected: diff only contains the removal and docs/plan changes described above.

## Self-Review

- Spec coverage: API, DB, YAML export, UI, tests, fixtures, and docs are covered.
- Placeholder scan: no placeholders or deferred decisions remain.
- Type consistency: all references use retained project counters only; the removed field is not used by later tasks.
