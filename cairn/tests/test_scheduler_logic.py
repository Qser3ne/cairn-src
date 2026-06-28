from __future__ import annotations

from collections import deque
from concurrent.futures import Future
from dataclasses import dataclass, field

from cairn.dispatcher.models import ReasonCheckpoint, RunningTask
from cairn.dispatcher.protocol.client import ApiResult
from cairn.dispatcher.runtime.cancellation import TaskCancellation
from cairn.dispatcher.scheduler.loop import DispatcherLoop
from cairn.server.models import ProjectAccount
from cairn.server.models import Finding, ProjectSummary, Settings
from conftest import FakeContainerManager, make_account, make_config, make_intent, make_project


class SchedulerContainer(FakeContainerManager):
    def container_name(self, project_id: str) -> str:
        return f"container-{project_id}"


class RecordingExecutor:
    def __init__(self) -> None:
        self.calls = []

    def submit(self, fn, *args):
        future: Future = Future()
        future.set_result("success")
        self.calls.append((fn, args, future))
        return future


@dataclass
class SchedulerClient:
    project: object
    settings: Settings = field(
        default_factory=lambda: Settings(
            task_timeout=120,
            reason_timeout=120,
            initial_collection_rounds=0,
            collection_worker_limit=1,
        )
    )
    claims: list[tuple[str, str, str, str]] = field(default_factory=list)
    heartbeats: list[tuple[str, str, str]] = field(default_factory=list)
    exported: list[str] = field(default_factory=list)
    released: list[tuple[str, str, str]] = field(default_factory=list)
    released_reasons: list[tuple[str, str, str]] = field(default_factory=list)
    status_updates: list[tuple[str, str]] = field(default_factory=list)

    def get_project(self, _project_id: str):
        return self.project

    def export_project(self, project_id: str) -> str:
        self.exported.append(project_id)
        return "graph"

    def get_settings(self) -> Settings:
        return self.settings

    def claim_reason(self, project_id: str, worker: str, trigger: str, task_mode: str) -> ApiResult:
        self.claims.append((project_id, worker, trigger, task_mode))
        return ApiResult(200, {})

    def heartbeat(self, project_id: str, task_id: str, worker: str) -> ApiResult:
        self.heartbeats.append((project_id, task_id, worker))
        return ApiResult(200, {})

    def update_project_status(self, project_id: str, status: str) -> ApiResult:
        self.status_updates.append((project_id, status))
        return ApiResult(200, {})

    def release_reason(self, project_id: str, worker: str, task_mode: str) -> ApiResult:
        self.released_reasons.append((project_id, worker, task_mode))
        return ApiResult(200, {})

    def release(self, project_id: str, task_id: str, worker: str) -> ApiResult:
        self.released.append((project_id, task_id, worker))
        return ApiResult(200, {})


def _summary(project_id: str = "proj_001", *, unclaimed: int = 0, working: int = 0) -> ProjectSummary:
    return ProjectSummary(
        id=project_id,
        title="test",
        status="active",
        project_kind="vuln",
        auth_mode="anonymous",
        created_at="2026-01-01T00:00:00Z",
        fact_count=0,
        task_count=unclaimed + working,
        working_task_count=working,
        unclaimed_task_count=unclaimed,
        hint_count=0,
        finding_count=0,
    )


def _loop(project=None, *, collection_limit: int = 1) -> DispatcherLoop:
    loop = DispatcherLoop.__new__(DispatcherLoop)
    loop.config = make_config()
    loop.config.workers[0].task_types = [
        "collection_reason",
        "collection_explore",
        "vulnerability_reason",
        "vulnerability_explore",
        "report",
    ]
    loop.client = SchedulerClient(
        project or make_project(tasks=[]),
        settings=Settings(
            task_timeout=120,
            reason_timeout=120,
            initial_collection_rounds=0,
            collection_worker_limit=collection_limit,
        ),
    )
    loop.container_manager = SchedulerContainer()
    loop.executor = RecordingExecutor()
    loop.cleanup_executor = RecordingExecutor()
    loop.futures = {}
    loop.cleanup_futures = {}
    loop.reason_checkpoints = {}
    loop.collection_expansion_requests = {}
    loop.collection_warmup_released = set()
    loop.authenticated_wait_queues = {}
    loop.account_leases = {}
    loop.runtime_project_ids = set()
    loop.worker_unhealthy_until = {}
    loop.worker_rejected_until = {}
    loop.project_failure_counts = {}
    loop.server_settings = None
    loop._log_state = {}
    loop._cleanup_pending = set()
    loop._inactive_cleanup_done = {}
    loop.project_cursor = 0
    return loop


def _reap_task_outcome(
    loop: DispatcherLoop,
    outcome: str | None = None,
    *,
    project_id: str = "proj_001",
    task_type: str = "vulnerability_explore",
    intent_id: str | None = "t1",
    account: ProjectAccount | None = None,
    exception: Exception | None = None,
) -> None:
    future: Future = Future()
    if exception is not None:
        future.set_exception(exception)
    else:
        future.set_result(outcome)
    loop.futures[future] = RunningTask(
        project_id,
        task_type,
        "test-worker",
        TaskCancellation(),
        intent_id=intent_id,
        account=account,
    )
    loop._reap_futures()


def test_task_type_mapping_uses_vulnerability_names() -> None:
    loop = _loop()

    assert loop._reason_task_type("collection") == "collection_reason"
    assert loop._reason_task_type("vulnerability") == "vulnerability_reason"
    assert loop._explore_task_type("collection") == "collection_explore"
    assert loop._explore_task_type("vulnerability") == "vulnerability_explore"


def test_initial_project_dispatches_collection_reason() -> None:
    project = make_project(tasks=[])
    project.facts = []
    loop = _loop(project)

    assert loop._try_dispatch_project(_summary()) is True

    assert loop.client.claims == [("proj_001", "test-worker", "initial", "collection")]
    assert len(loop.futures) == 1
    running = next(iter(loop.futures.values()))
    assert running.task_type == "collection_reason"


def test_open_collection_task_dispatches_collection_explore() -> None:
    task = make_intent("t1")
    task.task_mode = "collection"
    task.worker = None
    project = make_project(tasks=[task])
    project.project.collection_explore_rounds = 99
    loop = _loop(project)

    assert loop._try_dispatch_project(_summary(unclaimed=1)) is True

    assert loop.client.heartbeats == [("proj_001", "t1", "test-worker")]
    running = next(iter(loop.futures.values()))
    assert running.task_type == "collection_explore"
    assert running.intent_id == "t1"


def test_report_worker_consumes_unreported_findings_before_open_vulnerability_tasks() -> None:
    vuln_task = make_intent("t2")
    vuln_task.worker = None
    finding = Finding(
        id="F1",
        description="IDOR confirmed",
        creation_time="2026-01-01T00:00:03Z",
        from_=["f2"],
        from_task="t2",
    )
    project = make_project(tasks=[vuln_task])
    project.findings = [finding]
    project.project.collection_explore_rounds = 99
    loop = _loop(project)

    assert loop._try_dispatch_project(_summary(unclaimed=1)) is True

    running = next(iter(loop.futures.values()))
    assert running.task_type == "report"
    assert running.intent_id == "F1"
    assert loop.client.heartbeats == []


def test_vulnerability_task_dispatches_after_warmup_when_no_report_is_pending() -> None:
    task = make_intent("t2")
    task.worker = None
    project = make_project(tasks=[task])
    project.project.collection_explore_rounds = 99
    loop = _loop(project)

    assert loop._try_dispatch_project(_summary(unclaimed=1)) is True

    assert loop.client.heartbeats == [("proj_001", "t2", "test-worker")]
    running = next(iter(loop.futures.values()))
    assert running.task_type == "vulnerability_explore"


def test_collection_worker_limit_blocks_collection_but_not_vulnerability() -> None:
    collection_task = make_intent("t1")
    collection_task.task_mode = "collection"
    project = make_project(tasks=[collection_task])
    project.project.collection_explore_rounds = 99
    loop = _loop(project, collection_limit=1)
    loop.futures = {
        Future(): RunningTask("other", "collection_explore", "busy", TaskCancellation(), intent_id="busy")
    }

    assert loop._dispatch_explore(project, "graph", collection_task) is False

    vuln_task = make_intent("t2")
    assert loop._dispatch_explore(project, "graph", vuln_task) is True


def test_reason_trigger_tracks_per_mode_open_tasks() -> None:
    task = make_intent("t1")
    task.task_mode = "vulnerability"
    project = make_project(tasks=[task])
    loop = _loop(project)
    loop.reason_checkpoints[("proj_001", "vulnerability")] = ReasonCheckpoint(
        fact_count=1,
        hint_count=1,
        open_task_count=1,
        task_mode="vulnerability",
    )

    assert loop._reason_trigger(project, "vulnerability") is None
    project.tasks = []
    assert loop._reason_trigger(project, "vulnerability") == "open_tasks:1->0"


def test_two_consecutive_project_task_failures_do_not_stop_project() -> None:
    loop = _loop()

    _reap_task_outcome(loop, "failed", intent_id="t1")
    _reap_task_outcome(loop, "unhealthy", intent_id="t2")

    assert loop.project_failure_counts == {"proj_001": 2}
    assert loop.client.status_updates == []


def test_third_consecutive_project_task_failure_stops_project_and_cleans_local_state() -> None:
    loop = _loop()
    other_future: Future = Future()
    other_cancellation = TaskCancellation()
    loop.futures[other_future] = RunningTask(
        "proj_001",
        "vulnerability_explore",
        "other-worker",
        other_cancellation,
        intent_id="t-running",
    )
    loop.runtime_project_ids.add("proj_001")
    loop.collection_warmup_released.add("proj_001")
    loop.collection_expansion_requests["proj_001"] = "vulnerability_converged"
    loop.authenticated_wait_queues["proj_001"] = deque(["t-auth"])
    loop.account_leases["proj_001"] = {"a001": "t-auth"}

    _reap_task_outcome(loop, "failed", intent_id="t1")
    _reap_task_outcome(loop, "failed", intent_id="t2")
    _reap_task_outcome(loop, "rejected", intent_id="t3")

    assert loop.client.status_updates == [("proj_001", "stopped")]
    assert "proj_001" not in loop.runtime_project_ids
    assert "proj_001" not in loop.collection_warmup_released
    assert "proj_001" not in loop.collection_expansion_requests
    assert "proj_001" not in loop.authenticated_wait_queues
    assert "proj_001" not in loop.account_leases
    assert loop.worker_rejected_until == {}
    assert other_cancellation.reason == "stopped"


def test_project_task_success_resets_consecutive_failure_count() -> None:
    loop = _loop()

    _reap_task_outcome(loop, "failed", intent_id="t1")
    _reap_task_outcome(loop, "failed", intent_id="t2")
    _reap_task_outcome(loop, "success", intent_id="t3")
    _reap_task_outcome(loop, "failed", intent_id="t4")
    _reap_task_outcome(loop, "failed", intent_id="t5")

    assert loop.project_failure_counts == {"proj_001": 2}
    assert loop.client.status_updates == []


def test_cancelled_project_task_does_not_increment_or_reset_failure_count() -> None:
    loop = _loop()

    _reap_task_outcome(loop, "failed", intent_id="t1")
    _reap_task_outcome(loop, "failed", intent_id="t2")
    _reap_task_outcome(loop, "cancelled", intent_id="t3")

    assert loop.project_failure_counts == {"proj_001": 2}
    assert loop.client.status_updates == []


def test_future_exception_counts_as_project_failure_and_releases_lease() -> None:
    loop = _loop()
    account = make_account("a001")
    loop.account_leases["proj_001"] = {"a001": "t3"}

    _reap_task_outcome(loop, "failed", intent_id="t1")
    _reap_task_outcome(loop, "failed", intent_id="t2")
    _reap_task_outcome(loop, intent_id="t3", account=account, exception=RuntimeError("boom"))

    assert loop.client.released == [("proj_001", "t3", "test-worker")]
    assert loop.client.status_updates == [("proj_001", "stopped")]
