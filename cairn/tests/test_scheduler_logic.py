from __future__ import annotations

from collections import deque
from concurrent.futures import Future

from cairn.dispatcher.models import ReasonCheckpoint, RunningTask
from cairn.dispatcher.protocol.client import ApiResult
from cairn.dispatcher.runtime.cancellation import TaskCancellation
from cairn.dispatcher.scheduler.loop import DispatcherLoop
from cairn.dispatcher.scheduler.worker_select import choose_worker
from cairn.server.models import EphemeralJob, Fact, ProjectSummary

from conftest import make_account, make_config, make_intent, make_project


def _loop() -> DispatcherLoop:
    loop = DispatcherLoop.__new__(DispatcherLoop)
    loop.reason_checkpoints = {}
    loop.authenticated_wait_queues = {}
    loop.account_leases = {}
    loop.runtime_project_ids = set()
    loop.futures = {}
    loop.cleanup_futures = {}
    loop._cleanup_pending = set()
    loop._inactive_cleanup_done = {}
    loop.worker_unhealthy_until = {}
    loop.worker_rejected_until = {}
    loop._log_state = {}
    loop.project_cursor = 0
    return loop


def _summary(project_id: str, status: str) -> ProjectSummary:
    return ProjectSummary(
        id=project_id,
        title=project_id,
        status=status,
        project_kind="recon",
        auth_mode="anonymous",
        parent_project_id=None,
        parent_snapshot_id=None,
        created_at="2026-01-01T00:00:00Z",
        fact_count=2,
        intent_count=0,
        working_intent_count=0,
        unclaimed_intent_count=0,
        hint_count=0,
        finding_count=0,
    )


class _RecordingExecutor:
    def __init__(self) -> None:
        self.futures: list[Future[str]] = []
        self.submissions: list[tuple[object, tuple[object, ...]]] = []

    def submit(self, fn, *args):  # noqa: ANN001
        future: Future[str] = Future()
        self.futures.append(future)
        self.submissions.append((fn, args))
        return future


def _authenticated_project(intent_count: int, account_count: int = 3):
    intents = []
    for index in range(1, intent_count + 1):
        intent = make_intent(f"i{index:03d}")
        intent.worker = None
        intent.created_at = f"2026-01-01T00:00:{index:02d}Z"
        intents.append(intent)
    project = make_project(intents=intents)
    project.project.auth_mode = "authenticated"
    project.accounts = [make_account(f"a{index:03d}") for index in range(1, account_count + 1)]
    return project


def _prepare_real_dispatch(loop: DispatcherLoop, project, *, task_types: list[str] | None = None) -> _RecordingExecutor:
    config = make_config()
    worker = config.workers[0].model_copy(
        update={
            "max_running": 3,
            "task_types": task_types or ["reason", "explore", "judge", "report"],
        }
    )
    config = config.model_copy(
        update={
            "runtime": config.runtime.model_copy(update={"max_workers": 3, "max_project_workers": 3}),
            "workers": [worker],
        }
    )
    executor = _RecordingExecutor()
    loop.config = config
    loop.executor = executor
    loop.futures = {}
    loop.container_manager = type("Containers", (), {"container_name": lambda _self, project_id: project_id})()
    loop.client = type(
        "Client",
        (),
        {
            "get_project": lambda _self, _project_id: project,
            "export_project": lambda _self, _project_id: "graph",
            "heartbeat": lambda _self, _project_id, _intent_id, _worker: ApiResult(200, {}),
        },
    )()
    loop.reason_checkpoints["proj_001"] = ReasonCheckpoint(
        fact_count=len(project.facts),
        hint_count=len(project.hints),
        open_intent_count=len([intent for intent in project.intents if intent.to is None]),
    )
    return executor


def test_reason_trigger_detects_new_facts_and_open_intent_completion() -> None:
    loop = _loop()
    project = make_project(intents=[make_intent()])
    loop.reason_checkpoints["proj_001"] = ReasonCheckpoint(3, 1, 1)
    project.facts.append(Fact(id="f002", description="new"))
    project.intents = []

    assert loop._reason_trigger(project) == "facts:3->4,open_intents:1->0"


def test_refresh_runtime_projects_discards_active_and_changed_cleanup_markers() -> None:
    loop = _loop()
    loop.runtime_project_ids = {"active", "stopped", "deleted"}
    loop._inactive_cleanup_done = {
        "active": "stopped",
        "stopped": "stopped",
        "changed": "completed",
        "deleted": "completed",
    }

    loop._refresh_runtime_projects(
        [_summary("active", "active"), _summary("stopped", "stopped"), _summary("changed", "stopped")]
    )

    assert loop.runtime_project_ids == {"active"}
    assert loop._inactive_cleanup_done == {"stopped": "stopped"}


def test_reap_cleanup_future_records_only_successful_inactive_cleanup() -> None:
    loop = _loop()
    succeeded: Future[bool] = Future()
    failed: Future[bool] = Future()
    succeeded.set_result(True)
    failed.set_result(False)
    loop.cleanup_futures = {
        succeeded: ("container-success", "proj-success", "completed"),
        failed: ("container-failed", "proj-failed", "stopped"),
    }
    loop._cleanup_pending = {"container-success", "container-failed"}
    loop._inactive_cleanup_done = {"proj-failed": "stopped"}

    loop._reap_cleanup_futures()

    assert loop.cleanup_futures == {}
    assert loop._cleanup_pending == set()
    assert loop._inactive_cleanup_done == {"proj-success": "completed"}


def test_choose_worker_prefers_priority_then_lower_running_count() -> None:
    workers = make_config().workers
    first = workers[0].model_copy(update={"name": "first", "priority": 0})
    busy = workers[0].model_copy(update={"name": "busy", "priority": 0})
    lower_priority = workers[0].model_copy(update={"name": "lower", "priority": 1})

    ordered = choose_worker([lower_priority, busy, first], {"busy": 2, "first": 0, "lower": 0})

    assert [worker.name for worker in ordered] == ["first", "busy", "lower"]


def test_new_fact_dispatches_reason_before_unclaimed_explore_intent() -> None:
    loop = _loop()
    loop.config = make_config()
    loop.futures = {}
    project = make_project(intents=[make_intent()])
    project.intents[0].worker = None
    project.facts.append(Fact(id="f002", description="new"))
    loop.reason_checkpoints["proj_001"] = ReasonCheckpoint(3, 1, 1)
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
    loop._dispatch_reason = lambda _project, _graph, trigger: dispatched.append(("reason", trigger)) or True
    loop._dispatch_explore = lambda *_args: dispatched.append(("explore", "")) or True

    assert loop._try_dispatch_project(_summary("proj_001", "active"))
    assert dispatched == [("reason", "facts:3->4")]


def test_initial_project_dispatches_reason_directly() -> None:
    loop = _loop()
    loop.config = make_config()
    loop.futures = {}
    project = make_project()
    project.facts = project.facts[:2]
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
    loop._dispatch_reason = lambda _project, _graph, trigger: dispatched.append(("reason", trigger)) or True

    assert loop._try_dispatch_project(_summary("proj_001", "active"))
    assert dispatched == [("reason", "initial")]


def test_authenticated_project_dispatches_with_available_accounts() -> None:
    loop = _loop()
    project = _authenticated_project(intent_count=2, account_count=3)
    executor = _prepare_real_dispatch(loop, project)

    assert loop._try_dispatch_project(_summary("proj_001", "active"))
    assert loop._try_dispatch_project(_summary("proj_001", "active"))

    assert len(executor.submissions) == 2
    assert len(loop.futures) == 2
    assert loop.account_leases["proj_001"] == {"a001": "i002", "a002": "i001"}
    assert loop.authenticated_wait_queues == {}
    assert [task.account.id for task in loop.futures.values()] == ["a001", "a002"]


def test_authenticated_intent_queues_when_all_accounts_are_busy() -> None:
    loop = _loop()
    project = _authenticated_project(intent_count=4, account_count=3)
    executor = _prepare_real_dispatch(loop, project)
    loop.account_leases = {"proj_001": {"a001": "i004", "a002": "i003", "a003": "i002"}}
    loop.futures = {
        Future(): RunningTask("proj_001", "explore", "worker-a", TaskCancellation(), intent_id="i004", account=project.accounts[0]),
        Future(): RunningTask("proj_001", "explore", "worker-b", TaskCancellation(), intent_id="i003", account=project.accounts[1]),
        Future(): RunningTask("proj_001", "explore", "worker-c", TaskCancellation(), intent_id="i002", account=project.accounts[2]),
    }

    assert not loop._try_dispatch_project(_summary("proj_001", "active"))

    assert executor.submissions == []
    assert list(loop.authenticated_wait_queues["proj_001"]) == ["i001"]


def test_released_account_dispatches_queued_authenticated_intent_before_reason() -> None:
    loop = _loop()
    project = _authenticated_project(intent_count=4, account_count=3)
    project.facts.append(Fact(id="f002", description="new"))
    executor = _prepare_real_dispatch(loop, project)
    done: Future[str] = Future()
    running_a = Future()
    running_b = Future()
    loop.futures = {
        done: RunningTask("proj_001", "explore", "worker-a", TaskCancellation(), intent_id="i004", account=project.accounts[0]),
        running_a: RunningTask("proj_001", "explore", "worker-b", TaskCancellation(), intent_id="i003", account=project.accounts[1]),
        running_b: RunningTask("proj_001", "explore", "worker-c", TaskCancellation(), intent_id="i002", account=project.accounts[2]),
    }
    loop.account_leases = {"proj_001": {"a001": "i004", "a002": "i003", "a003": "i002"}}
    loop.authenticated_wait_queues = {"proj_001": deque(["i001"])}
    done.set_result("success")

    loop._reap_futures()
    loop._try_dispatch_project(_summary("proj_001", "active"))

    assert loop.account_leases["proj_001"]["a001"] == "i001"
    assert loop.authenticated_wait_queues == {}
    assert [task.intent_id for task in loop.futures.values()].count("i001") == 1
    assert executor.submissions[-1][1][5].id == "i001"


def test_authenticated_wait_queue_discards_invalid_or_inactive_projects() -> None:
    loop = _loop()
    valid = make_intent("i001")
    valid.worker = None
    claimed = make_intent("i002")
    claimed.worker = "worker"
    project = _authenticated_project(intent_count=0, account_count=2)
    project.intents = [claimed, valid]
    loop.authenticated_wait_queues = {
        "proj_001": deque(["missing", "i002", "i001"]),
        "stopped": deque(["i001"]),
        "anonymous": deque(["i001"]),
    }

    assert loop._next_authenticated_waiting_intent(project) == valid
    assert list(loop.authenticated_wait_queues["proj_001"]) == ["i001"]

    loop._cleanup_authenticated_wait_queues(
        [
            _summary("proj_001", "active").model_copy(update={"auth_mode": "authenticated"}),
            _summary("stopped", "stopped").model_copy(update={"auth_mode": "authenticated"}),
            _summary("anonymous", "active"),
        ]
    )

    assert set(loop.authenticated_wait_queues) == {"proj_001"}


def test_report_intent_dispatches_report_task() -> None:
    loop = _loop()
    project = make_project(intents=[make_intent()])
    project.intents[0].worker = None
    project.intents[0].intent_kind = "report"
    executor = _prepare_real_dispatch(loop, project)

    assert loop._try_dispatch_project(_summary("proj_001", "active"))

    assert executor.submissions
    assert loop.futures[executor.futures[0]].task_type == "report"


def test_dispatch_judge_jobs_uses_ephemeral_job_queue() -> None:
    loop = _loop()
    config = make_config()
    executor = _RecordingExecutor()
    job = EphemeralJob(
        id="job_001",
        project_id="proj_001",
        job_type="judge",
        status="queued",
        input_snapshot_yaml="project: {}",
        created_at="2026-01-01T00:00:00Z",
        expires_at="2026-01-02T00:00:00Z",
    )
    loop.config = config
    loop.executor = executor
    loop.futures = {}
    loop.client = type("Client", (), {"list_queued_ephemeral_jobs": lambda _self, _job_type: [job]})()
    loop.container_manager = object()

    loop._dispatch_judge_jobs()

    assert executor.submissions
    assert loop.futures[executor.futures[0]].task_type == "judge"
    assert loop.futures[executor.futures[0]].intent_id == "job_001"


def test_cancel_inactive_tasks_marks_stopped_and_deleted_projects() -> None:
    loop = _loop()
    stopped = TaskCancellation()
    deleted = TaskCancellation()
    loop.futures = {
        Future(): RunningTask("stopped", "explore", "worker", stopped),
        Future(): RunningTask("deleted", "reason", "worker", deleted),
    }

    loop._cancel_inactive_tasks([_summary("stopped", "stopped")])

    assert stopped.reason == "stopped"
    assert deleted.reason == "deleted"


def test_initialize_reason_checkpoint_only_for_active_projects_with_open_intents() -> None:
    loop = _loop()
    active = _summary("active", "active")
    active.unclaimed_intent_count = 1

    loop._initialize_reason_checkpoints([active, _summary("idle", "active"), _summary("stopped", "stopped")])

    assert loop.reason_checkpoints == {
        "active": ReasonCheckpoint(fact_count=2, hint_count=0, open_intent_count=1)
    }


def test_select_worker_reports_busy_unhealthy_rejected_and_unsupported_workers(monkeypatch) -> None:
    loop = _loop()
    base = make_config()
    busy = base.workers[0].model_copy(update={"name": "busy", "task_types": ["reason"]})
    unhealthy = base.workers[0].model_copy(update={"name": "unhealthy", "task_types": ["reason"]})
    rejected = base.workers[0].model_copy(update={"name": "rejected", "task_types": ["reason"]})
    unsupported = base.workers[0].model_copy(update={"name": "unsupported", "task_types": ["explore"]})
    loop.config = base.model_copy(update={"workers": [busy, unhealthy, rejected, unsupported]})
    loop.futures = {Future(): RunningTask("proj", "reason", "busy", TaskCancellation())}
    loop.worker_unhealthy_until = {"unhealthy": 110.0}
    loop.worker_rejected_until = {("proj", "reason", "rejected"): 120.0}
    monkeypatch.setattr("cairn.dispatcher.scheduler.loop.time.time", lambda: 100.0)

    selection = loop._select_worker("proj", "reason")

    assert selection.worker is None
    assert selection.blocked_busy == ["busy(1/1)"]
    assert selection.blocked_unhealthy == ["unhealthy(10.0s)"]
    assert selection.blocked_rejected == ["rejected(20.0s)"]
    assert selection.blocked_task_type == ["unsupported"]


def test_disabled_worker_healthcheck_skips_automatic_startup_but_force_runs_diagnostic() -> None:
    loop = _loop()
    config = make_config()
    loop.config = config.model_copy(update={"runtime": config.runtime.model_copy(update={"worker_healthcheck": "disabled"})})
    calls: list[bool] = []
    loop._run_startup_healthchecks = lambda *, show_commands: calls.append(show_commands)
    loop._startup_healthchecks_checked = False

    loop.run_startup_healthchecks()

    assert calls == []
    assert loop._startup_healthchecks_checked

    loop._startup_healthchecks_checked = False
    loop.run_startup_healthchecks(show_commands=True, force=True)

    assert calls == [True]


def test_startup_only_worker_healthcheck_runs_automatic_startup_check() -> None:
    loop = _loop()
    config = make_config()
    loop.config = config.model_copy(update={"runtime": config.runtime.model_copy(update={"worker_healthcheck": "startup_only"})})
    calls: list[bool] = []
    loop._run_startup_healthchecks = lambda *, show_commands: calls.append(show_commands)
    loop._startup_healthchecks_checked = False

    loop.run_startup_healthchecks()

    assert calls == [False]
