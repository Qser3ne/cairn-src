# Business Boundary Stability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enforce the documented Cairn SRC business boundary that recon writes facts only, while vuln projects own findings and reports.

**Architecture:** Keep the current FastAPI router and SQLite service boundaries. Add small project-kind guards in `server/routers/intents.py`, migrate lifecycle tests to a child vuln project, and update documentation to match the enforced API behavior.

**Tech Stack:** Python >=3.12, FastAPI, SQLite via `sqlite3`, Pydantic, pytest, uv.

## Global Constraints

- Do not introduce new runtime dependencies.
- Keep existing public route names and CLI commands unchanged.
- Use TDD for behavior changes: add failing regression tests before production code.
- Do not revert unrelated existing working-tree changes.
- Commit only after tests and compile verification complete.

---

### Task 1: Add Server Boundary Regression Tests

**Files:**
- Modify: `cairn/tests/test_server_api.py`

**Interfaces:**
- Consumes: existing `_create_recon(client: TestClient, **overrides) -> dict` and `_create_snapshot(client: TestClient, project_id: str, selected_fact_ids: list[str] | None = None) -> dict` helpers.
- Produces: tests that require Server to reject recon findings/report and keep vuln finding/report lifecycle working.

- [ ] Add `test_recon_conclude_rejects_findings` in `cairn/tests/test_server_api.py`:

```python
def test_recon_conclude_rejects_findings(client: TestClient) -> None:
    project_id = _create_recon(client)["project"]["id"]
    client.post(
        f"/projects/{project_id}/intents",
        json={"from": ["origin"], "description": "verify idor", "creator": "reasoner"},
    )

    response = client.post(
        f"/projects/{project_id}/intents/i001/conclude",
        json={
            "worker": "explorer",
            "description": "recon fact",
            "findings": [{"title": "recon should not write findings"}],
        },
    )

    assert response.status_code == 400
    detail = client.get(f"/projects/{project_id}").json()
    assert detail["findings"] == []
    assert detail["intents"][0]["to"] is None
```

- [ ] Add `test_recon_project_rejects_report_intents`:

```python
def test_recon_project_rejects_report_intents(client: TestClient) -> None:
    project_id = _create_recon(client)["project"]["id"]

    response = client.post(
        f"/projects/{project_id}/intents",
        json={
            "from": ["origin"],
            "description": "report should not exist on recon",
            "creator": "dispatcher.finding_report",
            "intent_kind": "report",
            "finding_id": "v001",
        },
    )

    assert response.status_code == 400
```

- [ ] Migrate `test_conclude_finding_lifecycle_creates_followup_and_report_intents` to create a vuln child before creating the intent:

```python
parent_id = _create_recon(client)["project"]["id"]
snapshot = _create_snapshot(client, parent_id)
child = client.post(
    f"/projects/{parent_id}/fork-vuln",
    json={"title": "vuln", "snapshot_id": snapshot["id"], "auth_mode": "authenticated", "accounts": [{"cookies": [{"name": "sid", "value": "child"}]}]},
).json()
project_id = child["project"]["id"]
```

- [ ] Add `test_report_intent_cannot_use_fact_conclude_endpoint` using a vuln child and a report intent. Assert `POST /conclude` returns `400` and the intent remains open.

- [ ] Run: `cd cairn && uv run --group dev pytest -s tests/test_server_api.py::test_recon_conclude_rejects_findings tests/test_server_api.py::test_recon_project_rejects_report_intents tests/test_server_api.py::test_report_intent_cannot_use_fact_conclude_endpoint tests/test_server_api.py::test_conclude_finding_lifecycle_creates_followup_and_report_intents`

Expected before implementation: new recon/report tests fail because the Server is too permissive.

### Task 2: Enforce Server Business Boundary

**Files:**
- Modify: `cairn/src/cairn/server/routers/intents.py`
- Test: `cairn/tests/test_server_api.py`

**Interfaces:**
- Consumes: existing `check_project_active(conn, project_id)` and intent rows returned by `get_claimable_open_intent_or_404`.
- Produces: route-level guards returning `HTTPException(400, ...)` for invalid recon findings/report paths.

- [ ] In `create_intent`, reject report intents unless `project["project_kind"] == "vuln"`:

```python
if body.intent_kind == "report":
    if project["project_kind"] != "vuln":
        raise HTTPException(400, "report intents are only supported for vuln projects")
    if not body.finding_id:
        raise HTTPException(400, "finding_id is required for report intents")
    get_finding_or_404(conn, project_id, body.finding_id)
    body.auth_scope = None
```

- [ ] In `conclude`, read `project_kind` once after the intent is loaded and reject invalid paths before writing any fact:

```python
project = conn.execute("SELECT project_kind FROM projects WHERE id = ?", (project_id,)).fetchone()
if project is None:
    raise HTTPException(404, "Project not found")
if intent["intent_kind"] == "report":
    raise HTTPException(400, "Report intents must be concluded through the report endpoint")
if project["project_kind"] == "recon" and body.findings:
    raise HTTPException(400, "recon projects cannot write findings")
```

- [ ] Reuse the loaded `project` row near the end of `conclude` when incrementing recon explore rounds.

- [ ] In `conclude_report`, reject non-vuln projects before loading the report intent:

```python
project = check_project_active(conn, project_id)
if project["project_kind"] != "vuln":
    raise HTTPException(400, "reports are only supported for vuln projects")
```

- [ ] Run: `cd cairn && uv run --group dev pytest -s tests/test_server_api.py::test_recon_conclude_rejects_findings tests/test_server_api.py::test_recon_project_rejects_report_intents tests/test_server_api.py::test_report_intent_cannot_use_fact_conclude_endpoint tests/test_server_api.py::test_conclude_finding_lifecycle_creates_followup_and_report_intents`

Expected after implementation: all selected tests pass.

### Task 3: Sync Documentation And Regression Scope

**Files:**
- Modify: `docs/architecture/overview.md`
- Modify: `docs/architecture/server-api.md`
- Modify: `docs/architecture/worker-contracts.md`
- Modify: `docs/user/src-workflow.md`
- Test: `cairn/tests/test_worker_tasks.py`

**Interfaces:**
- Consumes: Server behavior from Task 2.
- Produces: documentation that says recon writes facts only and vuln owns findings/report lifecycle.

- [ ] Update docs to state that `recon` conclude requests with findings are rejected by Server and that `report` intents are only valid for `vuln` projects.

- [ ] Run: `cd cairn && uv run --group dev pytest -s tests/test_server_api.py tests/test_worker_tasks.py`

Expected: pass.

### Task 4: Final Verification And Commit

**Files:**
- Verify all modified files.

**Interfaces:**
- Consumes: all previous tasks.
- Produces: one git commit containing current project state after verification.

- [ ] Run: `cd cairn && uv run --group dev pytest -s`

Expected: pass.

- [ ] Run from repository root: `python3 -m compileall -q cairn/src/cairn cairn/tests`

Expected: exit code 0.

- [ ] Run: `git status --short`, `git diff`, and `git log --oneline -10`.

Expected: only intended files are staged for commit.

- [ ] Commit all intended changes with a concise message:

```bash
git add .
git commit -m "fix: enforce SRC project boundaries"
```
