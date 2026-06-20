from __future__ import annotations

import logging
import time

from cairn.dispatcher.config import DispatchConfig, WorkerConfig
from cairn.dispatcher.contracts import parse_json_output, validate_judge_payload
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
from cairn.server.models import EphemeralJob

LOG = logging.getLogger(__name__)


def run_judge_task(
    config: DispatchConfig,
    client: CairnClient,
    container_manager: ContainerManager,
    job: EphemeralJob,
    worker: WorkerConfig,
    cancellation: TaskCancellation,
) -> str:
    driver = get_driver(worker.type)
    started = time.perf_counter()
    try:
        claim = client.claim_ephemeral_job(job.id, worker.name)
        if not claim.ok:
            LOG.info("judge claim failed job=%s worker=%s status=%s", job.id, worker.name, claim.status_code)
            return "failed"

        container_name = container_manager.ensure_running(job.project_id)
        if task_healthcheck_enabled(config):
            healthcheck = run_healthcheck(
                container_manager,
                container_name,
                worker,
                driver.build_healthcheck(worker),
                timeout_seconds=config.runtime.healthcheck_timeout,
                cancellation=cancellation,
            )
            cancelled = cancel_reason(healthcheck.result, cancellation)
            if cancelled is not None:
                return "cancelled"
            if healthcheck.result.returncode != 0:
                client.fail_ephemeral_job(job.id, worker.name, "worker healthcheck failed")
                return "unhealthy"

        prompt = render_prompt(
            load_prompt(config.runtime.prompt_group, "judge.md", "recon"),
            {
                "graph_yaml": write_graph_snapshot_reference(
                    container_manager,
                    container_name,
                    job.input_snapshot_yaml.strip(),
                    phase="judge_execute",
                ),
            },
        )
        session = driver.prepare_session()
        command = driver.build_execute(worker, prompt, session)
        result = run_worker_process(
            container_manager,
            container_name,
            worker,
            command.argv,
            phase="judge",
            timeout_seconds=config.tasks.judge.timeout,
            cancellation=cancellation,
        )
        if cancel_reason(result, cancellation) is not None:
            return "cancelled"
        if did_timeout(result) or result.returncode != 0:
            client.fail_ephemeral_job(job.id, worker.name, preview(result.stderr or result.stdout))
            return "failed"
        try:
            payload = parse_json_output(driver.extract_response_text(result.stdout, result.stderr))
            kind, data = validate_judge_payload(payload)
        except Exception as exc:
            client.fail_ephemeral_job(job.id, worker.name, str(exc))
            return "failed"
        if kind == "rejected":
            client.fail_ephemeral_job(job.id, worker.name, "judge rejected")
            return "rejected"
        response = client.finish_ephemeral_job(job.id, worker.name, data or {})
        if not response.ok:
            LOG.warning("judge finish failed job=%s status=%s body=%s", job.id, response.status_code, response.text)
            return "failed"
        LOG.info("judge finished job=%s worker=%s ms=%s", job.id, worker.name, int((time.perf_counter() - started) * 1000))
        return "success"
    except Exception as exc:
        LOG.exception("judge task crashed job=%s worker=%s", job.id, worker.name)
        client.fail_ephemeral_job(job.id, worker.name, str(exc))
        return "failed"
