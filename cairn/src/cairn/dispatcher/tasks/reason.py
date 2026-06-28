from __future__ import annotations

import logging
import time

from cairn.dispatcher.config import DispatchConfig, WorkerConfig
from cairn.dispatcher.contracts import parse_json_output, validate_reason_payload
from cairn.dispatcher.prompting import (
    format_fact_ids,
    format_open_tasks,
    load_prompt,
    render_prompt,
)
from cairn.dispatcher.protocol.client import CairnClient
from cairn.dispatcher.runtime.cancellation import TaskCancellation
from cairn.dispatcher.runtime.containers import ContainerManager
from cairn.dispatcher.runtime.heartbeat import HeartbeatLease
from cairn.dispatcher.tasks.common import (
    best_effort_release_reason,
    cancel_reason,
    did_timeout,
    preview,
    run_healthcheck,
    run_worker_process,
    task_healthcheck_enabled,
    write_graph_snapshot_reference,
)
from cairn.dispatcher.workers.registry import get_driver
from cairn.server.models import ProjectDetail, TaskMode

LOG = logging.getLogger(__name__)


def run_reason_task(
    config: DispatchConfig,
    client: CairnClient,
    container_manager: ContainerManager,
    project: ProjectDetail,
    export_yaml: str,
    worker: WorkerConfig,
    task_mode: TaskMode,
    cancellation: TaskCancellation,
) -> str:
    driver = get_driver(worker.type)
    task_started = time.perf_counter()
    healthcheck_timeout = config.runtime.healthcheck_timeout
    lease = HeartbeatLease.for_reason(client, project.project.id, worker.name, task_mode, config.runtime.interval)
    lease.start()
    try:
        container_name = container_manager.ensure_running(project.project.id)

        if task_healthcheck_enabled(config):
            LOG.info(
                "starting container exec project=%s worker=%s phase=reason_healthcheck timeout=%ss",
                project.project.id,
                worker.name,
                healthcheck_timeout,
            )
            healthcheck = run_healthcheck(
                container_manager,
                container_name,
                worker,
                driver.build_healthcheck(worker),
                timeout_seconds=healthcheck_timeout,
                lease=lease,
                cancellation=cancellation,
            )
            cancelled = cancel_reason(healthcheck.result, cancellation)
            if cancelled is not None:
                LOG.info(
                    "reason cancelled during healthcheck project=%s worker=%s reason=%s",
                    project.project.id,
                    worker.name,
                    cancelled,
                )
                return "cancelled"
            if lease.failure is not None:
                LOG.warning(
                    "heartbeat lost during reason healthcheck project=%s worker=%s status=%s",
                    project.project.id,
                    worker.name,
                    lease.failure.status_code,
                )
                return "failed"
            if healthcheck.result.returncode != 0:
                LOG.warning(
                    "worker unhealthy project=%s worker=%s healthcheck_ms=%s stderr=%s",
                    project.project.id,
                    worker.name,
                    healthcheck.duration_ms,
                    preview(healthcheck.result.stderr),
                )
                return "unhealthy"
        open_tasks = [
            {
                "id": task.id,
                "from": task.from_,
                "description": task.description,
                "worker": task.worker,
                "auth_scope": task.auth_scope,
                "task_mode": task.task_mode,
            }
            for task in project.tasks
            if task.completion_time is None and task.task_mode == task_mode
        ]
        allowed_fact_ids = ["origin", *[fact.id for fact in project.facts]]
        is_initial_collection = (
            task_mode == "collection"
            and not project.facts
            and not open_tasks
        )
        initial_collection_scopes = {"anonymous", "authenticated"} if project.accounts else {"anonymous"}
        max_tasks = (
            max(config.tasks.reason.max_tasks, len(initial_collection_scopes))
            if is_initial_collection
            else config.tasks.reason.max_tasks
        )
        LOG.debug(
            "reason context prepared project=%s worker=%s facts=%s allowed_fact_ids=%s hints=%s open_tasks=%s",
            project.project.id,
            worker.name,
            len(project.facts),
            len(allowed_fact_ids),
            len(project.hints),
            len(open_tasks),
        )
        prompt = render_prompt(
            load_prompt(config.runtime.prompt_group, "reason.md", task_mode),
            {
                "graph_yaml": write_graph_snapshot_reference(
                    container_manager,
                    container_name,
                    export_yaml.strip(),
                    phase="reason_execute",
                ),
                "fact_ids": format_fact_ids(allowed_fact_ids),
                "open_tasks": format_open_tasks(open_tasks),
                "max_tasks": str(max_tasks),
                "project_kind": project.project.project_kind,
                "task_mode": task_mode,
                "has_accounts": "true" if project.accounts else "false",
            },
        )

        session = driver.prepare_session()
        command = driver.build_execute(worker, prompt, session)
        execute_started = time.perf_counter()
        result = run_worker_process(
            container_manager,
            container_name,
            worker,
            command.argv,
            phase="reason_execute",
            timeout_seconds=config.tasks.reason.timeout,
            lease=lease,
            cancellation=cancellation,
        )
        execute_ms = int((time.perf_counter() - execute_started) * 1000)
        total_ms = int((time.perf_counter() - task_started) * 1000)
        session = driver.extract_session(session, result.stdout, result.stderr)
        cancelled = cancel_reason(result, cancellation)
        if cancelled is not None:
            LOG.info(
                "reason cancelled project=%s worker=%s reason=%s execute_ms=%s",
                project.project.id,
                worker.name,
                cancelled,
                execute_ms,
            )
            return "cancelled"
        if lease.failure is not None:
            LOG.warning(
                "heartbeat lost during reason project=%s worker=%s status=%s execute_ms=%s",
                project.project.id,
                worker.name,
                lease.failure.status_code,
                execute_ms,
            )
            return "failed"
        if did_timeout(result):
            LOG.warning(
                "reason timed out project=%s worker=%s execute_ms=%s total_ms=%s stdout_preview=%s stderr_preview=%s",
                project.project.id,
                worker.name,
                execute_ms,
                total_ms,
                preview(result.stdout),
                preview(result.stderr),
            )
            return "failed"
        if result.returncode != 0:
            LOG.warning(
                "reason command failed project=%s worker=%s code=%s execute_ms=%s total_ms=%s stdout_preview=%s stderr_preview=%s",
                project.project.id,
                worker.name,
                result.returncode,
                execute_ms,
                total_ms,
                preview(result.stdout),
                preview(result.stderr),
            )
            return "failed"
        try:
            model_output = driver.extract_response_text(result.stdout, result.stderr)
            payload = parse_json_output(model_output)
            kind, data = validate_reason_payload(
                payload,
                open_tasks_empty=not open_tasks,
                max_tasks=max_tasks,
                task_mode=task_mode,
                require_auth_scope=task_mode == "collection",
            )
            if is_initial_collection:
                scopes = {
                    task.get("auth_scope")
                    for task in data
                    if isinstance(task, dict) and task.get("type") == "collection_task"
                } if kind == "tasks" and isinstance(data, list) else set()
                if scopes != initial_collection_scopes:
                    expected = " and ".join(sorted(initial_collection_scopes))
                    raise ValueError(f"initial collection requires {expected} baseline tasks")
        except Exception as exc:
            LOG.warning(
                "reason parse failed project=%s worker=%s error=%s execute_ms=%s total_ms=%s stdout_preview=%s stderr_preview=%s",
                project.project.id,
                worker.name,
                exc,
                execute_ms,
                total_ms,
                preview(result.stdout),
                preview(result.stderr),
            )
            return "failed"
        if kind == "rejected":
            LOG.warning(
                "reason rejected project=%s worker=%s execute_ms=%s total_ms=%s stdout_preview=%s",
                project.project.id,
                worker.name,
                execute_ms,
                total_ms,
                preview(result.stdout),
            )
            return "rejected"
        if kind == "tasks":
            created = 0
            for task_data in data:
                if lease.failure is not None:
                    LOG.warning(
                        "reason heartbeat failed before task write project=%s worker=%s status=%s body=%s",
                        project.project.id,
                        worker.name,
                        lease.failure.status_code,
                        lease.failure.text,
                    )
                    return "failed"
                response = client.create_task(
                    project.project.id,
                    task_data["from"],
                    task_data["description"],
                    task_type=task_data.get("type") or ("collection_task" if task_mode == "collection" else "vulnerability_task"),
                    auth_scope=task_data.get("auth_scope"),
                )
                if lease.failure is not None:
                    LOG.warning(
                        "reason heartbeat failed after task write project=%s worker=%s status=%s body=%s",
                        project.project.id,
                        worker.name,
                        lease.failure.status_code,
                        lease.failure.text,
                    )
                    return "failed"
                if response.status_code == 403:
                    LOG.info("project became inactive during reason task create project=%s worker=%s created=%s", project.project.id, worker.name, created)
                    return "success"
                if response.status_code == 409:
                    LOG.info(
                        "duplicate task skipped project=%s worker=%s from=%s auth_scope=%s description=%s",
                        project.project.id,
                        worker.name,
                        task_data["from"],
                        task_data.get("auth_scope"),
                        task_data["description"],
                    )
                    continue
                if not response.ok:
                    LOG.warning(
                        "reason task write failed project=%s worker=%s status=%s body=%s",
                        project.project.id,
                        worker.name,
                        response.status_code,
                        response.text,
                    )
                    return "failed"
                created += 1
                LOG.info(
                    "reason created task project=%s worker=%s from=%s auth_scope=%s description=%s",
                    project.project.id,
                    worker.name,
                    task_data["from"],
                    task_data.get("auth_scope"),
                    task_data["description"],
                )
            LOG.info(
                "reason finished project=%s worker=%s created_tasks=%s/%s execute_ms=%s total_ms=%s",
                project.project.id,
                worker.name,
                created,
                len(data),
                execute_ms,
                total_ms,
            )
            if task_mode == "collection":
                if lease.failure is not None:
                    LOG.warning(
                        "reason heartbeat failed before collection round write project=%s worker=%s status=%s body=%s",
                        project.project.id,
                        worker.name,
                        lease.failure.status_code,
                        lease.failure.text,
                    )
                    return "failed"
                response = client.record_collection_reason_round(project.project.id, stable=False)
                if not response.ok and response.status_code not in (403, 409):
                    LOG.warning(
                        "collection reason round update failed project=%s worker=%s status=%s body=%s",
                        project.project.id,
                        worker.name,
                        response.status_code,
                        response.text,
                    )
                    return "failed"
                if lease.failure is not None:
                    LOG.warning(
                        "reason heartbeat failed after collection round write project=%s worker=%s status=%s body=%s",
                        project.project.id,
                        worker.name,
                        lease.failure.status_code,
                        lease.failure.text,
                    )
                    return "failed"
            return "success"
        if kind in ("noop", "stable"):
            if task_mode == "collection":
                if lease.failure is not None:
                    LOG.warning(
                        "reason heartbeat failed before collection round write project=%s worker=%s status=%s body=%s",
                        project.project.id,
                        worker.name,
                        lease.failure.status_code,
                        lease.failure.text,
                    )
                    return "failed"
                response = client.record_collection_reason_round(project.project.id, stable=(kind == "stable"))
                if not response.ok and response.status_code not in (403, 409):
                    LOG.warning(
                        "collection reason round update failed project=%s worker=%s status=%s body=%s",
                        project.project.id,
                        worker.name,
                        response.status_code,
                        response.text,
                    )
                    return "failed"
                if lease.failure is not None:
                    LOG.warning(
                        "reason heartbeat failed after collection round write project=%s worker=%s status=%s body=%s",
                        project.project.id,
                        worker.name,
                        lease.failure.status_code,
                        lease.failure.text,
                    )
                    return "failed"
        LOG.info(
            "reason finished without graph change project=%s worker=%s execute_ms=%s total_ms=%s",
            project.project.id,
            worker.name,
            execute_ms,
            total_ms,
        )
        return "success"
    finally:
        lease.stop()
        best_effort_release_reason(client, project.project.id, worker.name, task_mode)
