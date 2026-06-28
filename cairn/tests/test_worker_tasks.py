from __future__ import annotations

from dataclasses import dataclass, field
import json

from cairn.dispatcher.protocol.client import ApiResult
from cairn.dispatcher.runtime.cancellation import TaskCancellation
from cairn.dispatcher.runtime.process import ProcessResult
from cairn.dispatcher.tasks import explore, reason, report
from cairn.dispatcher.tasks.common import HealthcheckRun
from cairn.dispatcher.workers.base import DriverResult
from cairn.server.models import Finding
from conftest import FakeContainerManager, FakeLease, make_config, make_intent, make_project


@dataclass
class BlackboardClient:
    project: object
    created_tasks: list[tuple[str, list[str], str, str, str | None]] = field(default_factory=list)
    concluded: list[dict] = field(default_factory=list)
    reports: list[tuple[str, str, str, str]] = field(default_factory=list)
    released: list[tuple[str, str, str]] = field(default_factory=list)
    released_reasons: list[tuple[str, str, str]] = field(default_factory=list)
    collection_reason_rounds: list[tuple[str, bool]] = field(default_factory=list)

    def get_project(self, _project_id: str):
        return self.project

    def create_task(self, project_id: str, from_ids: list[str], description: str, *, task_type: str, auth_scope: str | None = None):
        self.created_tasks.append((project_id, from_ids, description, task_type, auth_scope))
        return ApiResult(201, {"id": f"t{len(self.created_tasks)}"})

    def conclude(self, project_id: str, task_id: str, worker: str, description: str, **kwargs):
        self.concluded.append(
            {
                "project_id": project_id,
                "task_id": task_id,
                "worker": worker,
                "description": description,
                **kwargs,
            }
        )
        return ApiResult(200, {"fact": {"id": "f2"}})

    def conclude_report(self, project_id: str, finding_id: str, worker: str, report_path: str):
        self.reports.append((project_id, finding_id, worker, report_path))
        return ApiResult(200, {"report": report_path})

    def release(self, project_id: str, task_id: str, worker: str):
        self.released.append((project_id, task_id, worker))
        return ApiResult(200, {})

    def release_reason(self, project_id: str, worker: str, task_mode: str):
        self.released_reasons.append((project_id, worker, task_mode))
        return ApiResult(200, {})

    def record_collection_reason_round(self, project_id: str, stable: bool):
        self.collection_reason_rounds.append((project_id, stable))
        return ApiResult(200, {})


class FakeDriver:
    def __init__(self) -> None:
        self.execute_prompts: list[str] = []

    def supports_conclude(self) -> bool:
        return True

    def prepare_session(self) -> str:
        return "session"

    def build_healthcheck(self, _worker) -> list[str]:
        return ["healthcheck"]

    def build_execute(self, _worker, prompt: str, session: str | None) -> DriverResult:
        self.execute_prompts.append(prompt)
        return DriverResult(["execute"], session=session)

    def build_conclude(self, _worker, prompt: str, _session: str) -> list[str]:
        return ["conclude"]

    def extract_session(self, session: str | None, _stdout: str, _stderr: str) -> str | None:
        return session

    def extract_response_text(self, stdout: str, _stderr: str) -> str:
        return stdout


def _lease_factory(lease: FakeLease):
    return lambda *_args, **_kwargs: lease


def _healthy(*_args, **_kwargs) -> HealthcheckRun:
    return HealthcheckRun(ProcessResult(0, "ok", ""), duration_ms=1)


def test_reason_worker_creates_collection_tasks_and_records_round(monkeypatch) -> None:
    config = make_config()
    project = make_project(tasks=[])
    client = BlackboardClient(project)
    containers = FakeContainerManager()
    lease = FakeLease()
    driver = FakeDriver()

    monkeypatch.setattr(reason, "get_driver", lambda _name: driver)
    monkeypatch.setattr(reason.HeartbeatLease, "for_reason", _lease_factory(lease))
    monkeypatch.setattr(reason, "run_healthcheck", _healthy)
    monkeypatch.setattr(
        reason,
        "run_worker_process",
        lambda *_args, **_kwargs: ProcessResult(
            0,
            json.dumps(
                {
                    "accepted": True,
                    "data": {
                        "tasks": [
                            {
                                "from": ["origin"],
                                "type": "collection_task",
                                "auth_scope": "anonymous",
                                "description": "Collect anonymous surface",
                            }
                        ]
                    },
                }
            ),
            "",
        ),
    )

    outcome = reason.run_reason_task(
        config,
        client,
        containers,
        project,
        "graph",
        config.workers[0],
        "collection",
        TaskCancellation(),
    )

    assert outcome == "success"
    assert client.created_tasks == [
        ("proj_001", ["origin"], "Collect anonymous surface", "collection_task", "anonymous")
    ]
    assert client.collection_reason_rounds == [("proj_001", False)]
    assert lease.started and lease.stopped
    assert containers.writes and "graph" in containers.writes[0][2]


def test_reason_worker_rejects_legacy_intents_output(monkeypatch) -> None:
    config = make_config()
    project = make_project(tasks=[])
    client = BlackboardClient(project)
    lease = FakeLease()

    monkeypatch.setattr(reason, "get_driver", lambda _name: FakeDriver())
    monkeypatch.setattr(reason.HeartbeatLease, "for_reason", _lease_factory(lease))
    monkeypatch.setattr(reason, "run_healthcheck", _healthy)
    monkeypatch.setattr(
        reason,
        "run_worker_process",
        lambda *_args, **_kwargs: ProcessResult(
            0,
            '{"accepted": true, "data": {"intents": [{"from": ["origin"], "description": "old"}]}}',
            "",
        ),
    )

    outcome = reason.run_reason_task(
        config,
        client,
        FakeContainerManager(),
        project,
        "graph",
        config.workers[0],
        "collection",
        TaskCancellation(),
    )

    assert outcome == "failed"
    assert client.created_tasks == []


def test_explore_worker_concludes_vulnerability_task_with_evidence_and_findings(monkeypatch) -> None:
    config = make_config()
    task = make_intent("t2")
    project = make_project(tasks=[task])
    client = BlackboardClient(project)
    lease = FakeLease()

    monkeypatch.setattr(explore, "get_driver", lambda _name: FakeDriver())
    monkeypatch.setattr(explore.HeartbeatLease, "for_intent", _lease_factory(lease))
    monkeypatch.setattr(explore, "run_healthcheck", _healthy)
    monkeypatch.setattr(
        explore,
        "_run_process",
        lambda *_args, **_kwargs: ProcessResult(
            0,
            json.dumps(
                {
                    "accepted": True,
                    "data": {
                        "description": "IDOR confirmed",
                        "evidence": "/tmp/evidence/t2.json",
                        "findings": [{"description": "Users can read other users' orders."}],
                    },
                }
            ),
            "",
        ),
    )

    outcome = explore.run_explore_task(
        config,
        client,
        FakeContainerManager(),
        project,
        "graph",
        task,
        config.workers[0],
        TaskCancellation(),
    )

    assert outcome == "success"
    assert client.concluded[0]["task_id"] == "t2"
    assert client.concluded[0]["evidence"] == "/tmp/evidence/t2.json"
    assert client.concluded[0]["findings"] == [{"description": "Users can read other users' orders."}]


def test_collection_explore_rejects_findings(monkeypatch) -> None:
    config = make_config()
    task = make_intent("t1")
    task.task_mode = "collection"
    project = make_project(tasks=[task])
    client = BlackboardClient(project)

    monkeypatch.setattr(explore, "get_driver", lambda _name: FakeDriver())
    monkeypatch.setattr(explore.HeartbeatLease, "for_intent", _lease_factory(FakeLease()))
    monkeypatch.setattr(explore, "run_healthcheck", _healthy)
    monkeypatch.setattr(
        explore,
        "_run_process",
        lambda *_args, **_kwargs: ProcessResult(
            0,
            '{"accepted": true, "data": {"description": "bad", "evidence": "/tmp/e.json", "findings": [{"description": "no"}]}}',
            "",
        ),
    )

    outcome = explore.run_explore_task(
        config,
        client,
        FakeContainerManager(),
        project,
        "graph",
        task,
        config.workers[0],
        TaskCancellation(),
    )

    assert outcome == "failed"
    assert client.concluded == []


def test_report_worker_writes_report_path(monkeypatch) -> None:
    config = make_config()
    finding = Finding(
        id="F1",
        description="IDOR allows reading other users' orders.",
        creation_time="2026-01-01T00:00:03Z",
        from_=["f2"],
        from_task="t2",
    )
    project = make_project()
    project.findings = [finding]
    client = BlackboardClient(project)

    monkeypatch.setattr(report, "get_driver", lambda _name: FakeDriver())
    monkeypatch.setattr(report, "run_healthcheck", _healthy)
    monkeypatch.setattr(
        report,
        "run_worker_process",
        lambda *_args, **_kwargs: ProcessResult(
            0,
            '{"accepted": true, "data": {"report": "/home/kali/reports/F1.md"}}',
            "",
        ),
    )

    outcome = report.run_report_task(
        config,
        client,
        FakeContainerManager(),
        project,
        "graph",
        finding,
        config.workers[0],
        TaskCancellation(),
    )

    assert outcome == "success"
    assert client.reports == [("proj_001", "F1", "test-worker", "/home/kali/reports/F1.md")]
