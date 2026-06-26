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

    def claim_reason(self, project_id: str, worker: str, trigger: str, task_mode: str) -> ApiResult:
        return self._post(
            f"/projects/{project_id}/reason/claim",
            {"worker": worker, "trigger": trigger, "task_mode": task_mode},
        )

    def reason_heartbeat(self, project_id: str, worker: str, task_mode: str) -> ApiResult:
        return self._post(f"/projects/{project_id}/reason/heartbeat", {"worker": worker, "task_mode": task_mode})

    def release_reason(self, project_id: str, worker: str, task_mode: str) -> ApiResult:
        return self._post(f"/projects/{project_id}/reason/release", {"worker": worker, "task_mode": task_mode})

    def release(self, project_id: str, intent_id: str, worker: str) -> ApiResult:
        return self._post(f"/projects/{project_id}/intents/{intent_id}/release", {"worker": worker})

    def conclude(
        self,
        project_id: str,
        intent_id: str,
        worker: str,
        description: str,
        *,
        fact_type: str = "observation",
        title: str | None = None,
        summary: str | None = None,
        details: dict | None = None,
        findings: list[dict] | None = None,
    ) -> ApiResult:
        body = {
            "worker": worker,
            "description": description,
            "fact_type": fact_type,
            "title": title,
            "summary": summary,
            "details": details or {},
        }
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
        task_mode: str | None = None,
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
        if task_mode is not None:
            body["task_mode"] = task_mode
        return self._post(f"/projects/{project_id}/intents", body)

    def record_collection_reason_round(self, project_id: str, stable: bool) -> ApiResult:
        return self._post(f"/projects/{project_id}/recon/reason-round", {"stable": stable})

    def record_collection_explore_round(self, project_id: str) -> ApiResult:
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

    def conclude_report(
        self,
        project_id: str,
        intent_id: str,
        worker: str,
        report_markdown: str,
        report_json: dict[str, Any],
    ) -> ApiResult:
        return self._post(
            f"/projects/{project_id}/intents/{intent_id}/report",
            {"worker": worker, "report_markdown": report_markdown, "report_json": report_json},
        )

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
    collection_reason: str | None = None,
    validation_reason: str | None = None,
    collection_explore: str | None = None,
    validation_explore: str | None = None,
    report: str | None = None,
    task_types: list[str] | None = None,
) -> DispatchConfig:
    explore_phase = explore or _phase("fact")
    env = {
        "MOCK_HEALTHCHECK": _phase("ok"),
        "MOCK_REASON": reason,
        "MOCK_COLLECTION_REASON": collection_reason or reason,
        "MOCK_VALIDATION_REASON": validation_reason or reason,
        "MOCK_EXPLORE_EXECUTE": explore_phase,
        "MOCK_COLLECTION_EXPLORE_EXECUTE": collection_explore or explore_phase,
        "MOCK_VALIDATION_EXPLORE_EXECUTE": validation_explore or explore_phase,
        "MOCK_REPORT": report or _phase("draft"),
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
                    "task_types": task_types
                    or ["collection_reason", "collection_explore", "validation_reason", "validation_explore", "report"],
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
    loop.collection_expansion_requests = {}
    loop.collection_warmup_released = set()
    loop.authenticated_wait_queues = {}
    loop.account_leases = {}
    loop.runtime_project_ids = set()
    loop.worker_unhealthy_until = {}
    loop.worker_rejected_until = {}
    loop.server_settings = client.get_settings()
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
        "accounts": [{"label": "alice", "cookies": [{"name": "sessionid", "value": "secret"}]}],
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
        assert loop.reason_checkpoints[(project_id, "collection")] == ReasonCheckpoint(
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
    assert project.project.collection_reason_rounds == 1
    assert project.project.collection_explore_rounds == 2
    assert [fact.id for fact in project.facts] == ["origin", "f001", "f002"]
    assert {intent.auth_scope for intent in project.intents} == {"anonymous", "authenticated"}
    assert {intent.task_mode for intent in project.intents} == {"collection"}
    assert {intent.to for intent in project.intents} == {"f001", "f002"}
    assert any("/reason_execute-" in path for _, path, _ in containers.writes)
    assert any("/explore_execute-" in path for _, path, _ in containers.writes)


def test_project_reason_leases_are_independent_by_task_mode(http_client: TestClient) -> None:
    project_id = _create_project(http_client)

    collection = http_client.post(
        f"/projects/{project_id}/reason/claim",
        json={"worker": "collection-worker", "trigger": "initial", "task_mode": "collection"},
    )
    validation = http_client.post(
        f"/projects/{project_id}/reason/claim",
        json={"worker": "validation-worker", "trigger": "stable", "task_mode": "validation"},
    )
    blocked = http_client.post(
        f"/projects/{project_id}/reason/claim",
        json={"worker": "other-worker", "trigger": "again", "task_mode": "collection"},
    )
    release_collection = http_client.post(
        f"/projects/{project_id}/reason/release",
        json={"worker": "collection-worker", "task_mode": "collection"},
    )
    project = ProjectDetail.model_validate(http_client.get(f"/projects/{project_id}").json())

    assert collection.status_code == 200, collection.text
    assert validation.status_code == 200, validation.text
    assert blocked.status_code == 409, blocked.text
    assert release_collection.status_code == 200, release_collection.text
    assert project.project.reasons["collection"] is None
    assert project.project.reasons["validation"] is not None
    assert project.project.reasons["validation"].worker == "validation-worker"


def test_mock_scheduler_collection_stable_does_not_stop_project(http_client: TestClient) -> None:
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
    project_id = _create_project(http_client)

    try:
        _dispatch_and_wait(loop)
        _dispatch_and_wait(loop)
        _dispatch_and_wait(loop)
        _dispatch_and_wait(loop)
        project = client.get_project(project_id)
    finally:
        loop.close()

    assert project.project.status == "active"
    assert project.project.collection_reason_rounds == 2


def test_mock_scheduler_validation_reason_creates_validation_intents(http_client: TestClient) -> None:
    project_id = _create_project(http_client)
    seed = http_client.post(
        f"/projects/{project_id}/intents",
        json={
            "from": ["origin"],
            "description": "validate candidate issue",
            "creator": "seed",
            "worker": "seed",
            "task_mode": "validation",
            "auth_scope": "anonymous",
        },
    )
    assert seed.status_code == 201, seed.text
    conclusion = http_client.post(
        f"/projects/{project_id}/intents/{seed.json()['id']}/conclude",
        json={
            "worker": "seed",
            "description": "candidate finding fact",
            "findings": [{"title": "candidate finding"}],
        },
    )
    assert conclusion.status_code == 200, conclusion.text
    client = InProcessClient(http_client)
    containers = LocalContainerManager()
    loop = _loop(
        _config(
            reason=_phase("intent"),
            task_types=["validation_reason"],
        ),
        client,
        containers,
    )
    loop.server_settings = loop.server_settings.model_copy(update={"initial_collection_rounds": 0})

    try:
        _dispatch_and_wait(loop)
        project = client.get_project(project_id)
    finally:
        loop.close()

    created_validation = [intent for intent in project.intents if intent.creator == "mock-worker"]
    assert created_validation
    assert {intent.task_mode for intent in created_validation} == {"validation"}
    assert any("/reason_execute-" in path for _, path, _ in containers.writes)


def test_mock_scheduler_exercises_collection_validation_report_flow(http_client: TestClient) -> None:
    client = InProcessClient(http_client)
    containers = LocalContainerManager()
    loop = _loop(
        _config(
            reason=_phase("intent"),
            collection_reason=_phase(
                "intent",
                rules=[{"fact_ids_gte": 3, "open_intents_empty": True, "force": "stable"}],
            ),
            validation_reason=_phase("intent"),
        ),
        client,
        containers,
    )
    project_id = _create_project(http_client)

    try:
        _dispatch_and_wait(loop)
        project = client.get_project(project_id)
        baseline_intents = [intent for intent in project.intents if intent.creator == "mock-worker"]
        assert {intent.task_mode for intent in baseline_intents} == {"collection"}
        assert {intent.auth_scope for intent in baseline_intents} == {"anonymous", "authenticated"}
        assert {intent.from_[0] for intent in baseline_intents} == {"origin"}

        _dispatch_and_wait(loop)
        _dispatch_and_wait(loop)
        project = client.get_project(project_id)
        collection_facts = [fact for fact in project.facts if fact.id != "origin"]
        assert len(collection_facts) == 2
        assert {fact.fact_type for fact in collection_facts} == {"feature_surface"}
        assert all(fact.details.get("features") for fact in collection_facts)
        assert all(fact.details.get("apis") for fact in collection_facts)
        assert all(fact.details.get("auth") for fact in collection_facts)
        assert project.findings == []

        _dispatch_and_wait(loop)

        collection_fact_id = collection_facts[0].id
        seed = http_client.post(
            f"/projects/{project_id}/intents",
            json={
                "from": [collection_fact_id],
                "description": "validate mock collection surface",
                "creator": "seed",
                "worker": None,
                "task_mode": "validation",
                "auth_scope": "anonymous",
            },
        )
        assert seed.status_code == 201, seed.text

        _dispatch_and_wait(loop)
        project = client.get_project(project_id)
        assert len(project.findings) == 1
        finding = project.findings[0]
        assert finding.next_action == "report"
        assert finding.report_status == "queued"
        assert finding.report_intent_id is not None

        _dispatch_and_wait(loop)
        project = client.get_project(project_id)
        finding = project.findings[0]
        assert finding.report_status == "drafted"

        _dispatch_and_wait(loop)
        project = client.get_project(project_id)
    finally:
        loop.close()

    validation_intents = [
        intent
        for intent in project.intents
        if intent.creator == "mock-worker" and intent.task_mode == "validation" and intent.to is None
    ]
    assert validation_intents
    assert any(collection_fact_id in intent.from_ for intent in validation_intents)
    assert any("/report_execute-" in path for _, path, _ in containers.writes)
