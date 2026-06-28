# Worker Scheduling Settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add global settings and dispatcher policy so projects run an initial collection warmup, then run collection and vulnerability validation/report work in parallel while collection work obeys its own global cap.

**Architecture:** Extend the existing `/settings` API and SQLite `settings` row with `initial_collection_rounds` and `collection_worker_limit`. The dispatcher reads these settings each scheduler tick, gates validation/report until collection warmup is satisfied or converged, and applies a global cap to `collection_reason` plus `collection_explore`. Existing five task types remain the unit of worker selection.

**Tech Stack:** Python 3.12+, FastAPI, Pydantic, SQLite, pytest, static Alpine.js HTML, YAML dispatcher config.

## Global Constraints

- Do not introduce a sixth task type or a separate worker backend class.
- Do not remove support for workers that handle multiple task types.
- Do not make these settings per-project.
- Do not change report writeback, finding lifecycle, prompt contracts, or worker JSON contracts beyond documentation/examples.
- `initial_collection_rounds` default is `5` and accepts `0` to disable warmup.
- `collection_worker_limit` default is `1` and must be at least `1`.
- Warmup uses `collection_explore_rounds`, not `collection_reason_rounds`, as the primary counter.
- Early validation/report is allowed when collection cannot produce or run more work before the threshold.
- Do not revert or rewrite unrelated dirty worktree changes.
- Do not commit unless the user explicitly asks.

---

## File Structure

- `cairn/src/cairn/server/models.py`: extend the `Settings` response/request model with the two scheduler settings.
- `cairn/src/cairn/server/db.py`: add schema defaults and migration helper for existing `settings` tables.
- `cairn/src/cairn/server/routers/settings.py`: read and write all four server settings.
- `cairn/src/cairn/dispatcher/protocol/client.py`: continues to parse `Settings`; no new method is required.
- `cairn/src/cairn/dispatcher/scheduler/loop.py`: load settings per tick, validate timeout settings, enforce warmup and collection cap.
- `cairn/src/cairn/server/static/index.html`: expose the two settings in the existing Server Settings modal and load/save them.
- `dispatch.example.yaml`: show five logical worker entries, one per task type, while preserving schema validity.
- `README.md`, `docs/architecture/dispatcher.md`, `docs/user/src-workflow.md`: document settings and scheduling behavior.
- `cairn/tests/test_db_migrations.py`: cover new settings columns and migration.
- `cairn/tests/test_server_api.py`: cover API and UI settings behavior.
- `cairn/tests/test_scheduler_logic.py`: cover warmup gate, early transition, and collection cap.
- `cairn/tests/test_config_and_adapters.py`: existing schema test should continue to load `dispatch.example.yaml`; update only if the example structure changes assertions.

---

### Task 1: Persist And Expose New Settings

**Files:**
- Modify: `cairn/src/cairn/server/models.py`
- Modify: `cairn/src/cairn/server/db.py`
- Modify: `cairn/src/cairn/server/routers/settings.py`
- Test: `cairn/tests/test_db_migrations.py`
- Test: `cairn/tests/test_server_api.py`

**Interfaces:**
- Consumes: existing `Settings`, `get_settings()`, `update_settings()`, and SQLite `settings` table.
- Produces: `Settings(intent_timeout: int, reason_timeout: int, initial_collection_rounds: int, collection_worker_limit: int)` used by the dispatcher and UI.

- [ ] **Step 1: Add failing API and DB tests**

Add these tests to `cairn/tests/test_server_api.py` near existing settings/static UI tests:

```python
def test_settings_api_exposes_collection_scheduling_defaults_and_updates(client: TestClient) -> None:
    defaults = client.get("/settings")

    assert defaults.status_code == 200
    assert defaults.json() == {
        "intent_timeout": 15,
        "reason_timeout": 15,
        "initial_collection_rounds": 5,
        "collection_worker_limit": 1,
    }

    updated = client.put(
        "/settings",
        json={
            "intent_timeout": 20,
            "reason_timeout": 21,
            "initial_collection_rounds": 3,
            "collection_worker_limit": 2,
        },
    )

    assert updated.status_code == 200, updated.text
    assert updated.json() == {
        "intent_timeout": 20,
        "reason_timeout": 21,
        "initial_collection_rounds": 3,
        "collection_worker_limit": 2,
    }
    assert client.get("/settings").json() == updated.json()


def test_settings_api_validates_collection_scheduling_bounds(client: TestClient) -> None:
    invalid_rounds = client.put(
        "/settings",
        json={
            "intent_timeout": 10,
            "reason_timeout": 10,
            "initial_collection_rounds": -1,
            "collection_worker_limit": 1,
        },
    )
    invalid_limit = client.put(
        "/settings",
        json={
            "intent_timeout": 10,
            "reason_timeout": 10,
            "initial_collection_rounds": 0,
            "collection_worker_limit": 0,
        },
    )

    assert invalid_rounds.status_code == 422
    assert invalid_limit.status_code == 422
```

Update `cairn/tests/test_server_api.py::test_write_requests_reject_unknown_fields` so the settings request includes valid new fields while still sending `surprise`:

```python
settings = client.put(
    "/settings",
    json={
        "intent_timeout": 10,
        "reason_timeout": 10,
        "initial_collection_rounds": 5,
        "collection_worker_limit": 1,
        "surprise": True,
    },
)
```

Add these checks to `cairn/tests/test_db_migrations.py::test_new_database_has_src_only_schema` after the existing column collection:

```python
settings_columns = {row["name"] for row in conn.execute("PRAGMA table_info(settings)")}
settings = conn.execute("SELECT * FROM settings WHERE rowid = 1").fetchone()
```

Add these assertions in the same test:

```python
assert {"initial_collection_rounds", "collection_worker_limit"} <= settings_columns
assert settings["initial_collection_rounds"] == 5
assert settings["collection_worker_limit"] == 1
```

Add this migration test to `cairn/tests/test_db_migrations.py`:

```python
def test_legacy_settings_table_gains_collection_scheduling_columns(tmp_path, monkeypatch) -> None:
    path = tmp_path / "legacy-settings.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE settings (
                intent_timeout INTEGER NOT NULL DEFAULT 15,
                reason_timeout INTEGER NOT NULL DEFAULT 15
            );
            INSERT INTO settings (rowid, intent_timeout, reason_timeout) VALUES (1, 30, 31);
            """
        )

    monkeypatch.setattr(db, "_db_path", None)
    db.configure(path)

    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM settings WHERE rowid = 1").fetchone()

    assert row["intent_timeout"] == 30
    assert row["reason_timeout"] == 31
    assert row["initial_collection_rounds"] == 5
    assert row["collection_worker_limit"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run from `cairn/`:

```bash
uv run --group dev pytest -s tests/test_server_api.py::test_settings_api_exposes_collection_scheduling_defaults_and_updates tests/test_server_api.py::test_settings_api_validates_collection_scheduling_bounds tests/test_server_api.py::test_write_requests_reject_unknown_fields tests/test_db_migrations.py::test_new_database_has_src_only_schema tests/test_db_migrations.py::test_legacy_settings_table_gains_collection_scheduling_columns
```

Expected: FAIL because `Settings` rejects the new fields and the database table lacks the new columns.

- [ ] **Step 3: Extend `Settings` model**

Change `cairn/src/cairn/server/models.py` `Settings` to:

```python
class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent_timeout: int = Field(ge=5)
    reason_timeout: int = Field(ge=5)
    initial_collection_rounds: int = Field(ge=0)
    collection_worker_limit: int = Field(ge=1)
```

- [ ] **Step 4: Extend settings schema and migration**

In `cairn/src/cairn/server/db.py`, update the `settings` schema block to:

```sql
CREATE TABLE IF NOT EXISTS settings (
    intent_timeout INTEGER NOT NULL DEFAULT 15,
    reason_timeout INTEGER NOT NULL DEFAULT 15,
    initial_collection_rounds INTEGER NOT NULL DEFAULT 5,
    collection_worker_limit INTEGER NOT NULL DEFAULT 1
);

INSERT OR IGNORE INTO settings (
    rowid,
    intent_timeout,
    reason_timeout,
    initial_collection_rounds,
    collection_worker_limit
) VALUES (1, 15, 15, 5, 1);
```

Add this helper in `db.py` after `configure()` or near other `_ensure_*` helpers:

```python
def _ensure_settings_columns(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(settings)")}
    if "initial_collection_rounds" not in columns:
        conn.execute("ALTER TABLE settings ADD COLUMN initial_collection_rounds INTEGER NOT NULL DEFAULT 5")
    if "collection_worker_limit" not in columns:
        conn.execute("ALTER TABLE settings ADD COLUMN collection_worker_limit INTEGER NOT NULL DEFAULT 1")
```

Call it in `configure()` immediately after `conn.executescript(SCHEMA)`:

```python
conn.executescript(SCHEMA)
_ensure_settings_columns(conn)
_ensure_src_only_project_columns(conn)
```

- [ ] **Step 5: Read and write all settings fields**

Replace `cairn/src/cairn/server/routers/settings.py` with equivalent logic that selects and updates all fields:

```python
@router.get("/settings", response_model=Settings)
def get_settings():
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT intent_timeout,
                   reason_timeout,
                   initial_collection_rounds,
                   collection_worker_limit
            FROM settings
            WHERE rowid = 1
            """
        ).fetchone()
        return Settings(
            intent_timeout=row["intent_timeout"],
            reason_timeout=row["reason_timeout"],
            initial_collection_rounds=row["initial_collection_rounds"],
            collection_worker_limit=row["collection_worker_limit"],
        )


@router.put("/settings", response_model=Settings)
def update_settings(body: Settings):
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE settings
            SET intent_timeout = ?,
                reason_timeout = ?,
                initial_collection_rounds = ?,
                collection_worker_limit = ?
            WHERE rowid = 1
            """,
            (
                body.intent_timeout,
                body.reason_timeout,
                body.initial_collection_rounds,
                body.collection_worker_limit,
            ),
        )
        return body
```

- [ ] **Step 6: Run focused tests again**

Run from `cairn/`:

```bash
uv run --group dev pytest -s tests/test_server_api.py::test_settings_api_exposes_collection_scheduling_defaults_and_updates tests/test_server_api.py::test_settings_api_validates_collection_scheduling_bounds tests/test_server_api.py::test_write_requests_reject_unknown_fields tests/test_db_migrations.py::test_new_database_has_src_only_schema tests/test_db_migrations.py::test_legacy_settings_table_gains_collection_scheduling_columns
```

Expected: PASS.

---

### Task 2: Enforce Warmup And Collection Cap In Scheduler

**Files:**
- Modify: `cairn/src/cairn/dispatcher/scheduler/loop.py`
- Test: `cairn/tests/test_scheduler_logic.py`

**Interfaces:**
- Consumes: `Settings.initial_collection_rounds`, `Settings.collection_worker_limit`, `ProjectMeta.collection_explore_rounds`, `ProjectMeta.collection_reason_rounds`.
- Produces: `DispatcherLoop.server_settings: Settings | None`, `_current_server_settings() -> Settings`, `_collection_capacity_available() -> bool`, `_collection_warmup_complete(project: ProjectDetail) -> bool`.

- [ ] **Step 1: Add scheduler tests for settings access and warmup**

Update imports in `cairn/tests/test_scheduler_logic.py` to continue using `Settings` from `cairn.server.models`.

Update `_loop()` in `cairn/tests/test_scheduler_logic.py` to initialize scheduler settings:

```python
loop.server_settings = Settings(
    intent_timeout=15,
    reason_timeout=15,
    initial_collection_rounds=5,
    collection_worker_limit=1,
)
```

Add this helper near `_prepare_real_dispatch()`:

```python
def _set_scheduler_settings(
    loop: DispatcherLoop,
    *,
    initial_collection_rounds: int = 5,
    collection_worker_limit: int = 1,
) -> None:
    loop.server_settings = Settings(
        intent_timeout=15,
        reason_timeout=15,
        initial_collection_rounds=initial_collection_rounds,
        collection_worker_limit=collection_worker_limit,
    )
```

Add these tests to `cairn/tests/test_scheduler_logic.py` near the existing dispatch-order tests:

```python
def test_collection_warmup_dispatches_collection_before_validation_and_report() -> None:
    loop = _loop()
    loop.config = make_config()
    _set_scheduler_settings(loop, initial_collection_rounds=5, collection_worker_limit=2)
    loop.futures = {}
    collection = make_intent("i001")
    collection.worker = None
    collection.task_mode = "collection"
    collection.created_at = "2026-01-01T00:00:01Z"
    validation = make_intent("i002")
    validation.worker = None
    validation.task_mode = "validation"
    validation.created_at = "2026-01-01T00:00:03Z"
    report = make_intent("i003")
    report.worker = None
    report.intent_kind = "report"
    report.task_mode = "report"
    report.created_at = "2026-01-01T00:00:04Z"
    project = make_project(intents=[collection, validation, report])
    project.project.collection_explore_rounds = 2
    project.project.collection_reason_rounds = 2
    loop.container_manager = type("Containers", (), {"container_name": lambda _self, project_id: project_id})()
    loop.client = type(
        "Client",
        (),
        {
            "get_project": lambda _self, _project_id: project,
            "export_project": lambda _self, _project_id: "graph",
        },
    )()
    dispatched: list[tuple[str, str]] = []
    loop._dispatch_report = lambda _project, _graph, intent: dispatched.append(("report", intent.id)) or True
    loop._dispatch_explore = lambda _project, _graph, intent: dispatched.append((intent.task_mode, intent.id)) or True

    assert loop._try_dispatch_project(_summary("proj_001", "active"))

    assert dispatched == [("collection", "i001")]


def test_collection_warmup_blocks_validation_when_no_collection_round_has_run() -> None:
    loop = _loop()
    loop.config = make_config()
    _set_scheduler_settings(loop, initial_collection_rounds=5, collection_worker_limit=2)
    validation = make_intent("i001")
    validation.worker = None
    validation.task_mode = "validation"
    project = make_project(intents=[validation])
    project.project.collection_explore_rounds = 0
    project.project.collection_reason_rounds = 0
    loop.container_manager = type("Containers", (), {"container_name": lambda _self, project_id: project_id})()
    loop.client = type(
        "Client",
        (),
        {
            "get_project": lambda _self, _project_id: project,
            "export_project": lambda _self, _project_id: "graph",
        },
    )()
    dispatched: list[tuple[str, str]] = []
    loop._dispatch_reason = lambda _project, _graph, trigger, task_mode: dispatched.append((task_mode, trigger)) or True
    loop._dispatch_explore = lambda _project, _graph, intent: dispatched.append((intent.task_mode, intent.id)) or True

    assert loop._try_dispatch_project(_summary("proj_001", "active"))

    assert dispatched == [("collection", "initial")]


def test_collection_warmup_allows_validation_after_explore_threshold() -> None:
    loop = _loop()
    loop.config = make_config()
    _set_scheduler_settings(loop, initial_collection_rounds=5, collection_worker_limit=1)
    validation = make_intent("i001")
    validation.worker = None
    validation.task_mode = "validation"
    project = make_project(intents=[validation])
    project.project.collection_explore_rounds = 5
    project.project.collection_reason_rounds = 1
    loop.container_manager = type("Containers", (), {"container_name": lambda _self, project_id: project_id})()
    loop.client = type(
        "Client",
        (),
        {
            "get_project": lambda _self, _project_id: project,
            "export_project": lambda _self, _project_id: "graph",
        },
    )()
    dispatched: list[tuple[str, str]] = []
    loop._dispatch_explore = lambda _project, _graph, intent: dispatched.append((intent.task_mode, intent.id)) or True

    assert loop._try_dispatch_project(_summary("proj_001", "active"))

    assert dispatched == [("validation", "i001")]


def test_collection_warmup_allows_early_validation_when_collection_converged() -> None:
    loop = _loop()
    loop.config = make_config()
    _set_scheduler_settings(loop, initial_collection_rounds=5, collection_worker_limit=1)
    validation = make_intent("i001")
    validation.worker = None
    validation.task_mode = "validation"
    project = make_project(intents=[validation])
    project.project.collection_explore_rounds = 1
    project.project.collection_reason_rounds = 2
    loop.reason_checkpoints[("proj_001", "collection")] = ReasonCheckpoint(
        fact_count=len(project.facts),
        hint_count=len(project.hints),
        open_intent_count=0,
    )
    loop.container_manager = type("Containers", (), {"container_name": lambda _self, project_id: project_id})()
    loop.client = type(
        "Client",
        (),
        {
            "get_project": lambda _self, _project_id: project,
            "export_project": lambda _self, _project_id: "graph",
        },
    )()
    dispatched: list[tuple[str, str]] = []
    loop._dispatch_explore = lambda _project, _graph, intent: dispatched.append((intent.task_mode, intent.id)) or True

    assert loop._try_dispatch_project(_summary("proj_001", "active"))

    assert dispatched == [("validation", "i001")]
```

- [ ] **Step 2: Add scheduler tests for collection cap**

Add these tests to `cairn/tests/test_scheduler_logic.py`:

```python
def test_collection_worker_limit_blocks_collection_dispatch_globally() -> None:
    loop = _loop()
    loop.config = make_config()
    _set_scheduler_settings(loop, initial_collection_rounds=5, collection_worker_limit=1)
    loop.futures = {
        Future(): RunningTask("other", "collection_explore", "worker-a", TaskCancellation(), intent_id="i999")
    }
    collection = make_intent("i001")
    collection.worker = None
    collection.task_mode = "collection"
    project = make_project(intents=[collection])
    project.project.collection_explore_rounds = 0
    project.project.collection_reason_rounds = 1
    loop.container_manager = type("Containers", (), {"container_name": lambda _self, project_id: project_id})()
    loop.client = type(
        "Client",
        (),
        {
            "get_project": lambda _self, _project_id: project,
            "export_project": lambda _self, _project_id: "graph",
        },
    )()
    dispatched: list[str] = []
    loop._dispatch_explore = lambda *_args: dispatched.append("explore") or True

    assert not loop._try_dispatch_project(_summary("proj_001", "active"))

    assert dispatched == []


def test_collection_worker_limit_does_not_block_validation_after_warmup() -> None:
    loop = _loop()
    loop.config = make_config()
    _set_scheduler_settings(loop, initial_collection_rounds=5, collection_worker_limit=1)
    loop.futures = {
        Future(): RunningTask("other", "collection_reason", "worker-a", TaskCancellation())
    }
    validation = make_intent("i001")
    validation.worker = None
    validation.task_mode = "validation"
    project = make_project(intents=[validation])
    project.project.collection_explore_rounds = 5
    loop.container_manager = type("Containers", (), {"container_name": lambda _self, project_id: project_id})()
    loop.client = type(
        "Client",
        (),
        {
            "get_project": lambda _self, _project_id: project,
            "export_project": lambda _self, _project_id: "graph",
        },
    )()
    dispatched: list[tuple[str, str]] = []
    loop._dispatch_explore = lambda _project, _graph, intent: dispatched.append((intent.task_mode, intent.id)) or True

    assert loop._try_dispatch_project(_summary("proj_001", "active"))

    assert dispatched == [("validation", "i001")]
```

- [ ] **Step 3: Run tests to verify they fail**

Run from `cairn/`:

```bash
uv run --group dev pytest -s tests/test_scheduler_logic.py::test_collection_warmup_dispatches_collection_before_validation_and_report tests/test_scheduler_logic.py::test_collection_warmup_blocks_validation_when_no_collection_round_has_run tests/test_scheduler_logic.py::test_collection_warmup_allows_validation_after_explore_threshold tests/test_scheduler_logic.py::test_collection_warmup_allows_early_validation_when_collection_converged tests/test_scheduler_logic.py::test_collection_worker_limit_blocks_collection_dispatch_globally tests/test_scheduler_logic.py::test_collection_worker_limit_does_not_block_validation_after_warmup
```

Expected: FAIL because the scheduler does not yet know the new settings or warmup/cap rules.

- [ ] **Step 4: Load settings per scheduler tick**

In `cairn/src/cairn/dispatcher/scheduler/loop.py`, add `Settings` to the import from `cairn.server.models`:

```python
from cairn.server.models import Intent, ProjectDetail, ProjectSummary, Settings, TaskMode
```

In `DispatcherLoop.__init__`, add:

```python
self.server_settings: Settings | None = None
```

In `DispatcherLoop.run()`, fetch settings before project listing and reuse them for timeout validation:

```python
settings = self.client.get_settings()
self.server_settings = settings
if not self._settings_checked:
    self._validate_server_settings(settings)
    self._settings_checked = True
self._reap_futures()
```

Change `_validate_server_settings` to accept an optional settings object:

```python
def _validate_server_settings(self, settings: Settings | None = None) -> None:
    settings = settings or self.client.get_settings()
    interval = self.config.runtime.interval
    heartbeat_grace = max(interval, interval * HEARTBEAT_FAILURE_GRACE_MULTIPLIER)
    for name, value in (("intent_timeout", settings.intent_timeout), ("reason_timeout", settings.reason_timeout)):
        if value <= heartbeat_grace:
            raise RuntimeError(
                f"server {name}={value}s must be greater than heartbeat grace={heartbeat_grace}s "
                f"for dispatcher interval={interval}s"
            )
        LOG.info(
            "server setting validated %s=%ss interval=%ss",
            name,
            value,
            interval,
        )
```

Add this helper in `DispatcherLoop` near other small scheduler helpers:

```python
def _current_server_settings(self) -> Settings:
    if self.server_settings is not None:
        return self.server_settings
    self.server_settings = self.client.get_settings()
    return self.server_settings
```

- [ ] **Step 5: Add collection cap helpers**

Add these helpers in `DispatcherLoop` near `_worker_counts()`:

```python
def _is_collection_task_type(self, task_type: str) -> bool:
    return task_type in ("collection_reason", "collection_explore")


def _running_collection_task_count(self) -> int:
    return sum(1 for task in self.futures.values() if self._is_collection_task_type(task.task_type))


def _collection_capacity_available(self) -> bool:
    settings = self._current_server_settings()
    return self._running_collection_task_count() < settings.collection_worker_limit
```

In `_dispatch_reason`, after `task_type = self._reason_task_type(task_mode)` and before `_select_worker`, add:

```python
if self._is_collection_task_type(task_type) and not self._collection_capacity_available():
    self._log_changed(
        f"project:{project.project.id}:collection_limit:{task_type}",
        logging.INFO,
        "skip %s project=%s because collection_worker_limit reached running_collection_tasks=%s limit=%s",
        task_type,
        project.project.id,
        self._running_collection_task_count(),
        self._current_server_settings().collection_worker_limit,
    )
    return False
```

In `_dispatch_explore`, compute `task_type = self._explore_task_type(intent.task_mode)` before leasing accounts and add the same cap check before account leasing:

```python
task_type = self._explore_task_type(intent.task_mode)
if self._is_collection_task_type(task_type) and not self._collection_capacity_available():
    self._log_changed(
        f"project:{project.project.id}:collection_limit:{task_type}",
        logging.INFO,
        "skip %s project=%s intent=%s because collection_worker_limit reached running_collection_tasks=%s limit=%s",
        task_type,
        project.project.id,
        intent.id,
        self._running_collection_task_count(),
        self._current_server_settings().collection_worker_limit,
    )
    return False
```

Remove the later duplicate `task_type = self._explore_task_type(intent.task_mode)` line in `_dispatch_explore`.

- [ ] **Step 6: Add warmup helpers**

Add these helpers in `DispatcherLoop` near `_is_initial_project()`:

```python
def _collection_warmup_complete(self, project: ProjectDetail) -> bool:
    settings = self._current_server_settings()
    if settings.initial_collection_rounds <= 0:
        return True
    if project.project.collection_explore_rounds >= settings.initial_collection_rounds:
        return True
    return self._collection_warmup_converged(project)


def _collection_warmup_converged(self, project: ProjectDetail) -> bool:
    if project.project.collection_reason_rounds <= 0:
        return False
    if any(intent.to is None and intent.task_mode == "collection" for intent in project.intents):
        return False
    if self._reason_claimed(project, "collection") is not None:
        return False
    return self._reason_trigger(project, "collection") is None
```

- [ ] **Step 7: Apply warmup filtering in `_try_dispatch_project`**

In `_try_dispatch_project`, after the `max_workers` check and before authenticated queue selection, add:

```python
collection_warmup_complete = self._collection_warmup_complete(project)
```

Only use the authenticated wait queue after warmup:

```python
queued_intent = None if not collection_warmup_complete else self._next_authenticated_waiting_intent(project)
```

When building unclaimed intent candidates, keep the existing list, then use this warmup branch before report/validation dispatch:

```python
if unclaimed_intents:
    export_yaml = self.client.export_project(summary.id)
    if not collection_warmup_complete:
        collection_intent = self._newest_unclaimed_intent(unclaimed_intents, task_mode="collection")
        if collection_intent is not None:
            return self._dispatch_explore(project, export_yaml, collection_intent)
    else:
        report_intent = self._newest_unclaimed_intent(unclaimed_intents, intent_kind="report")
        if report_intent is not None:
            return self._dispatch_report(project, export_yaml, report_intent)
        validation_intent = self._newest_unclaimed_intent(unclaimed_intents, task_mode="validation")
        if validation_intent is not None:
            return self._dispatch_explore(project, export_yaml, validation_intent)
        collection_intent = self._newest_unclaimed_intent(unclaimed_intents, task_mode="collection")
        if collection_intent is not None:
            return self._dispatch_explore(project, export_yaml, collection_intent)
```

Replace the existing reason loop:

```python
for task_mode in ("validation", "collection"):
```

with:

```python
reason_task_modes: tuple[TaskMode, ...] = ("validation", "collection") if collection_warmup_complete else ("collection",)
for task_mode in reason_task_modes:
```

This keeps validation reason blocked during warmup while still allowing collection reason to produce more collection work.

- [ ] **Step 8: Run focused scheduler tests again**

Run from `cairn/`:

```bash
uv run --group dev pytest -s tests/test_scheduler_logic.py::test_collection_warmup_dispatches_collection_before_validation_and_report tests/test_scheduler_logic.py::test_collection_warmup_blocks_validation_when_no_collection_round_has_run tests/test_scheduler_logic.py::test_collection_warmup_allows_validation_after_explore_threshold tests/test_scheduler_logic.py::test_collection_warmup_allows_early_validation_when_collection_converged tests/test_scheduler_logic.py::test_collection_worker_limit_blocks_collection_dispatch_globally tests/test_scheduler_logic.py::test_collection_worker_limit_does_not_block_validation_after_warmup
```

Expected: PASS.

- [ ] **Step 9: Run existing scheduler regression tests**

Run from `cairn/`:

```bash
uv run --group dev pytest -s tests/test_scheduler_logic.py
```

Expected: PASS. If an existing dispatch-order test expected report/validation before collection below the default warmup threshold, update that test to set `initial_collection_rounds=0` or set `project.project.collection_explore_rounds = 5` so it explicitly covers post-warmup ordering.

---

### Task 3: Add UI Settings Controls

**Files:**
- Modify: `cairn/src/cairn/server/static/index.html`
- Test: `cairn/tests/test_server_api.py`

**Interfaces:**
- Consumes: `/settings` JSON fields `initial_collection_rounds` and `collection_worker_limit`.
- Produces: `settingsForm` with four numeric fields and UI controls for all four fields.

- [ ] **Step 1: Add failing static UI test**

Add this test to `cairn/tests/test_server_api.py` near other static UI tests:

```python
def test_static_ui_exposes_collection_scheduling_settings(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    ui = response.text
    assert "Initial collection rounds" in ui
    assert "Collection worker limit" in ui
    assert "settingsForm: { intent_timeout: 5, reason_timeout: 5, initial_collection_rounds: 5, collection_worker_limit: 1 }" in ui
    assert "this.settingsForm.initial_collection_rounds = s.initial_collection_rounds;" in ui
    assert "this.settingsForm.collection_worker_limit = s.collection_worker_limit;" in ui
```

- [ ] **Step 2: Run UI test to verify it fails**

Run from `cairn/`:

```bash
uv run --group dev pytest -s tests/test_server_api.py::test_static_ui_exposes_collection_scheduling_settings
```

Expected: FAIL because the HTML does not expose or load the new fields.

- [ ] **Step 3: Add settings fields to HTML modal**

In `cairn/src/cairn/server/static/index.html`, inside the Server Settings modal after Reason timeout, insert:

```html
    <div class="mt-4">
      <label class="text-[11px] text-slate-400 mb-1 block font-medium">Initial collection rounds</label>
      <input type="number" min="0" x-model.number="settingsForm.initial_collection_rounds" class="w-full px-3 py-2 border border-slate-200 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-brand-100 focus:border-brand-400 transition">
      <p class="text-[11px] text-slate-400 mt-2">Validation and report start after this many completed collection executions, unless collection converges earlier.</p>
    </div>
    <div class="mt-4">
      <label class="text-[11px] text-slate-400 mb-1 block font-medium">Collection worker limit</label>
      <input type="number" min="1" x-model.number="settingsForm.collection_worker_limit" class="w-full px-3 py-2 border border-slate-200 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-brand-100 focus:border-brand-400 transition">
      <p class="text-[11px] text-slate-400 mt-2">Global cap for running collection reason and collection explore tasks.</p>
    </div>
```

- [ ] **Step 4: Extend `settingsForm` defaults and load logic**

Change the `settingsForm` object to:

```javascript
settingsForm: { intent_timeout: 5, reason_timeout: 5, initial_collection_rounds: 5, collection_worker_limit: 1 },
```

In `loadSettings()`, after assigning `reason_timeout`, add:

```javascript
this.settingsForm.initial_collection_rounds = s.initial_collection_rounds;
this.settingsForm.collection_worker_limit = s.collection_worker_limit;
```

`saveServerSettings()` can keep sending `this.settingsForm` because it already PUTs the whole object.

- [ ] **Step 5: Run UI test again**

Run from `cairn/`:

```bash
uv run --group dev pytest -s tests/test_server_api.py::test_static_ui_exposes_collection_scheduling_settings
```

Expected: PASS.

---

### Task 4: Update Worker Example And Documentation

**Files:**
- Modify: `dispatch.example.yaml`
- Modify: `README.md`
- Modify: `docs/architecture/dispatcher.md`
- Modify: `docs/user/src-workflow.md`
- Test: `cairn/tests/test_config_and_adapters.py`

**Interfaces:**
- Consumes: existing worker `task_types` schema and settings names.
- Produces: docs and example config that describe five logical worker roles and the two settings.

- [ ] **Step 1: Split worker examples into five logical roles**

In `dispatch.example.yaml`, keep existing backend examples but represent each role with one task type. Use the same env structure already present in the file. The worker section should contain entries shaped like:

```yaml
workers:
  - name: "claudecode_collection_reason_deepseek-v4-pro"
    type: "claudecode"
    task_types: [collection_reason]
    max_running: 1
    priority: 0
    env:
      ANTHROPIC_MODEL: "deepseek-v4-pro"
      ANTHROPIC_BASE_URL: "https://api.deepseek.com/anthropic"
      ANTHROPIC_AUTH_TOKEN: "sk-xxx"

  - name: "claudecode_collection_explore_deepseek-v4-pro"
    type: "claudecode"
    task_types: [collection_explore]
    max_running: 1
    priority: 0
    env:
      ANTHROPIC_MODEL: "deepseek-v4-pro"
      ANTHROPIC_BASE_URL: "https://api.deepseek.com/anthropic"
      ANTHROPIC_AUTH_TOKEN: "sk-xxx"

  - name: "codex_validation_reason_qwen3.6-plus"
    type: "codex"
    task_types: [validation_reason]
    max_running: 1
    priority: 0
    env:
      CODEX_MODEL: "qwen3.6-plus"
      CODEX_BASE_URL: "https://dashscope.aliyuncs.com/compatible-mode/v1"
      OPENAI_API_KEY: "sk-xxx"

  - name: "codex_validation_explore_qwen3.6-plus"
    type: "codex"
    task_types: [validation_explore]
    max_running: 1
    priority: 0
    env:
      CODEX_MODEL: "qwen3.6-plus"
      CODEX_BASE_URL: "https://dashscope.aliyuncs.com/compatible-mode/v1"
      OPENAI_API_KEY: "sk-xxx"

  - name: "pi_report_qwen3.6-plus"
    type: "pi"
    task_types: [report]
    max_running: 1
    priority: 0
    env:
      PI_MODEL: "qwen3.6-plus"
      PI_BASE_URL: "https://dashscope.aliyuncs.com/compatible-mode/v1"
      PI_API_KEY: "sk-xxx"
      PI_PROVIDER_API: "openai-completions"
      PI_MODEL_CONTEXT_WINDOW: "262144"
```

Also update nearby comments to say these are logical roles and that operators may combine task types on one backend worker.

- [ ] **Step 2: Update README configuration notes**

In `README.md`, update the configuration notes after the `runtime`/`workers` bullets with:

```markdown
- Server settings include `initial_collection_rounds` (default `5`) and
  `collection_worker_limit` (default `1`). New projects run collection-only
  warmup until enough collection executions finish or collection converges;
  after that, collection and validation/report can run in parallel.
```

Add one sentence after the Task types table:

```markdown
The five task types are separate logical worker roles. A deployment may use one
worker entry per role or combine compatible task types on the same backend.
```

- [ ] **Step 3: Update dispatcher architecture docs**

In `docs/architecture/dispatcher.md`, add the two settings to the configuration section:

```markdown
Server-side `/settings` also controls runtime scheduling behavior:

- `initial_collection_rounds` defaults to `5`. Validation/report scheduling is
  blocked until this many collection explore executions finish, unless
  collection converges earlier.
- `collection_worker_limit` defaults to `1`. It caps running
  `collection_reason` plus `collection_explore` tasks globally.
```

Update project scheduling rules to include:

```markdown
- Before warmup completes, report intents, validation explore, and validation
  reason are not dispatched. Collection reason/explore remain eligible.
- Warmup completes when `collection_explore_rounds >= initial_collection_rounds`
  or collection has no open/triggerable collection work after at least one
  collection reason round.
```

Update concurrency limits to include:

```markdown
- `settings.collection_worker_limit`：全局 collection reason/explore 并发上限。
```

- [ ] **Step 4: Update user workflow docs**

In `docs/user/src-workflow.md`, add this paragraph under `## Collection 阶段` after the collection goals list:

```markdown
项目启动后先进入 collection warmup。默认需要完成 5 次 `collection_explore`
执行后才开始 validation/report；如果 collection 已无可继续产出的工作，则允许提前进入 validation/report，避免卡住。全局 `collection_worker_limit` 默认 1，会限制同时运行的 `collection_reason` 与 `collection_explore` 数量。
```

- [ ] **Step 5: Verify example config still matches schema**

Run from `cairn/`:

```bash
uv run --group dev pytest -s tests/test_config_and_adapters.py::test_dispatch_example_yaml_matches_current_schema
```

Expected: PASS.

---

### Task 5: Final Verification And Diff Review

**Files:**
- Verify: all modified files from Tasks 1-4.

**Interfaces:**
- Consumes: completed settings, scheduler, UI, example, and documentation changes.
- Produces: verified implementation ready for user review.

- [ ] **Step 1: Run targeted regression suite**

Run from `cairn/`:

```bash
uv run --group dev pytest -s tests/test_db_migrations.py tests/test_server_api.py tests/test_scheduler_logic.py tests/test_config_and_adapters.py
```

Expected: PASS.

- [ ] **Step 2: Run worker task smoke tests**

Run from `cairn/`:

```bash
uv run --group dev pytest -s tests/test_worker_tasks.py tests/test_mock_end_to_end.py
```

Expected: PASS. These tests confirm the task runners and mock end-to-end scheduler flow still work with the new settings model.

- [ ] **Step 3: Search for missing settings references**

Run from repository root:

```bash
rg "initial_collection_rounds|collection_worker_limit" cairn/src cairn/tests README.md docs/architecture/dispatcher.md docs/user/src-workflow.md dispatch.example.yaml
```

Expected: matches in models, DB, settings router, scheduler, UI, tests, config example, and docs.

- [ ] **Step 4: Inspect diff without reverting unrelated work**

Run from repository root:

```bash
git diff -- docs/superpowers/specs/2026-06-27-worker-scheduling-settings-design.md docs/superpowers/plans/2026-06-27-worker-scheduling-settings.md cairn/src/cairn/server/models.py cairn/src/cairn/server/db.py cairn/src/cairn/server/routers/settings.py cairn/src/cairn/dispatcher/scheduler/loop.py cairn/src/cairn/server/static/index.html dispatch.example.yaml README.md docs/architecture/dispatcher.md docs/user/src-workflow.md cairn/tests/test_db_migrations.py cairn/tests/test_server_api.py cairn/tests/test_scheduler_logic.py cairn/tests/test_config_and_adapters.py
```

Expected: diff contains only the approved worker scheduling settings work and the earlier spec/plan files.

## Self-Review

- Spec coverage: Tasks 1-4 cover settings persistence/API, scheduler warmup and collection cap, UI, five-role worker example, docs, and tests.
- Placeholder scan: the plan contains no placeholder markers, deferred implementation, or unnamed edge-case instructions.
- Type consistency: new settings are consistently named `initial_collection_rounds` and `collection_worker_limit`; dispatcher helpers consume `Settings` from `cairn.server.models`.
