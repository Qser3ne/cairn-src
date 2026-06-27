from __future__ import annotations

import logging
from collections import deque
from concurrent.futures import Future

import pytest
import requests

from cairn.dispatcher.models import ReasonCheckpoint, RunningTask
from cairn.dispatcher.protocol.client import ApiResult, ProtocolError
from cairn.dispatcher.runtime.cancellation import TaskCancellation
from cairn.dispatcher.scheduler.loop import DispatcherLoop
from cairn.dispatcher.scheduler.worker_select import choose_worker
from cairn.dispatcher.tasks.explore import format_auth_context
from cairn.server.models import EphemeralJob, Fact, ProjectSummary, Settings, TaskMode

from conftest import make_account, make_config, make_intent, make_project


def _loop() -> DispatcherLoop:
    loop = DispatcherLoop.__new__(DispatcherLoop)
    loop.server_settings = Settings(
        intent_timeout=15,
        reason_timeout=15,
        initial_collection_rounds=5,
        collection_worker_limit=1,
    )
    loop.reason_checkpoints = {}
    loop.collection_expansion_requests = {}
    loop.collection_warmup_released = set()
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


def _summary(project_id: str, status: str) -> ProjectSummary:
    return ProjectSummary(
        id=project_id,
        title=project_id,
        status=status,
        project_kind="vuln",
        auth_mode="anonymous",
        parent_project_id=None,
        parent_snapshot_id=None,
        created_at="2026-01-01T00:00:00Z",
        fact_count=1,
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


class _RecordingContainerManager:
    def __init__(self) -> None:
        self.needs_stopped_cleanup_calls: list[str] = []
        self.needs_orphan_cleanup_calls: list[str] = []
        self.cleanup_orphan_calls: list[str] = []
        self.managed_names: list[str] = []

    def container_name(self, project_id: str) -> str:
        return f"container-{project_id}"

    def needs_stopped_cleanup(self, project_id: str) -> bool:
        self.needs_stopped_cleanup_calls.append(project_id)
        return True

    def cleanup_stopped(self, _project_id: str) -> bool:
        return True

    def managed_container_names(self) -> list[str]:
        return self.managed_names

    def needs_orphan_cleanup(self, name: str) -> bool:
        self.needs_orphan_cleanup_calls.append(name)
        return True

    def cleanup_orphan(self, name: str) -> bool:
        self.cleanup_orphan_calls.append(name)
        return True


def _authenticated_project(intent_count: int, account_count: int = 3):
    intents = []
    for index in range(1, intent_count + 1):
        intent = make_intent(f"i{index:03d}")
        intent.worker = None
        intent.auth_scope = "authenticated"
        intent.created_at = f"2026-01-01T00:00:{index:02d}Z"
        intents.append(intent)
    project = make_project(intents=intents)
    project.project.auth_mode = "authenticated"
    project.accounts = [make_account(f"a{index:03d}") for index in range(1, account_count + 1)]
    return project


@pytest.mark.parametrize(
    ("intent_timeout", "reason_timeout"),
    [
        (6, 7),
        (7, 6),
    ],
)
def test_validate_server_settings_rejects_timeout_at_heartbeat_grace(intent_timeout: int, reason_timeout: int) -> None:
    loop = _loop()
    loop.config = make_config().model_copy(
        update={"runtime": make_config().runtime.model_copy(update={"interval": 3})}
    )
    loop.client = type(
        "Client",
        (),
        {
            "get_settings": lambda _self: Settings(
                intent_timeout=intent_timeout,
                reason_timeout=reason_timeout,
                initial_collection_rounds=5,
                collection_worker_limit=1,
            )
        },
    )()

    with pytest.raises(RuntimeError, match="must be greater than heartbeat grace"):
        loop._validate_server_settings()


def _prepare_real_dispatch(loop: DispatcherLoop, project, *, task_types: list[str] | None = None) -> _RecordingExecutor:
    config = make_config()
    worker = config.workers[0].model_copy(
        update={
            "max_running": 3,
            "task_types": task_types
            or ["collection_reason", "collection_explore", "validation_reason", "validation_explore", "report"],
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
    _set_scheduler_settings(loop, initial_collection_rounds=0, collection_worker_limit=3)
    project.project.collection_explore_rounds = max(project.project.collection_explore_rounds, 5)
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
    loop.reason_checkpoints[("proj_001", "collection")] = ReasonCheckpoint(
        fact_count=len(project.facts),
        hint_count=len(project.hints),
        open_intent_count=len([intent for intent in project.intents if intent.to is None]),
    )
    return executor


def test_reason_trigger_detects_new_facts_and_open_intent_completion() -> None:
    loop = _loop()
    project = make_project(intents=[make_intent()])
    loop.reason_checkpoints[("proj_001", "collection")] = ReasonCheckpoint(2, 1, 1)
    project.facts.append(Fact(id="f002", description="new"))
    project.intents = []

    assert loop._reason_trigger(project, "collection") == "facts:2->3,open_intents:1->0"


def test_reason_trigger_detects_pending_reason_signal() -> None:
    loop = _loop()
    project = make_project()
    project.project.reason_pending = True
    loop.reason_checkpoints[("proj_001", "collection")] = ReasonCheckpoint(
        fact_count=len(project.facts),
        hint_count=len(project.hints),
        open_intent_count=0,
    )

    assert loop._reason_trigger(project, "collection") == "pending"


def test_reason_trigger_uses_independent_mode_checkpoints() -> None:
    loop = _loop()
    project = make_project()
    project.facts.append(Fact(id="f002", description="new collection fact"))
    loop.reason_checkpoints[("proj_001", "collection")] = ReasonCheckpoint(
        fact_count=2,
        hint_count=len(project.hints),
        open_intent_count=0,
    )
    loop.reason_checkpoints[("proj_001", "validation")] = ReasonCheckpoint(
        fact_count=len(project.facts),
        hint_count=len(project.hints),
        open_intent_count=0,
        task_mode="validation",
    )

    assert loop._reason_trigger(project, "collection") == "facts:2->3"
    assert loop._reason_trigger(project, "validation") is None


def test_reason_success_checkpoint_uses_latest_project_detail() -> None:
    loop = _loop()
    started_project = make_project(intents=[make_intent()])
    latest_project = make_project(intents=[])
    latest_project.facts.append(Fact(id="f002", description="created while reason ran"))
    latest_project.hints.append(latest_project.hints[0].model_copy(update={"id": "h002"}))
    done: Future[str] = Future()
    done.set_result("success")
    loop.futures = {
            done: RunningTask(
                "proj_001",
                "collection_reason",
                "worker",
            TaskCancellation(),
            reason_trigger="facts:1->2",
            reason_start_fact_count=len(started_project.facts),
            reason_start_hint_count=len(started_project.hints),
            reason_start_open_intent_count=1,
        )
    }
    loop.client = type("Client", (), {"get_project": lambda _self, _project_id: latest_project})()

    loop._reap_futures()

    assert loop.reason_checkpoints[("proj_001", "collection")] == ReasonCheckpoint(
        fact_count=3,
        hint_count=2,
        open_intent_count=0,
    )


def test_reason_success_checkpoint_falls_back_to_start_counts_when_refresh_fails(caplog) -> None:
    loop = _loop()
    done: Future[str] = Future()
    done.set_result("success")
    loop.futures = {
            done: RunningTask(
                "proj_001",
                "collection_reason",
                "worker",
            TaskCancellation(),
            reason_trigger="open_intents:1->0",
            reason_start_fact_count=2,
            reason_start_hint_count=1,
            reason_start_open_intent_count=1,
        )
    }

    class Client:
        def get_project(self, _project_id: str):
            raise requests.ConnectionError("offline")

    loop.client = Client()

    with caplog.at_level(logging.WARNING, logger="cairn.dispatcher.scheduler.loop"):
        loop._reap_futures()

    assert loop.reason_checkpoints[("proj_001", "collection")] == ReasonCheckpoint(
        fact_count=2,
        hint_count=1,
        open_intent_count=1,
    )
    assert any(
        "reason checkpoint refresh failed project=proj_001 worker=worker trigger=open_intents:1->0" in record.getMessage()
        for record in caplog.records
    )


@pytest.mark.parametrize("task_type", ["collection_explore", "validation_explore", "report"])
def test_reap_crashed_intent_task_releases_intent_lease(task_type: str) -> None:
    loop = _loop()
    crashed: Future[str] = Future()
    crashed.set_exception(RuntimeError("boom"))
    loop.futures = {
        crashed: RunningTask(
            "proj_001",
            task_type,
            "worker",
            TaskCancellation(),
            intent_id="i001",
        )
    }
    released: list[tuple[str, str, str]] = []
    loop._best_effort_release = lambda project_id, intent_id, worker_name: released.append(
        (project_id, intent_id, worker_name)
    )

    loop._reap_futures()

    assert released == [("proj_001", "i001", "worker")]


@pytest.mark.parametrize(
    ("task_type", "task_mode"),
    [("collection_reason", "collection"), ("validation_reason", "validation")],
)
def test_reap_crashed_reason_task_releases_reason_lease(task_type: str, task_mode: TaskMode) -> None:
    loop = _loop()
    crashed: Future[str] = Future()
    crashed.set_exception(RuntimeError("boom"))
    loop.futures = {
        crashed: RunningTask(
            "proj_001",
            task_type,
            "worker",
            TaskCancellation(),
            reason_task_mode=task_mode,
        )
    }
    released: list[tuple[str, str, TaskMode]] = []
    loop._best_effort_release_reason = lambda project_id, worker_name, mode: released.append(
        (project_id, worker_name, mode)
    )

    loop._reap_futures()

    assert released == [("proj_001", "worker", task_mode)]


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


def test_reap_orphan_cleanup_future_does_not_mark_inactive_project() -> None:
    loop = _loop()
    succeeded: Future[bool] = Future()
    succeeded.set_result(True)
    loop.cleanup_futures = {succeeded: ("container-orphan", None, "orphan")}
    loop._cleanup_pending = {"container-orphan"}
    loop._inactive_cleanup_done = {"proj_001": "stopped"}

    loop._reap_cleanup_futures()

    assert loop.cleanup_futures == {}
    assert loop._cleanup_pending == set()
    assert loop._inactive_cleanup_done == {"proj_001": "stopped"}


def test_queue_container_cleanups_isolates_completed_precheck_exception() -> None:
    loop = _loop()
    executor = _RecordingExecutor()
    loop.cleanup_executor = executor

    class Containers(_RecordingContainerManager):
        def needs_completed_cleanup(self, _project_id: str) -> bool:
            raise RuntimeError("docker unavailable")

    loop.container_manager = Containers()

    loop._queue_container_cleanups([_summary("proj_001", "completed")])

    assert executor.submissions == []
    assert loop.cleanup_futures == {}
    assert loop._cleanup_pending == set()
    assert loop._inactive_cleanup_done == {}


def test_queue_container_cleanups_isolates_stopped_precheck_exception() -> None:
    loop = _loop()
    executor = _RecordingExecutor()
    loop.cleanup_executor = executor

    class Containers(_RecordingContainerManager):
        def needs_stopped_cleanup(self, _project_id: str) -> bool:
            raise RuntimeError("docker unavailable")

    loop.container_manager = Containers()

    loop._queue_container_cleanups([_summary("proj_001", "stopped")])

    assert executor.submissions == []
    assert loop.cleanup_futures == {}
    assert loop._cleanup_pending == set()
    assert loop._inactive_cleanup_done == {}


def test_queue_container_cleanups_isolates_orphan_precheck_exception() -> None:
    loop = _loop()
    executor = _RecordingExecutor()
    loop.cleanup_executor = executor

    class Containers(_RecordingContainerManager):
        def needs_orphan_cleanup(self, _name: str) -> bool:
            raise RuntimeError("docker unavailable")

    containers = Containers()
    containers.managed_names = ["container-deleted"]
    loop.container_manager = containers

    loop._queue_container_cleanups([])

    assert executor.submissions == []
    assert loop.cleanup_futures == {}
    assert loop._cleanup_pending == set()


def test_queue_container_cleanups_removes_deleted_project_orphans() -> None:
    loop = _loop()
    containers = _RecordingContainerManager()
    containers.managed_names = ["container-active", "container-deleted"]
    executor = _RecordingExecutor()
    loop.container_manager = containers
    loop.cleanup_executor = executor

    loop._queue_container_cleanups([_summary("active", "active")])

    assert containers.needs_orphan_cleanup_calls == ["container-deleted"]
    assert len(executor.submissions) == 1
    assert executor.submissions[0][1] == ("container-deleted",)
    assert loop.cleanup_futures[executor.futures[0]] == ("container-deleted", None, "orphan")
    assert loop._cleanup_pending == {"container-deleted"}


def test_queue_container_cleanups_skips_active_project_container() -> None:
    loop = _loop()
    containers = _RecordingContainerManager()
    containers.managed_names = ["container-active"]
    executor = _RecordingExecutor()
    loop.container_manager = containers
    loop.cleanup_executor = executor

    loop._queue_container_cleanups([_summary("active", "active")])

    assert containers.needs_orphan_cleanup_calls == []
    assert executor.submissions == []


def test_queue_container_cleanups_skips_pending_orphan_cleanup() -> None:
    loop = _loop()
    containers = _RecordingContainerManager()
    containers.managed_names = ["container-deleted"]
    executor = _RecordingExecutor()
    loop.container_manager = containers
    loop.cleanup_executor = executor
    loop._cleanup_pending = {"container-deleted"}

    loop._queue_container_cleanups([])

    assert containers.needs_orphan_cleanup_calls == []
    assert executor.submissions == []


def test_choose_worker_prefers_priority_then_lower_running_count() -> None:
    workers = make_config().workers
    first = workers[0].model_copy(update={"name": "first", "priority": 0})
    busy = workers[0].model_copy(update={"name": "busy", "priority": 0})
    lower_priority = workers[0].model_copy(update={"name": "lower", "priority": 1})

    ordered = choose_worker([lower_priority, busy, first], {"busy": 2, "first": 0, "lower": 0})

    assert [worker.name for worker in ordered] == ["first", "busy", "lower"]


def test_unclaimed_explore_dispatches_before_new_reason_trigger() -> None:
    loop = _loop()
    loop.config = make_config()
    loop.futures = {}
    project = make_project(intents=[make_intent()])
    project.intents[0].worker = None
    project.project.collection_explore_rounds = 5
    project.facts.append(Fact(id="f002", description="new"))
    loop.reason_checkpoints[("proj_001", "collection")] = ReasonCheckpoint(2, 1, 1)
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
    loop._dispatch_reason = lambda _project, _graph, trigger, task_mode: dispatched.append(("reason", trigger)) or True
    loop._dispatch_explore = lambda *_args: dispatched.append(("explore", "")) or True

    assert loop._try_dispatch_project(_summary("proj_001", "active"))
    assert dispatched == [("explore", "")]


def test_initial_project_dispatches_reason_directly() -> None:
    loop = _loop()
    loop.config = make_config()
    loop.futures = {}
    project = make_project()
    project.facts = project.facts[:1]
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
    loop._dispatch_reason = lambda _project, _graph, trigger, task_mode: dispatched.append(("reason", f"{task_mode}:{trigger}")) or True

    assert loop._try_dispatch_project(_summary("proj_001", "active"))
    assert dispatched == [("reason", "collection:initial")]


def test_dual_vuln_project_without_accounts_dispatches_collection_baseline() -> None:
    loop = _loop()
    project = make_project()
    project.project.auth_mode = "dual"
    project.accounts = []
    loop.config = make_config()
    loop.futures = {}
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

    assert loop._try_dispatch_project(_summary("proj_001", "active"))

    assert dispatched == [("collection", "initial")]


def test_report_validation_collection_intents_dispatch_in_required_order() -> None:
    loop = _loop()
    loop.config = make_config()
    _set_scheduler_settings(loop, initial_collection_rounds=5, collection_worker_limit=2)
    loop.futures = {}
    collection = make_intent("i001")
    collection.worker = None
    collection.task_mode = "collection"
    collection.created_at = "2026-01-01T00:00:03Z"
    validation = make_intent("i002")
    validation.worker = None
    validation.task_mode = "validation"
    validation.created_at = "2026-01-01T00:00:02Z"
    report = make_intent("i003")
    report.worker = None
    report.intent_kind = "report"
    report.task_mode = "report"
    report.created_at = "2026-01-01T00:00:01Z"
    project = make_project(intents=[collection, validation, report])
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

    def dispatch_report(_project, _graph, intent):  # noqa: ANN001
        dispatched.append(("report", intent.id))
        intent.worker = "worker"
        return True

    def dispatch_explore(_project, _graph, intent):  # noqa: ANN001
        dispatched.append((intent.task_mode, intent.id))
        intent.worker = "worker"
        return True

    loop._dispatch_report = dispatch_report
    loop._dispatch_explore = dispatch_explore

    assert loop._try_dispatch_project(_summary("proj_001", "active"))
    assert loop._try_dispatch_project(_summary("proj_001", "active"))
    assert loop._try_dispatch_project(_summary("proj_001", "active"))

    assert dispatched == [("report", "i003"), ("validation", "i002"), ("collection", "i001")]


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
    executor = _prepare_real_dispatch(loop, project)
    _set_scheduler_settings(loop, initial_collection_rounds=5, collection_worker_limit=1)
    project.project.collection_explore_rounds = 0
    project.project.collection_reason_rounds = 1
    loop.futures = {
        Future(): RunningTask("other", "collection_explore", "worker-a", TaskCancellation(), intent_id="i999")
    }

    assert not loop._try_dispatch_project(_summary("proj_001", "active"))

    assert executor.submissions == []


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


def test_validation_convergence_schedules_collection_reason_without_collection_delta() -> None:
    loop = _loop()
    loop.config = make_config()
    validation_intent = make_intent("i001")
    validation_intent.task_mode = "validation"
    validation_intent.to = "f001"
    validation_intent.concluded_at = "2026-01-01T00:00:03Z"
    project = make_project(intents=[validation_intent])
    loop.reason_checkpoints[("proj_001", "validation")] = ReasonCheckpoint(
        fact_count=len(project.facts),
        hint_count=len(project.hints),
        open_intent_count=0,
        task_mode="validation",
    )
    loop.reason_checkpoints[("proj_001", "collection")] = ReasonCheckpoint(
        fact_count=len(project.facts),
        hint_count=len(project.hints),
        open_intent_count=0,
    )
    done: Future[str] = Future()
    done.set_result("success")
    loop.futures = {
        done: RunningTask(
            "proj_001",
            "validation_reason",
            "worker",
            TaskCancellation(),
            reason_trigger="noop",
            reason_task_mode="validation",
            reason_start_fact_count=len(project.facts),
            reason_start_hint_count=len(project.hints),
            reason_start_open_intent_count=0,
        )
    }
    loop.container_manager = type("Containers", (), {"container_name": lambda _self, project_id: project_id})()
    loop.client = type(
        "Client",
        (),
        {
            "get_project": lambda _self, _project_id: project,
            "export_project": lambda _self, _project_id: "graph",
        },
    )()
    dispatched: list[tuple[TaskMode, str]] = []
    loop._dispatch_reason = lambda _project, _graph, trigger, task_mode: dispatched.append((task_mode, trigger)) or True

    loop._reap_futures()
    assert loop._try_dispatch_project(_summary("proj_001", "active"))

    assert dispatched == [("collection", "validation_converged")]


def test_collection_expansion_request_survives_failed_collection_reason() -> None:
    loop = _loop()
    loop.collection_expansion_requests["proj_001"] = "validation_converged"
    failed: Future[str] = Future()
    failed.set_result("failed")
    loop.futures = {
        failed: RunningTask(
            "proj_001",
            "collection_reason",
            "worker",
            TaskCancellation(),
            reason_trigger="validation_converged",
            reason_task_mode="collection",
            reason_start_fact_count=1,
            reason_start_hint_count=0,
            reason_start_open_intent_count=0,
        )
    }

    loop._reap_futures()

    assert loop.collection_expansion_requests == {"proj_001": "validation_converged"}


def test_collection_expansion_request_clears_after_successful_collection_reason() -> None:
    loop = _loop()
    project = make_project()
    loop.collection_expansion_requests["proj_001"] = "validation_converged"
    succeeded: Future[str] = Future()
    succeeded.set_result("success")
    loop.futures = {
        succeeded: RunningTask(
            "proj_001",
            "collection_reason",
            "worker",
            TaskCancellation(),
            reason_trigger="validation_converged",
            reason_task_mode="collection",
            reason_start_fact_count=1,
            reason_start_hint_count=0,
            reason_start_open_intent_count=0,
        )
    }
    loop.client = type("Client", (), {"get_project": lambda _self, _project_id: project})()

    loop._reap_futures()

    assert loop.collection_expansion_requests == {}


def test_running_validation_explore_does_not_block_collection_reason_when_capacity_allows() -> None:
    loop = _loop()
    config = make_config()
    loop.config = config.model_copy(
        update={"runtime": config.runtime.model_copy(update={"max_workers": 2, "max_project_workers": 2})}
    )
    loop.futures = {
        Future(): RunningTask("proj_001", "validation_explore", "test-worker", TaskCancellation(), intent_id="i001")
    }
    project = make_project()
    loop.reason_checkpoints[("proj_001", "collection")] = ReasonCheckpoint(
        fact_count=len(project.facts),
        hint_count=len(project.hints),
        open_intent_count=1,
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
    dispatched: list[tuple[TaskMode, str]] = []
    loop._dispatch_reason = lambda _project, _graph, trigger, task_mode: dispatched.append((task_mode, trigger)) or True

    assert loop._try_dispatch_project(_summary("proj_001", "active"))

    assert dispatched == [("collection", "open_intents:1->0")]


def test_dispatch_reason_uses_collection_reason_capability_and_passes_task_mode() -> None:
    loop = _loop()
    config = make_config()
    executor = _RecordingExecutor()
    project = make_project()
    project.project.project_kind = "vuln"
    project.project.judge_status = "not_judged"
    loop.config = config
    loop.executor = executor
    loop.futures = {}
    loop.container_manager = _RecordingContainerManager()
    loop.client = type(
        "Client",
        (),
        {"claim_reason": lambda _self, _project_id, _worker, _trigger, _task_mode: ApiResult(200, {})},
    )()

    assert loop._dispatch_reason(project, "graph", "initial")

    assert executor.submissions
    assert loop.futures[executor.futures[0]].task_type == "collection_reason"
    assert loop.futures[executor.futures[0]].reason_task_mode == "collection"
    assert executor.submissions[0][1][6] == "collection"


def test_dispatch_explore_uses_validation_explore_capability() -> None:
    loop = _loop()
    project = make_project(intents=[make_intent()])
    project.intents[0].worker = None
    project.intents[0].task_mode = "validation"
    executor = _prepare_real_dispatch(loop, project, task_types=["validation_explore"])

    assert loop._try_dispatch_project(_summary("proj_001", "active"))

    assert executor.submissions
    assert loop.futures[executor.futures[0]].task_type == "validation_explore"


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


def test_collection_authenticated_intent_dispatches_with_account_lease() -> None:
    loop = _loop()
    project = _authenticated_project(intent_count=1, account_count=1)
    project.intents[0].task_mode = "collection"
    executor = _prepare_real_dispatch(loop, project)

    assert loop._try_dispatch_project(_summary("proj_001", "active"))

    submitted_args = executor.submissions[0][1]
    assert loop.futures[executor.futures[0]].task_type == "collection_explore"
    assert loop.account_leases == {"proj_001": {"a001": "i001"}}
    assert loop.futures[executor.futures[0]].account == project.accounts[0]
    assert submitted_args[8] == project.accounts[0]


def test_anonymous_intent_dispatches_without_account_lease() -> None:
    loop = _loop()
    project = _authenticated_project(intent_count=1, account_count=1)
    project.intents[0].auth_scope = "anonymous"
    executor = _prepare_real_dispatch(loop, project)

    assert loop._try_dispatch_project(_summary("proj_001", "active"))

    assert len(executor.submissions) == 1
    assert loop.account_leases == {}
    assert loop.authenticated_wait_queues == {}
    assert loop.futures[executor.futures[0]].account is None


def test_collection_anonymous_intent_dispatches_without_account_lease_and_anonymous_context() -> None:
    loop = _loop()
    project = _authenticated_project(intent_count=1, account_count=1)
    project.project.auth_mode = "dual"
    project.intents[0].auth_scope = "anonymous"
    project.intents[0].task_mode = "collection"
    executor = _prepare_real_dispatch(loop, project)

    assert loop._try_dispatch_project(_summary("proj_001", "active"))

    submitted_args = executor.submissions[0][1]
    assert loop.futures[executor.futures[0]].task_type == "collection_explore"
    assert loop.account_leases == {}
    assert loop.authenticated_wait_queues == {}
    assert loop.futures[executor.futures[0]].account is None
    assert submitted_args[8] is None
    assert "Current intent auth_scope is anonymous" in format_auth_context(project, project.intents[0], submitted_args[8])


def test_authenticated_intent_logs_missing_accounts(caplog) -> None:
    loop = _loop()
    project = _authenticated_project(intent_count=1, account_count=0)
    executor = _prepare_real_dispatch(loop, project)

    with caplog.at_level(logging.INFO, logger="cairn.dispatcher.scheduler.loop"):
        assert not loop._try_dispatch_project(_summary("proj_001", "active"))

    assert executor.submissions == []
    assert loop.account_leases == {}
    assert list(loop.authenticated_wait_queues["proj_001"]) == ["i001"]
    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "authenticated explore cannot dispatch because project has no cookie sessions project=proj_001 intent=i001 queued_authenticated_intents=1 busy_accounts=0 total_accounts=0"
        in message
        for message in messages
    )


def test_authenticated_intent_releases_account_when_worker_unavailable(caplog) -> None:
    loop = _loop()
    project = _authenticated_project(intent_count=1, account_count=1)
    _prepare_real_dispatch(loop, project)
    loop.config = loop.config.model_copy(
        update={"workers": [loop.config.workers[0].model_copy(update={"max_running": 1})]}
    )
    loop.futures = {Future(): RunningTask("other", "validation_explore", "test-worker", TaskCancellation(), intent_id="busy")}

    with caplog.at_level(logging.INFO, logger="cairn.dispatcher.scheduler.loop"):
        assert not loop._try_dispatch_project(_summary("proj_001", "active"))

    assert loop.account_leases == {}
    assert loop.authenticated_wait_queues == {}
    messages = [record.getMessage() for record in caplog.records]
    assert any(
            "no worker available for validation_explore project=proj_001 intent=i001 queued_authenticated_intents=0 busy_accounts=0 total_accounts=1"
        in message
        and "blocked_busy=['test-worker(1/1)']" in message
        for message in messages
    )


def test_authenticated_intent_queues_when_all_accounts_are_busy() -> None:
    loop = _loop()
    project = _authenticated_project(intent_count=4, account_count=3)
    executor = _prepare_real_dispatch(loop, project)
    loop.account_leases = {"proj_001": {"a001": "i004", "a002": "i003", "a003": "i002"}}
    loop.futures = {
            Future(): RunningTask("proj_001", "validation_explore", "worker-a", TaskCancellation(), intent_id="i004", account=project.accounts[0]),
            Future(): RunningTask("proj_001", "validation_explore", "worker-b", TaskCancellation(), intent_id="i003", account=project.accounts[1]),
            Future(): RunningTask("proj_001", "validation_explore", "worker-c", TaskCancellation(), intent_id="i002", account=project.accounts[2]),
    }

    assert not loop._try_dispatch_project(_summary("proj_001", "active"))

    assert executor.submissions == []
    assert list(loop.authenticated_wait_queues["proj_001"]) == ["i001"]


def test_second_authenticated_intent_queues_when_single_account_is_busy(caplog) -> None:
    loop = _loop()
    project = _authenticated_project(intent_count=2, account_count=1)
    executor = _prepare_real_dispatch(loop, project)

    with caplog.at_level(logging.INFO, logger="cairn.dispatcher.scheduler.loop"):
        assert loop._try_dispatch_project(_summary("proj_001", "active"))
        assert not loop._try_dispatch_project(_summary("proj_001", "active"))

    assert len(executor.submissions) == 1
    assert loop.account_leases["proj_001"] == {"a001": "i002"}
    assert list(loop.authenticated_wait_queues["proj_001"]) == ["i001"]
    messages = [record.getMessage() for record in caplog.records]
    assert any("queued authenticated intent project=proj_001 intent=i001 queue_length=1" in message for message in messages)
    assert any(
        "authenticated explore waiting for cookie session project=proj_001 intent=i001 queued_authenticated_intents=1 busy_accounts=1 total_accounts=1"
        in message
        for message in messages
    )


def test_released_account_dispatches_queued_authenticated_intent_before_reason(caplog) -> None:
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

    with caplog.at_level(logging.DEBUG, logger="cairn.dispatcher.scheduler.loop"):
        loop._reap_futures()
        loop._try_dispatch_project(_summary("proj_001", "active"))

    assert loop.account_leases["proj_001"]["a001"] == "i001"
    assert loop.authenticated_wait_queues == {}
    assert [task.intent_id for task in loop.futures.values()].count("i001") == 1
    assert executor.submissions[-1][1][5].id == "i001"
    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "released authenticated account project=proj_001 intent=i004 released_account=a001 queued_authenticated_intents=1 busy_accounts=2"
        in message
        for message in messages
    )
    assert any(
        "selected authenticated waiting intent project=proj_001 intent=i001 queue_length=1 busy_accounts=2 total_accounts=3"
        in message
        for message in messages
    )


def test_authenticated_wait_queue_discards_invalid_or_inactive_projects(caplog) -> None:
    loop = _loop()
    valid = make_intent("i001")
    valid.worker = None
    valid.auth_scope = "authenticated"
    claimed = make_intent("i002")
    claimed.worker = "worker"
    claimed.auth_scope = "authenticated"
    project = _authenticated_project(intent_count=0, account_count=2)
    project.intents = [claimed, valid]
    loop.authenticated_wait_queues = {
        "proj_001": deque(["missing", "i002", "i001"]),
        "stopped": deque(["i001"]),
        "anonymous": deque(["i001"]),
    }
    loop.account_leases = {
        "proj_001": {"a001": "i999"},
        "stopped": {"a001": "i001"},
        "anonymous": {"a001": "i001"},
    }

    with caplog.at_level(logging.DEBUG, logger="cairn.dispatcher.scheduler.loop"):
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
    assert set(loop.account_leases) == {"proj_001"}
    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "discarding stale authenticated waiting intent project=proj_001 intent=missing reason=missing" in message
        for message in messages
    )
    assert any(
        "discarding stale authenticated waiting intent project=proj_001 intent=i002 reason=claimed" in message
        for message in messages
    )
    assert any(
        "cleared authenticated wait queue project=stopped queued_authenticated_intents=1 reason=inactive_or_anonymous"
        in message
        for message in messages
    )
    assert any(
        "cleared authenticated account leases project=anonymous busy_accounts=1 reason=inactive_or_anonymous"
        in message
        for message in messages
    )


def test_report_intent_dispatches_report_task() -> None:
    loop = _loop()
    project = make_project(intents=[make_intent()])
    project.intents[0].worker = None
    project.intents[0].intent_kind = "report"
    executor = _prepare_real_dispatch(loop, project)

    assert loop._try_dispatch_project(_summary("proj_001", "active"))

    assert executor.submissions
    assert loop.futures[executor.futures[0]].task_type == "report"


def test_dispatch_judge_jobs_ignores_legacy_ephemeral_job_queue() -> None:
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
    loop.container_manager = _RecordingContainerManager()

    loop._dispatch_judge_jobs()

    assert executor.submissions == []
    assert loop.futures == {}


def test_dispatch_fork_seed_jobs_ignores_legacy_ephemeral_job_queue() -> None:
    loop = _loop()
    config = make_config()
    executor = _RecordingExecutor()
    job = EphemeralJob(
        id="fork_001",
        project_id="proj_001",
        job_type="fork_seed",
        status="queued",
        input_snapshot_yaml="project: {}\nfacts:\n- id: f001\n  description: fact\n",
        created_at="2026-01-01T00:00:00Z",
        expires_at="2026-01-02T00:00:00Z",
    )
    loop.config = config
    loop.executor = executor
    loop.futures = {}
    loop.client = type("Client", (), {"list_queued_ephemeral_jobs": lambda _self, _job_type: [job]})()
    loop.container_manager = _RecordingContainerManager()

    loop._dispatch_fork_seed_jobs()

    assert executor.submissions == []
    assert loop.futures == {}


def test_dispatcher_run_once_does_not_call_legacy_ephemeral_dispatchers() -> None:
    loop = _loop()
    config = make_config()
    loop.config = config
    loop.client = type(
        "Client",
        (),
        {
            "get_settings": lambda _self: loop.server_settings,
            "list_projects": lambda _self: [],
        },
    )()
    loop.container_manager = _RecordingContainerManager()
    loop.executor = _RecordingExecutor()
    loop.cleanup_executor = _RecordingExecutor()
    loop._settings_checked = True
    loop._startup_healthchecks_checked = True
    called: list[str] = []
    loop._dispatch_judge_jobs = lambda: called.append("judge")
    loop._dispatch_fork_seed_jobs = lambda: called.append("fork_seed")
    loop.close = lambda: None

    loop.run(once=True)

    assert called == []


def test_dispatcher_run_retries_protocol_error_in_loop_mode(monkeypatch) -> None:
    class StopLoop(Exception):
        pass

    loop = _loop()
    config = make_config()
    loop.config = config
    loop.container_manager = _RecordingContainerManager()
    loop.executor = _RecordingExecutor()
    loop.cleanup_executor = _RecordingExecutor()
    loop._settings_checked = True
    loop._startup_healthchecks_checked = True
    loop.close = lambda: None
    sleeps: list[int] = []
    monkeypatch.setattr("cairn.dispatcher.scheduler.loop.time.sleep", lambda seconds: sleeps.append(seconds))

    class Client:
        def __init__(self) -> None:
            self.settings_calls = 0

        def get_settings(self):
            self.settings_calls += 1
            if self.settings_calls == 1:
                raise ProtocolError("bad settings json", 200, "not json")
            return loop.server_settings

        def list_projects(self):
            raise StopLoop()

    client = Client()
    loop.client = client

    with pytest.raises(StopLoop):
        loop.run(once=False)

    assert client.settings_calls == 2
    assert sleeps == [config.runtime.interval]


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


def test_cancel_inactive_tasks_cancels_legacy_ephemeral_tasks() -> None:
    loop = _loop()
    stopped_judge = TaskCancellation()
    stopped_explore = TaskCancellation()
    completed_judge = TaskCancellation()
    deleted_judge = TaskCancellation()
    loop.futures = {
        Future(): RunningTask("stopped", "judge", "worker", stopped_judge, intent_id="judge_001"),
        Future(): RunningTask("stopped", "explore", "worker", stopped_explore, intent_id="i001"),
        Future(): RunningTask("completed", "judge", "worker", completed_judge, intent_id="judge_002"),
        Future(): RunningTask("deleted", "judge", "worker", deleted_judge, intent_id="judge_003"),
    }

    loop._cancel_inactive_tasks([_summary("stopped", "stopped"), _summary("completed", "completed")])

    assert stopped_judge.reason == "stopped"
    assert stopped_explore.reason == "stopped"
    assert completed_judge.reason == "completed"
    assert deleted_judge.reason == "deleted"


def test_cleanup_stopped_containers_does_not_treat_legacy_judge_as_active_blocker() -> None:
    loop = _loop()
    containers = _RecordingContainerManager()
    executor = _RecordingExecutor()
    loop.container_manager = containers
    loop.cleanup_executor = executor
    loop.futures = {Future(): RunningTask("proj_001", "judge", "worker", TaskCancellation(), intent_id="judge_001")}

    loop._cleanup_stopped_containers([_summary("proj_001", "stopped")])

    assert containers.needs_stopped_cleanup_calls == ["proj_001"]
    assert len(executor.submissions) == 1


def test_initialize_reason_checkpoint_only_for_active_projects_with_open_intents() -> None:
    loop = _loop()
    active = _summary("active", "active")
    active.unclaimed_intent_count = 1
    collection_intent = make_intent("i001")
    collection_intent.task_mode = "collection"
    project = make_project(intents=[collection_intent])
    project.project.id = "active"
    active.fact_count = len(project.facts)
    active.hint_count = len(project.hints)
    loop.client = type("Client", (), {"get_project": lambda _self, _project_id: project})()

    loop._initialize_reason_checkpoints([active, _summary("idle", "active"), _summary("stopped", "stopped")])

    assert loop.reason_checkpoints == {
        ("active", "collection"): ReasonCheckpoint(fact_count=2, hint_count=1, open_intent_count=1),
        ("active", "validation"): ReasonCheckpoint(
            fact_count=2,
            hint_count=1,
            open_intent_count=0,
            task_mode="validation",
        ),
    }


def test_initialize_reason_checkpoint_does_not_create_collection_delta_for_validation_work() -> None:
    loop = _loop()
    active = _summary("proj_001", "active")
    active.working_intent_count = 1
    validation_intent = make_intent("i001")
    validation_intent.task_mode = "validation"
    project = make_project(intents=[validation_intent])
    active.fact_count = len(project.facts)
    active.hint_count = len(project.hints)
    loop.client = type("Client", (), {"get_project": lambda _self, _project_id: project})()

    loop._initialize_reason_checkpoints([active])

    assert loop.reason_checkpoints[("proj_001", "collection")].open_intent_count == 0
    assert loop.reason_checkpoints[("proj_001", "validation")].open_intent_count == 1

    validation_intent.to = "f001"
    validation_intent.concluded_at = "2026-01-01T00:00:03Z"

    assert loop._reason_trigger(project, "collection") is None
    assert loop._reason_trigger(project, "validation") == "open_intents:1->0"


def test_select_worker_reports_busy_unhealthy_rejected_and_unsupported_workers(monkeypatch) -> None:
    loop = _loop()
    base = make_config()
    busy = base.workers[0].model_copy(update={"name": "busy", "task_types": ["collection_reason"]})
    unhealthy = base.workers[0].model_copy(update={"name": "unhealthy", "task_types": ["collection_reason"]})
    rejected = base.workers[0].model_copy(update={"name": "rejected", "task_types": ["collection_reason"]})
    unsupported = base.workers[0].model_copy(update={"name": "unsupported", "task_types": ["validation_explore"]})
    loop.config = base.model_copy(update={"workers": [busy, unhealthy, rejected, unsupported]})
    loop.futures = {Future(): RunningTask("proj", "collection_reason", "busy", TaskCancellation())}
    loop.worker_unhealthy_until = {"unhealthy": 110.0}
    loop.worker_rejected_until = {("proj", "collection_reason", "rejected"): 120.0}
    monkeypatch.setattr("cairn.dispatcher.scheduler.loop.time.time", lambda: 100.0)

    selection = loop._select_worker("proj", "collection_reason")

    assert selection.worker is None
    assert selection.blocked_busy == ["busy(1/1)"]
    assert selection.blocked_unhealthy == ["unhealthy(10.0s)"]
    assert selection.blocked_rejected == ["rejected(20.0s)"]
    assert selection.blocked_task_type == ["unsupported"]


def test_successful_task_does_not_clear_existing_worker_cooldowns() -> None:
    loop = _loop()
    done: Future[str] = Future()
    done.set_result("success")
    loop.futures = {
        done: RunningTask("proj", "explore", "worker", TaskCancellation(), intent_id="i001")
    }
    loop.worker_unhealthy_until = {"worker": 200.0}
    loop.worker_rejected_until = {("proj", "explore", "worker"): 200.0}

    loop._reap_futures()

    assert loop.worker_unhealthy_until == {"worker": 200.0}
    assert loop.worker_rejected_until == {("proj", "explore", "worker"): 200.0}


def test_select_worker_prunes_expired_cooldowns(monkeypatch) -> None:
    loop = _loop()
    loop.config = make_config()
    loop.worker_unhealthy_until = {"test-worker": 90.0}
    loop.worker_rejected_until = {("proj", "collection_reason", "test-worker"): 95.0}
    monkeypatch.setattr("cairn.dispatcher.scheduler.loop.time.time", lambda: 100.0)

    selection = loop._select_worker("proj", "collection_reason")

    assert selection.worker is not None
    assert selection.worker.name == "test-worker"
    assert loop.worker_unhealthy_until == {}
    assert loop.worker_rejected_until == {}


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
