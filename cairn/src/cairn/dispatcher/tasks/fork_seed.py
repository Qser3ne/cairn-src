from __future__ import annotations

import logging

from cairn.dispatcher.config import DispatchConfig, WorkerConfig
from cairn.dispatcher.protocol.client import CairnClient
from cairn.dispatcher.runtime.cancellation import TaskCancellation
from cairn.dispatcher.runtime.containers import ContainerManager
from cairn.server.models import EphemeralJob

LOG = logging.getLogger(__name__)

def run_fork_seed_task(
    config: DispatchConfig,
    client: CairnClient,
    container_manager: ContainerManager,
    job: EphemeralJob,
    worker: WorkerConfig,
    cancellation: TaskCancellation,
) -> str:
    _ = (config, container_manager, cancellation)
    response = client.fail_ephemeral_job(job.id, worker.name, "fork_seed jobs have been retired")
    if not response.ok and response.status_code != 409:
        LOG.warning(
            "retired fork_seed fail update failed job=%s worker=%s status=%s body=%s",
            job.id,
            worker.name,
            response.status_code,
            response.text,
        )
    return "failed"
