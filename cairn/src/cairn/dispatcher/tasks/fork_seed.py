from __future__ import annotations

import logging
import time

from cairn.dispatcher.config import DispatchConfig, WorkerConfig
from cairn.dispatcher.contracts import parse_json_output, validate_fork_seed_payload
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


def _fail_cancelled_job(client: CairnClient, job: EphemeralJob, worker: WorkerConfig, reason: str) -> None:
    response = client.fail_ephemeral_job(job.id, worker.name, f"fork_seed cancelled: {reason}")
    if not response.ok and response.status_code != 409:
        LOG.warning(
            "fork_seed cancel fail update failed job=%s worker=%s status=%s body=%s",
            job.id,
            worker.name,
            response.status_code,
            response.text,
        )


def run_fork_seed_task(
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
            LOG.info("fork_seed claim failed job=%s worker=%s status=%s", job.id, worker.name, claim.status_code)
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
                _fail_cancelled_job(client, job, worker, cancelled)
                return "cancelled"
            if healthcheck.result.returncode != 0:
                client.fail_ephemeral_job(job.id, worker.name, "worker healthcheck failed")
                return "unhealthy"

        prompt = render_prompt(
            load_prompt(config.runtime.prompt_group, "fork_seed.md", "recon"),
            {
                "graph_yaml": write_graph_snapshot_reference(
                    container_manager,
                    container_name,
                    job.input_snapshot_yaml.strip(),
                    phase="fork_seed_execute",
                ),
                "max_seed_facts": str(config.tasks.fork_seed.max_seed_facts),
            },
        )
        session = driver.prepare_session()
        command = driver.build_execute(worker, prompt, session)
        result = run_worker_process(
            container_manager,
            container_name,
            worker,
            command.argv,
            phase="fork_seed",
            timeout_seconds=config.tasks.fork_seed.timeout,
            cancellation=cancellation,
        )
        cancelled = cancel_reason(result, cancellation)
        if cancelled is not None:
            _fail_cancelled_job(client, job, worker, cancelled)
            return "cancelled"
        if did_timeout(result) or result.returncode != 0:
            client.fail_ephemeral_job(job.id, worker.name, preview(result.stderr or result.stdout))
            return "failed"
        try:
            payload = parse_json_output(driver.extract_response_text(result.stdout, result.stderr))
            kind, data = validate_fork_seed_payload(
                payload,
                job.input_snapshot_yaml,
                max_seed_facts=config.tasks.fork_seed.max_seed_facts,
            )
        except Exception as exc:
            client.fail_ephemeral_job(job.id, worker.name, str(exc))
            return "failed"
        if kind == "rejected":
            client.fail_ephemeral_job(job.id, worker.name, "fork_seed rejected")
            return "rejected"
        assert data is not None
        response = client.finish_fork_seed_job(job.id, worker.name, data["seed_facts"])
        if not response.ok:
            LOG.warning("fork_seed finish failed job=%s status=%s body=%s", job.id, response.status_code, response.text)
            return "failed"
        LOG.info("fork_seed finished job=%s worker=%s ms=%s", job.id, worker.name, int((time.perf_counter() - started) * 1000))
        return "success"
    except Exception as exc:
        LOG.exception("fork_seed task crashed job=%s worker=%s", job.id, worker.name)
        client.fail_ephemeral_job(job.id, worker.name, str(exc))
        return "failed"
