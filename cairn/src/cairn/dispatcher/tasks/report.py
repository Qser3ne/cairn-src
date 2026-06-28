from __future__ import annotations

import logging
import time

from cairn.dispatcher.config import DispatchConfig, WorkerConfig
from cairn.dispatcher.contracts import parse_json_output, validate_report_payload
from cairn.dispatcher.prompting import load_prompt, render_prompt
from cairn.dispatcher.protocol.client import CairnClient
from cairn.dispatcher.runtime.cancellation import TaskCancellation
from cairn.dispatcher.runtime.containers import ContainerManager
from cairn.dispatcher.tasks.common import (
    cancel_reason,
    did_timeout,
    preview,
    run_healthcheck,
    run_worker_process,
    task_healthcheck_enabled,
    write_graph_snapshot_reference,
)
from cairn.dispatcher.workers.registry import get_driver
from cairn.server.models import Finding, ProjectDetail

LOG = logging.getLogger(__name__)


def run_report_task(
    config: DispatchConfig,
    client: CairnClient,
    container_manager: ContainerManager,
    project: ProjectDetail,
    export_yaml: str,
    finding: Finding,
    worker: WorkerConfig,
    cancellation: TaskCancellation,
) -> str:
    driver = get_driver(worker.type)
    task_started = time.perf_counter()
    try:
        container_name = container_manager.ensure_running(project.project.id)
        if task_healthcheck_enabled(config):
            healthcheck = run_healthcheck(
                container_manager,
                container_name,
                worker,
                driver.build_healthcheck(worker),
                timeout_seconds=config.runtime.healthcheck_timeout,
                cancellation=cancellation,
            )
            if cancel_reason(healthcheck.result, cancellation) is not None:
                return "cancelled"
            if healthcheck.result.returncode != 0:
                return "unhealthy"

        prompt = render_prompt(
            load_prompt(config.runtime.prompt_group, "report.md", "report"),
            {
                "graph_yaml": write_graph_snapshot_reference(
                    container_manager,
                    container_name,
                    export_yaml.strip(),
                    phase="report_execute",
                ),
                "finding_id": finding.id,
                "finding_description": finding.description,
                "intent_id": finding.id,
                "intent_description": finding.description,
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
            cancellation=cancellation,
        )
        if cancel_reason(result, cancellation) is not None:
            return "cancelled"
        if did_timeout(result) or result.returncode != 0:
            LOG.warning(
                "report failed project=%s finding=%s worker=%s stdout=%s stderr=%s",
                project.project.id,
                finding.id,
                worker.name,
                preview(result.stdout),
                preview(result.stderr),
            )
            return "failed"
        try:
            payload = parse_json_output(driver.extract_response_text(result.stdout, result.stderr))
            kind, data = validate_report_payload(payload)
        except Exception as exc:
            LOG.warning("report parse failed project=%s finding=%s error=%s", project.project.id, finding.id, exc)
            return "failed"
        if kind == "rejected":
            return "rejected"
        assert data is not None
        response = client.conclude_report(project.project.id, finding.id, worker.name, data["report"])
        if not response.ok:
            LOG.warning(
                "report path write failed project=%s finding=%s status=%s body=%s",
                project.project.id,
                finding.id,
                response.status_code,
                response.text,
            )
            return "failed"
        LOG.info(
            "report path written project=%s finding=%s worker=%s total_ms=%s",
            project.project.id,
            finding.id,
            worker.name,
            int((time.perf_counter() - task_started) * 1000),
        )
        return "success"
    except Exception:
        LOG.exception("report task crashed project=%s finding=%s worker=%s", project.project.id, finding.id, worker.name)
        return "failed"
