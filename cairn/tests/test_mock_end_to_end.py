from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
import subprocess
import threading
from typing import Any

from fastapi.testclient import TestClient
from pydantic import TypeAdapter
import pytest

from cairn.dispatcher.config import DispatchConfig
from cairn.dispatcher.models import ReasonCheckpoint
from cairn.dispatcher.protocol.client import ApiResult
from cairn.dispatcher.runtime.process import ProcessResult
from cairn.dispatcher.scheduler.loop import DispatcherLoop
from cairn.server import db
from cairn.server.app import app
from cairn.server.models import EphemeralJob, ProjectDetail, ProjectSummary, Settings


class InProcessClient:
    def __init__(self, http: TestClient):
        self.http = http
        self._summaries = TypeAdapter(list[ProjectSummary])

    def close(self) -> None:
        return None

    def list_projects(self) -> list[ProjectSummary]:
        response = self.http.get("/projects")
        response.raise_for_status()
        return self._summaries.validate_python(response.json())

    def get_project(self, project_id: str) -> ProjectDetail:
        response = self.http.get(f"/projects/{project_id}")
        response.raise_for_status()
        return ProjectDetail.model_validate(response.json())

    def get_settings(self) -> Settings:
        response = self.http.get("/settings")
        response.raise_for_status()
        return Settings.model_validate(response.json())

    def export_project(self, project_id: str) -> str:
        response = self.http.get(f"/projects/{project_id}/export?format=yaml")
        response.raise_for_status()
        return response.text

    def heartbeat(self, project_id: str, intent_id: str, worker: str) -> ApiResult:
        return self._post(f"/projects/{project_id}/intents/{intent_id}/heartbeat", {"worker": worker})

    def claim_reason(self, project_id: str, worker: str, trigger: str) -> ApiResult:
        return self._post(f"/projects/{project_id}/reason/claim", {"worker": worker, "trigger": trigger})

    def reason_heartbeat(self, project_id: str, worker: str) -> ApiResult:
        return self._post(f"/projects/{project_id}/reason/heartbeat", {"worker": worker})

    def release_reason(self, project_id: str, worker: str) -> ApiResult:
        return self._post(f"/projects/{project_id}/reason/release", {"worker": worker})

    def release(self, project_id: str, intent_id: str, worker: str) -> ApiResult:
        return self._post(f"/projects/{project_id}/intents/{intent_id}/release", {"worker": worker})

    def conclude(
        self,
        project_id: str,
        intent_id: str,
        worker: str,
        description: str,
        findings: list[dict] | None = None,
    ) -> ApiResult:
        body = {"worker": worker, "description": description}
        if findings:
            body["findings"] = findings
        return self._post(f"/projects/{project_id}/intents/{intent_id}/conclude", body)

    def create_intent(
        self,
        project_id: str,
        from_ids: list[str],
        description: str,
        creator: str,
        *,
        intent_kind: str = "explore",
        finding_id: str | None = None,
        auth_scope: str | None = None,
    ) -> ApiResult:
        body = {
            "from": from_ids,
            "description": description,
            "creator": creator,
            "worker": None,
            "intent_kind": intent_kind,
            "finding_id": finding_id,
        }
        if auth_scope is not None:
            body["auth_scope"] = auth_scope
        return self._post(f"/projects/{project_id}/intents", body)

    def record_recon_reason_round(self, project_id: str, stable: bool) -> ApiResult:
        return self._post(f"/projects/{project_id}/recon/reason-round", {"stable": stable})

    def record_recon_explore_round(self, project_id: str) -> ApiResult:
        return self._post(f"/projects/{project_id}/recon/explore-round", {})

    def list_queued_ephemeral_jobs(self, job_type: str = "judge") -> list[EphemeralJob]:
        response = self.http.get(f"/ephemeral-jobs/queued?job_type={job_type}")
        response.raise_for_status()
        return [EphemeralJob.model_validate(item) for item in response.json()]

    def claim_ephemeral_job(self, job_id: str, worker: str) -> ApiResult:
        return self._post(f"/ephemeral-jobs/{job_id}/claim", {"worker": worker})

    def finish_ephemeral_job(self, job_id: str, worker: str, result: dict[str, Any]) -> ApiResult:
        return self._post(f"/ephemeral-jobs/{job_id}/finish", {"worker": worker, "result": result})

    def fail_ephemeral_job(self, job_id: str, worker: str, error: str) -> ApiResult:
        return self._post(f"/ephemeral-jobs/{job_id}/fail", {"worker": worker, "error": error})

    def _post(self, path: str, payload: dict[str, Any]) -> ApiResult:
        response = self.http.post(path, json=payload)
        data = response.json() if response.headers.get("content-type", "").startswith("application/json") else None
        return ApiResult(response.status_code, data, response.text)


class LocalProcess:
    def __init__(self, command: list[str], env: dict[str, str]):
        self.command = command
        self.env = env
        self._process: subprocess.Popen[str] | None = None
        self._cancel_reason: str | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            self._process = subprocess.Popen(
                self.command,
                env={**os.environ, **self.env},
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

    def communicate(self, timeout: float | None) -> ProcessResult:
        assert self._process is not None
        timed_out = False
        try:
            stdout, stderr = self._process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            self.kill()
            stdout, stderr = self._process.communicate()
        return ProcessResult(
            returncode=self._process.returncode,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
            cancelled=self._cancel_reason is not None,
            cancel_reason=self._cancel_reason,
        )

    def kill(self) -> None:
        with self._lock:
            process = self._process
        if process is not None and process.poll() is None:
            process.kill()

    def cancel(self, reason: str) -> None:
        if self._cancel_reason is None:
            self._cancel_reason = reason
        self.kill()


class LocalContainerManager:
    def __init__(self) -> None:
        self.writes: list[tuple[str, str, str]] = []

    def close(self) -> None:
        return None

    def container_name(self, project_id: str) -> str:
        return f"local-{project_id}"

    def ensure_running(self, project_id: str) -> str:
        return self.container_name(project_id)

    def build_exec_process(
        self,
        _container_name: str,
        env: dict[str, str],
        command: list[str],
        timeout_seconds: int | None = None,
        kill_after_seconds: int = 5,
    ) -> LocalProcess:
        assert timeout_seconds is not None
        assert kill_after_seconds == 5
        return LocalProcess(command, env)

    def write_text_file(self, container_name: str, path: str, content: str) -> None:
        self.writes.append((container_name, path, content))

    def needs_completed_cleanup(self, _project_id: str) -> bool:
        return False

    def needs_stopped_cleanup(self, _project_id: str) -> bool:
        return False

    def managed_container_names(self) -> list[str]:
        return []


@pytest.fixture
def http_client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setattr(db, "_db_path", None)
    db.configure(tmp_path / "cairn.db")
    with TestClient(app) as client:
        yield client


def _phase(
    outcome: str,
    *,
    rules: list[dict[str, Any]] | None = None,
    zero_outcomes: list[str] | None = None,
) -> str:
    outcomes = {name: 0 for name in zero_outcomes or []}
    outcomes[outcome] = 1
    payload: dict[str, Any] = {"delay": [0, 0], "outcomes": outcomes}
    if rules is not None:
        payload["rules"] = rules
    return json.dumps(payload)


def _config(
    *,
    reason: str,
    explore: str | None = None,
    judge: str | None = None,
    task_types: list[str] | None = None,
) -> DispatchConfig:
    env = {
        "MOCK_HEALTHCHECK": _phase("ok"),
        "MOCK_REASON": reason,
        "MOCK_EXPLORE_EXECUTE": explore or _phase("fact"),
        "MOCK_JUDGE": judge or _phase("ready"),
    }
    return DispatchConfig.model_validate(
        {
            "server": "in-process",
            "runtime": {
                "interval": 1,
                "max_workers": 2,
                "max_running_projects": 1,
                "max_project_workers": 2,
                "healthcheck_timeout": 2,
                "prompt_group": "mock",
            },
            "tasks": {
                "reason": {"timeout": 2, "max_intents": 2},
                "explore": {"timeout": 2, "conclude_timeout": 2},
                "judge": {"timeout": 2},
                "report": {"timeout": 2},
            },
            "container": {
                "image": "unused",
                "network_mode": "host",
                "completed_action": "stop",
            },
            "workers": [
                {
                    "name": "mock-worker",
                    "type": "mock",
                    "task_types": task_types or ["reason", "explore", "judge"],
                    "max_running": 1,
                    "priority": 0,
                    "env": env,
                }
            ],
        }
    )


def _loop(config: DispatchConfig, client: InProcessClient, containers: LocalContainerManager) -> DispatcherLoop:
    loop = DispatcherLoop.__new__(DispatcherLoop)
    loop.config = config
    loop.client = client
    loop.container_manager = containers
    loop.executor = ThreadPoolExecutor(max_workers=config.runtime.max_workers)
    loop.cleanup_executor = ThreadPoolExecutor(max_workers=1)
    loop.futures = {}
    loop.cleanup_futures = {}
    loop.reason_checkpoints = {}
    loop.authenticated_wait_queues = {}
    loop.account_leases = {}
    loop.runtime_project_ids = set()
    loop.worker_unhealthy_until = {}
    loop.worker_rejected_until = {}
    loop._log_state = {}
    loop._cleanup_pending = set()
    loop._inactive_cleanup_done = {}
    loop.project_cursor = 0
    return loop


def _dispatch_and_wait(loop: DispatcherLoop) -> None:
    loop._reap_futures()
    summaries = loop.client.list_projects()
    loop._initialize_reason_checkpoints(summaries)
    loop._refresh_runtime_projects(summaries)
    loop._cleanup_authenticated_wait_queues(summaries)
    loop._cancel_inactive_tasks(summaries)
    loop._queue_container_cleanups(summaries)
    loop._dispatch_available(summaries)
    loop._dispatch_judge_jobs()
    assert loop.futures
    for future in list(loop.futures):
        future.result(timeout=5)
    loop._reap_futures()


def _create_project(http: TestClient, **overrides) -> str:
    body = {
        "title": "integration",
        "origin": "start",
        "accounts": [{"label": "alice", "username": "alice@example.test", "password": "secret"}],
    }
    body.update(overrides)
    response = http.post("/projects", json=body)
    assert response.status_code == 201, response.text
    return response.json()["project"]["id"]


def test_mock_scheduler_runs_reason_explore_chain_without_completion(http_client: TestClient) -> None:
    client = InProcessClient(http_client)
    containers = LocalContainerManager()
    loop = _loop(_config(reason=_phase("intent"), explore=_phase("fact")), client, containers)
    project_id = _create_project(http_client)

    try:
        _dispatch_and_wait(loop)
        assert loop.reason_checkpoints[project_id] == ReasonCheckpoint(
            fact_count=1,
            hint_count=0,
            open_intent_count=2,
        )
        _dispatch_and_wait(loop)
        _dispatch_and_wait(loop)
        project = client.get_project(project_id)
    finally:
        loop.close()

    assert project.project.status == "active"
    assert project.project.recon_reason_rounds == 1
    assert project.project.recon_explore_rounds == 2
    assert [fact.id for fact in project.facts] == ["origin", "f001", "f002"]
    assert [(intent.id, intent.auth_scope, intent.to) for intent in project.intents] == [
        ("i001", "anonymous", "f001"),
        ("i002", "authenticated", "f002"),
    ]
    assert any("/reason_execute-" in path for _, path, _ in containers.writes)
    assert any("/explore_execute-" in path for _, path, _ in containers.writes)


def test_mock_scheduler_recon_stable_can_stop_at_reason_limit(http_client: TestClient) -> None:
    client = InProcessClient(http_client)
    containers = LocalContainerManager()
    loop = _loop(
        _config(
            reason=_phase(
                "intent",
                rules=[
                    {"fact_ids_gte": 3, "open_intents_empty": True, "force": "stable"},
                ],
            )
        ),
        client,
        containers,
    )
    project_id = _create_project(http_client, recon_max_reason_rounds=2)

    try:
        _dispatch_and_wait(loop)
        _dispatch_and_wait(loop)
        _dispatch_and_wait(loop)
        _dispatch_and_wait(loop)
        project = client.get_project(project_id)
    finally:
        loop.close()

    assert project.project.status == "stopped"
    assert project.project.recon_reason_rounds == 2
    assert project.project.recon_stable_rounds == 1


def test_mock_scheduler_processes_recon_judge_job(http_client: TestClient) -> None:
    project_id = _create_project(http_client)
    response = http_client.post(f"/projects/{project_id}/recon/judgements")
    assert response.status_code == 201
    client = InProcessClient(http_client)
    containers = LocalContainerManager()
    loop = _loop(
        _config(reason=_phase("stable", zero_outcomes=["intent"]), judge=_phase("ready"), task_types=["judge"]),
        client,
        containers,
    )

    try:
        _dispatch_and_wait(loop)
        project = client.get_project(project_id)
    finally:
        loop.close()

    assert project.project.judge_status == "ready"
    assert len(project.facts) == 1
    assert any("/judge_execute-" in path for _, path, _ in containers.writes)
