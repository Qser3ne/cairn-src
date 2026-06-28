from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from cairn.server.db import get_conn
from cairn.server.models import (
    ConcludeTaskRequest,
    ConcludeTaskResponse,
    CreateTaskRequest,
    Finding,
    HeartbeatRequest,
    ReportPathRequest,
    Task,
)
from cairn.server.services import (
    append_node_downstream_task,
    append_task_output,
    check_project_active,
    fact_from_row,
    finding_to_model,
    get_claimable_open_task_or_404,
    get_finding_or_404,
    get_releasable_open_task_or_404,
    get_task_or_404,
    increment_collection_explore_round,
    mark_reason_pending_if_reason_running,
    next_fact_id,
    next_finding_id,
    next_task_id,
    task_sources,
    task_to_model,
    utcnow,
    validate_sources_exist,
)

router = APIRouter(tags=["tasks"])


@router.post("/projects/{project_id}/tasks", response_model=Task, status_code=201)
def create_task(project_id: str, body: CreateTaskRequest):
    with get_conn() as conn:
        check_project_active(conn, project_id)
        validate_sources_exist(conn, project_id, body.from_)
        conn.execute("BEGIN IMMEDIATE")
        task_id = next_task_id(conn, project_id)
        now = utcnow()
        claimed = body.worker is not None
        conn.execute(
            """
            INSERT INTO tasks (
                id, project_id, type, description, creation_time, completion_time,
                "to", worker, last_heartbeat_at, auth_scope
            ) VALUES (?, ?, ?, ?, ?, NULL, '[]', ?, ?, ?)
            """,
            (
                task_id,
                project_id,
                body.type,
                body.description,
                now,
                body.worker,
                now if claimed else None,
                body.auth_scope,
            ),
        )
        for source_id in body.from_:
            conn.execute(
                "INSERT INTO task_sources (task_id, project_id, source_id) VALUES (?, ?, ?)",
                (task_id, project_id, source_id),
            )
            append_node_downstream_task(conn, project_id, source_id, task_id)
        row = get_task_or_404(conn, project_id, task_id)
        return task_to_model(conn, row, project_id, body.from_)


@router.post("/projects/{project_id}/tasks/{task_id}/heartbeat", response_model=Task)
def heartbeat(project_id: str, task_id: str, body: HeartbeatRequest):
    with get_conn() as conn:
        check_project_active(conn, project_id)
        get_claimable_open_task_or_404(conn, project_id, task_id, body.worker)
        now = utcnow()
        conn.execute(
            "UPDATE tasks SET worker = ?, last_heartbeat_at = ? WHERE id = ? AND project_id = ?",
            (body.worker, now, task_id, project_id),
        )
        return task_to_model(conn, get_task_or_404(conn, project_id, task_id), project_id)


@router.post("/projects/{project_id}/tasks/{task_id}/release", response_model=Task)
def release(project_id: str, task_id: str, body: HeartbeatRequest):
    with get_conn() as conn:
        check_project_active(conn, project_id)
        row = get_releasable_open_task_or_404(conn, project_id, task_id, body.worker)
        if row["worker"] == body.worker:
            conn.execute(
                "UPDATE tasks SET worker = NULL WHERE id = ? AND project_id = ?",
                (task_id, project_id),
            )
            row = get_task_or_404(conn, project_id, task_id)
        return task_to_model(conn, row, project_id)


@router.post("/projects/{project_id}/tasks/{task_id}/conclude", response_model=ConcludeTaskResponse)
def conclude(project_id: str, task_id: str, body: ConcludeTaskRequest):
    with get_conn() as conn:
        check_project_active(conn, project_id)
        task = get_claimable_open_task_or_404(conn, project_id, task_id, body.worker)
        if task["type"] == "collection_task" and body.findings:
            raise HTTPException(400, "collection tasks cannot write findings")

        now = utcnow()
        sources = task_sources(conn, project_id, task_id)
        fact_type = "collection_fact" if task["type"] == "collection_task" else "vulnerability_fact"
        fact_id = next_fact_id(conn, project_id)
        conn.execute(
            """
            INSERT INTO facts (
                id, project_id, type, description, creation_time, "from", from_task, "to", evidence
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '[]', ?)
            """,
            (
                fact_id,
                project_id,
                fact_type,
                body.description,
                now,
                json.dumps(sources, ensure_ascii=False),
                task_id,
                body.evidence,
            ),
        )
        append_task_output(conn, project_id, task_id, fact_id)

        findings = []
        for finding in body.findings or []:
            finding_id = next_finding_id(conn, project_id)
            conn.execute(
                """
                INSERT INTO findings (
                    id, project_id, type, description, creation_time, "from", from_task, "to", report
                ) VALUES (?, ?, 'findings', ?, ?, ?, ?, '[]', NULL)
                """,
                (
                    finding_id,
                    project_id,
                    finding.description,
                    now,
                    json.dumps(sources, ensure_ascii=False),
                    task_id,
                ),
            )
            append_task_output(conn, project_id, task_id, finding_id)
            findings.append(finding_to_model(get_finding_or_404(conn, project_id, finding_id)))

        conn.execute(
            """
            UPDATE tasks
            SET worker = ?,
                last_heartbeat_at = ?,
                completion_time = ?
            WHERE id = ? AND project_id = ?
            """,
            (body.worker, now, now, task_id, project_id),
        )
        mark_reason_pending_if_reason_running(conn, project_id)
        if task["type"] == "collection_task":
            increment_collection_explore_round(conn, project_id)
        updated_task = get_task_or_404(conn, project_id, task_id)
        fact = conn.execute(
            "SELECT * FROM facts WHERE id = ? AND project_id = ?",
            (fact_id, project_id),
        ).fetchone()
        return ConcludeTaskResponse(
            fact=fact_from_row(fact),
            task=task_to_model(conn, updated_task, project_id, sources),
            findings=findings,
        )


@router.post("/projects/{project_id}/findings/{finding_id}/report", response_model=Finding)
def update_finding_report(project_id: str, finding_id: str, body: ReportPathRequest):
    with get_conn() as conn:
        check_project_active(conn, project_id)
        get_finding_or_404(conn, project_id, finding_id)
        conn.execute(
            "UPDATE findings SET report = ? WHERE id = ? AND project_id = ?",
            (body.report, finding_id, project_id),
        )
        return finding_to_model(get_finding_or_404(conn, project_id, finding_id))
