from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

from cairn.dispatcher.protocol.client import ApiResult
from cairn.dispatcher.runtime.cancellation import TaskCancellation
from cairn.dispatcher.runtime.process import ProcessResult
from cairn.dispatcher.tasks.common import HealthcheckRun
from cairn.dispatcher.tasks import explore, judge, reason, report
from cairn.server.models import EphemeralJob

from conftest import (
    FakeClient,
    FakeContainerManager,
    FakeDriver,
    FakeLease,
    make_account,
    make_config,
    make_intent,
    make_project,
)


def _healthy(*_args, **_kwargs) -> HealthcheckRun:
    return HealthcheckRun(ProcessResult(0, "", ""), duration_ms=1)


def _lease_factory(lease: FakeLease):
    return lambda *_args, **_kwargs: lease


def test_reason_writes_graph_snapshot_and_creates_intent(monkeypatch) -> None:
    config = make_config()
    project = make_project()
    client = FakeClient(project)
    containers = FakeContainerManager()
    driver = FakeDriver()
    lease = FakeLease()
    graph_yaml = "project:\n  title: huge\n" + ("x" * 100_000)

    monkeypatch.setattr(reason, "get_driver", lambda _name: driver)
    monkeypatch.setattr(reason.HeartbeatLease, "for_reason", _lease_factory(lease))
    monkeypatch.setattr(reason, "run_healthcheck", _healthy)
    monkeypatch.setattr(
        reason,
        "run_worker_process",
        lambda *_args, **_kwargs: ProcessResult(
            0,
            '{"accepted":true,"data":{"intents":[{"from":["f001"],"description":"next step"}]}}',
            "",
        ),
    )

    outcome = reason.run_reason_task(
        config,
        client,
        containers,
        project,
        graph_yaml,
        config.workers[0],
        TaskCancellation(),
    )

    assert outcome == "success"
    assert client.created_intents == [("proj_001", ["f001"], "next step", "test-worker", None)]
    assert client.recon_reason_rounds == []
    assert client.released_reasons == [("proj_001", "test-worker")]
    assert lease.started and lease.stopped
    container_name, path, content = containers.writes[0]
    assert container_name == "container-proj_001"
    assert path.startswith("/tmp/cairn-prompts/reason_execute-")
    assert path.endswith("/graph.yaml")
    assert content == graph_yaml
    assert graph_yaml not in driver.execute_prompts[0]
    assert path in driver.execute_prompts[0]


def test_recon_reason_stable_records_stable_round(monkeypatch) -> None:
    config = make_config()
    project = make_project()
    project.project.project_kind = "recon"
    client = FakeClient(project)
    containers = FakeContainerManager()
    lease = FakeLease()

    monkeypatch.setattr(reason, "get_driver", lambda _name: FakeDriver())
    monkeypatch.setattr(reason.HeartbeatLease, "for_reason", _lease_factory(lease))
    monkeypatch.setattr(reason, "run_healthcheck", _healthy)
    monkeypatch.setattr(
        reason,
        "run_worker_process",
        lambda *_args, **_kwargs: ProcessResult(
            0,
            '{"accepted":true,"data":{"decision":"no_new_high_value","intents":[]}}',
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
        TaskCancellation(),
    )

    assert outcome == "success"
    assert client.recon_reason_rounds == [("proj_001", True)]


def test_initial_recon_reason_requires_dual_baseline_intents(monkeypatch) -> None:
    config = make_config()
    project = make_project()
    project.project.project_kind = "recon"
    project.facts = project.facts[:1]
    client = FakeClient(project)
    containers = FakeContainerManager()
    lease = FakeLease()

    monkeypatch.setattr(reason, "get_driver", lambda _name: FakeDriver())
    monkeypatch.setattr(reason.HeartbeatLease, "for_reason", _lease_factory(lease))
    monkeypatch.setattr(reason, "run_healthcheck", _healthy)
    monkeypatch.setattr(
        reason,
        "run_worker_process",
        lambda *_args, **_kwargs: ProcessResult(
            0,
            '{"accepted":true,"data":{"intents":[{"from":["origin"],"auth_scope":"anonymous","description":"public baseline"}]}}',
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
        TaskCancellation(),
    )

    assert outcome == "failed"
    assert client.created_intents == []


def test_reason_complete_payload_is_invalid(monkeypatch) -> None:
    config = make_config()
    project = make_project()
    client = FakeClient(project)
    containers = FakeContainerManager()
    lease = FakeLease()

    monkeypatch.setattr(reason, "get_driver", lambda _name: FakeDriver())
    monkeypatch.setattr(reason.HeartbeatLease, "for_reason", _lease_factory(lease))
    monkeypatch.setattr(reason, "run_healthcheck", _healthy)
    monkeypatch.setattr(
        reason,
        "run_worker_process",
        lambda *_args, **_kwargs: ProcessResult(
            0,
            '{"accepted":true,"data":{"complete":{"from":["f001"],"description":"done"}}}',
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
        TaskCancellation(),
    )

    assert outcome == "failed"
    assert client.created_intents == []
    assert client.released_reasons == [("proj_001", "test-worker")]


def test_explore_early_plain_text_exit_uses_conclude_fallback(monkeypatch) -> None:
    config = make_config()
    intent = make_intent()
    project = make_project(intents=[intent])
    client = FakeClient(project)
    containers = FakeContainerManager()
    driver = FakeDriver()
    lease = FakeLease()
    results: Iterator[ProcessResult] = iter(
        [
            ProcessResult(0, "Need inspect files and keep working.", ""),
            ProcessResult(0, '{"accepted":true,"data":{"description":"confirmed fact"}}', ""),
        ]
    )

    monkeypatch.setattr(explore, "get_driver", lambda _name: driver)
    monkeypatch.setattr(explore.HeartbeatLease, "for_intent", _lease_factory(lease))
    monkeypatch.setattr(explore, "run_healthcheck", _healthy)
    monkeypatch.setattr(explore, "_run_process", lambda *_args, **_kwargs: next(results))

    outcome = explore.run_explore_task(
        config,
        client,
        containers,
        project,
        "facts:\n- id: f001\n",
        intent,
        config.workers[0],
        TaskCancellation(),
    )

    assert outcome == "success"
    assert client.concluded == [("proj_001", "i001", "test-worker", "confirmed fact")]
    assert len(containers.writes) == 2
    assert "/explore_execute-" in containers.writes[0][1]
    assert "/explore_conclude-" in containers.writes[1][1]
    assert len(driver.execute_prompts) == 1
    assert len(driver.conclude_prompts) == 1
    assert lease.started and lease.stopped


def test_recon_explore_success_does_not_record_round_in_worker(monkeypatch) -> None:
    config = make_config()
    intent = make_intent()
    project = make_project(intents=[intent])
    project.project.project_kind = "recon"
    client = FakeClient(project)
    containers = FakeContainerManager()
    lease = FakeLease()

    monkeypatch.setattr(explore, "get_driver", lambda _name: FakeDriver())
    monkeypatch.setattr(explore.HeartbeatLease, "for_intent", _lease_factory(lease))
    monkeypatch.setattr(explore, "run_healthcheck", _healthy)
    monkeypatch.setattr(
        explore,
        "_run_process",
        lambda *_args, **_kwargs: ProcessResult(0, '{"accepted":true,"data":{"description":"fact"}}', ""),
    )

    outcome = explore.run_explore_task(
        config,
        client,
        containers,
        project,
        "graph",
        intent,
        config.workers[0],
        TaskCancellation(),
    )

    assert outcome == "success"
    assert client.recon_explore_rounds == []


def test_explore_healthcheck_failure_releases_claim(monkeypatch) -> None:
    config = make_config()
    config.runtime.worker_healthcheck = "startup_and_task"
    intent = make_intent()
    project = make_project(intents=[intent])
    client = FakeClient(project)
    containers = FakeContainerManager()
    lease = FakeLease()

    monkeypatch.setattr(explore, "get_driver", lambda _name: FakeDriver())
    monkeypatch.setattr(explore.HeartbeatLease, "for_intent", _lease_factory(lease))
    monkeypatch.setattr(
        explore,
        "run_healthcheck",
        lambda *_args, **_kwargs: HealthcheckRun(ProcessResult(1, "", "unhealthy"), duration_ms=1),
    )

    outcome = explore.run_explore_task(
        config,
        client,
        containers,
        project,
        "graph",
        intent,
        config.workers[0],
        TaskCancellation(),
    )

    assert outcome == "unhealthy"
    assert client.released == [("proj_001", "i001", "test-worker")]
    assert containers.writes == []


def test_reason_startup_only_mode_skips_task_healthcheck(monkeypatch) -> None:
    config = make_config()
    config.runtime.worker_healthcheck = "startup_only"
    project = make_project()
    client = FakeClient(project)
    containers = FakeContainerManager()
    lease = FakeLease()

    monkeypatch.setattr(reason, "get_driver", lambda _name: FakeDriver())
    monkeypatch.setattr(reason.HeartbeatLease, "for_reason", _lease_factory(lease))
    monkeypatch.setattr(
        reason,
        "run_healthcheck",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("task healthcheck should be skipped")),
    )
    monkeypatch.setattr(
        reason,
        "run_worker_process",
        lambda *_args, **_kwargs: ProcessResult(
            0,
            '{"accepted":true,"data":{"intents":[{"from":["f001"],"description":"next"}]}}',
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
        TaskCancellation(),
    )

    assert outcome == "success"
    assert client.created_intents == [("proj_001", ["f001"], "next", "test-worker", None)]


def test_authenticated_explore_prompt_includes_leased_account(monkeypatch) -> None:
    config = make_config()
    intent = make_intent()
    intent.auth_scope = "authenticated"
    account = make_account("a001")
    project = make_project(intents=[intent])
    project.project.auth_mode = "authenticated"
    project.accounts = [account]
    client = FakeClient(project)
    containers = FakeContainerManager()
    driver = FakeDriver()
    lease = FakeLease()

    monkeypatch.setattr(explore, "get_driver", lambda _name: driver)
    monkeypatch.setattr(explore.HeartbeatLease, "for_intent", _lease_factory(lease))
    monkeypatch.setattr(explore, "run_healthcheck", _healthy)
    monkeypatch.setattr(
        explore,
        "_run_process",
        lambda *_args, **_kwargs: ProcessResult(
            0,
            '{"accepted":true,"data":{"description":"authenticated fact"}}',
            "",
        ),
    )

    outcome = explore.run_explore_task(
        config,
        client,
        containers,
        project,
        "graph",
        intent,
        config.workers[0],
        TaskCancellation(),
        account,
    )

    assert outcome == "success"
    prompt = driver.execute_prompts[0]
    assert "auth_scope is authenticated" in prompt
    assert "account_id: a001" in prompt
    assert "username: user-a001" in prompt
    assert "password: pass-a001" in prompt
    assert "/home/kali/workspace/auth/proj_001/a001" in prompt


@dataclass
class FakeEphemeralClient:
    claimed: list[tuple[str, str]] = field(default_factory=list)
    finished: list[tuple[str, str, dict]] = field(default_factory=list)
    failed: list[tuple[str, str, str]] = field(default_factory=list)

    def claim_ephemeral_job(self, job_id: str, worker: str) -> ApiResult:
        self.claimed.append((job_id, worker))
        return ApiResult(200, {})

    def finish_ephemeral_job(self, job_id: str, worker: str, result: dict) -> ApiResult:
        self.finished.append((job_id, worker, result))
        return ApiResult(200, {})

    def fail_ephemeral_job(self, job_id: str, worker: str, error: str) -> ApiResult:
        self.failed.append((job_id, worker, error))
        return ApiResult(200, {})


def test_judge_task_finishes_ephemeral_job_without_graph_writes(monkeypatch) -> None:
    config = make_config()
    client = FakeEphemeralClient()
    containers = FakeContainerManager()
    driver = FakeDriver()
    job = EphemeralJob(
        id="job_001",
        project_id="proj_001",
        job_type="judge",
        status="queued",
        input_snapshot_yaml="project:\n  project_kind: recon\n",
        created_at="2026-01-01T00:00:00Z",
        expires_at="2026-01-02T00:00:00Z",
    )

    monkeypatch.setattr(judge, "get_driver", lambda _name: driver)
    monkeypatch.setattr(judge, "run_healthcheck", _healthy)
    monkeypatch.setattr(
        judge,
        "run_worker_process",
        lambda *_args, **_kwargs: ProcessResult(
            0,
            '{"accepted":true,"data":{"verdict":"ready","summary":"enough signal"}}',
            "",
        ),
    )

    outcome = judge.run_judge_task(config, client, containers, job, config.workers[0], TaskCancellation())

    assert outcome == "success"
    assert client.claimed == [("job_001", "test-worker")]
    assert client.finished == [("job_001", "test-worker", {"verdict": "ready", "summary": "enough signal"})]
    assert containers.writes[0][1].startswith("/tmp/cairn-prompts/judge_execute-")


def test_report_task_writes_report_artifact(monkeypatch) -> None:
    config = make_config()
    intent = make_intent()
    intent.intent_kind = "report"
    project = make_project(intents=[intent])
    client = FakeClient(project)
    containers = FakeContainerManager()
    lease = FakeLease()

    monkeypatch.setattr(report, "get_driver", lambda _name: FakeDriver())
    monkeypatch.setattr(report.HeartbeatLease, "for_intent", _lease_factory(lease))
    monkeypatch.setattr(report, "run_healthcheck", _healthy)
    monkeypatch.setattr(
        report,
        "run_worker_process",
        lambda *_args, **_kwargs: ProcessResult(
            0,
            '{"accepted":true,"data":{"report_markdown":"# Report","report_json":{"severity":"high"}}}',
            "",
        ),
    )

    outcome = report.run_report_task(
        config,
        client,
        containers,
        project,
        "graph",
        intent,
        config.workers[0],
        TaskCancellation(),
    )

    assert outcome == "success"
    assert lease.started and lease.stopped
