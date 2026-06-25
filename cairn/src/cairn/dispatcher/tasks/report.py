from __future__ import annotations

import logging
import time

from cairn.dispatcher.config import DispatchConfig, WorkerConfig
from cairn.dispatcher.contracts import parse_json_output, validate_report_payload
from cairn.dispatcher.prompting import load_prompt, render_prompt
from cairn.dispatcher.protocol.client import CairnClient
from cairn.dispatcher.runtime.cancellation import TaskCancellation
from cairn.dispatcher.runtime.containers import ContainerManager
from cairn.dispatcher.runtime.heartbeat import HeartbeatLease
from cairn.dispatcher.tasks.common import (
    best_effort_release,
    cancel_reason,
    did_timeout,
    preview,
    run_healthcheck,
    run_worker_process,
    task_healthcheck_enabled,
    write_graph_snapshot_reference,
)
from cairn.dispatcher.workers.registry import get_driver
from cairn.server.models import Intent, ProjectDetail

LOG = logging.getLogger(__name__)


def run_report_task(
    config: DispatchConfig,
    client: CairnClient,
    container_manager: ContainerManager,
    project: ProjectDetail,
    export_yaml: str,
    intent: Intent,
    worker: WorkerConfig,
    cancellation: TaskCancellation,
) -> str:
    driver = get_driver(worker.type)
    task_started = time.perf_counter()
    lease = HeartbeatLease.for_intent(client, project.project.id, intent.id, worker.name, config.runtime.interval)
    lease.start()
    try:
        container_name = container_manager.ensure_running(project.project.id)
        if task_healthcheck_enabled(config):
            healthcheck = run_healthcheck(
                container_manager,
                container_name,
                worker,
                driver.build_healthcheck(worker),
                timeout_seconds=config.runtime.healthcheck_timeout,
                lease=lease,
                cancellation=cancellation,
            )
            if cancel_reason(healthcheck.result, cancellation) is not None:
                best_effort_release(client, project.project.id, intent.id, worker.name)
                return "cancelled"
            if lease.failure is not None:
                best_effort_release(client, project.project.id, intent.id, worker.name)
                return "failed"
            if healthcheck.result.returncode != 0:
                best_effort_release(client, project.project.id, intent.id, worker.name)
                return "unhealthy"

        prompt = render_prompt(
            load_prompt(config.runtime.prompt_group, "report.md", project.project.project_kind),
            {
                "graph_yaml": write_graph_snapshot_reference(
                    container_manager,
                    container_name,
                    export_yaml.strip(),
                    phase="report_execute",
                ),
                "intent_id": intent.id,
                "intent_description": intent.description,
            },
        )
        session = driver.prepare_session()
        command = driver.build_execute(worker, prompt, session)
        result = run_worker_process(
            container_manager,
            container_name,
            worker,
            command.argv,
            phase="report",
            timeout_seconds=config.tasks.report.timeout,
            lease=lease,
            cancellation=cancellation,
        )
        if cancel_reason(result, cancellation) is not None:
            best_effort_release(client, project.project.id, intent.id, worker.name)
            return "cancelled"
        if lease.failure is not None:
            best_effort_release(client, project.project.id, intent.id, worker.name)
            return "failed"
        if did_timeout(result) or result.returncode != 0:
            LOG.warning(
                "report failed project=%s intent=%s worker=%s stdout=%s stderr=%s",
                project.project.id,
                intent.id,
                worker.name,
                preview(result.stdout),
                preview(result.stderr),
            )
            best_effort_release(client, project.project.id, intent.id, worker.name)
            return "failed"
        try:
            payload = parse_json_output(driver.extract_response_text(result.stdout, result.stderr))
            kind, data = validate_report_payload(payload)
        except Exception as exc:
            LOG.warning("report parse failed project=%s intent=%s error=%s", project.project.id, intent.id, exc)
            best_effort_release(client, project.project.id, intent.id, worker.name)
            return "failed"
        if kind == "rejected":
            best_effort_release(client, project.project.id, intent.id, worker.name)
            return "rejected"
        assert data is not None
        response = client.conclude_report(
            project.project.id,
            intent.id,
            worker.name,
            data["report_markdown"],
            data["report_json"],
        )
        if not response.ok:
            LOG.warning("report write failed project=%s intent=%s status=%s body=%s", project.project.id, intent.id, response.status_code, response.text)
            best_effort_release(client, project.project.id, intent.id, worker.name)
            return "failed"
        LOG.info(
            "report drafted project=%s intent=%s worker=%s total_ms=%s",
            project.project.id,
            intent.id,
            worker.name,
            int((time.perf_counter() - task_started) * 1000),
        )
        return "success"
    finally:
        lease.stop()
