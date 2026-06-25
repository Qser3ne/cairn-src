import json

from fastapi import APIRouter, HTTPException

from cairn.server.db import get_conn
from cairn.server.models import (
    ConcludeRequest,
    ConcludeResponse,
    CreateIntentRequest,
    HeartbeatRequest,
    Intent,
    ReportConcludeRequest,
    FindingReport,
)
from cairn.server.services import (
    check_duplicate_intent,
    check_project_active,
    fact_from_row,
    finding_to_model,
    finding_report_to_model,
    get_finding_or_404,
    get_claimable_open_intent_or_404,
    get_releasable_open_intent_or_404,
    increment_collection_explore_round,
    intent_to_model,
    mark_reason_pending_if_reason_running,
    next_fact_id,
    next_finding_id,
    next_intent_id,
    next_report_id,
    utcnow,
    validate_facts_exist,
    validate_intent_creator_worker,
)

router = APIRouter(tags=["intents"])


@router.post(
    "/projects/{project_id}/intents",
    response_model=Intent,
    status_code=201,
)
def create_intent(project_id: str, body: CreateIntentRequest):
    with get_conn() as conn:
        check_project_active(conn, project_id)
        validate_facts_exist(conn, project_id, body.from_)
        validate_intent_creator_worker(body.creator, body.worker)
        project = conn.execute(
            "SELECT project_kind, auth_mode FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        if project is None:
            raise HTTPException(404, "Project not found")

        if body.intent_kind == "report":
            body.task_mode = "report"
        elif body.task_mode is None:
            body.task_mode = "validation"
        elif body.task_mode == "report":
            raise HTTPException(400, "task_mode=report requires a report intent")

        if body.intent_kind == "explore" and body.task_mode == "collection" and body.auth_scope is None:
            raise HTTPException(400, "collection intents require auth_scope")
        if body.intent_kind == "explore" and body.task_mode == "collection" and body.auth_scope == "authenticated":
            account_count = conn.execute(
                "SELECT COUNT(*) AS count FROM project_accounts WHERE project_id = ?", (project_id,)
            ).fetchone()["count"]
            if account_count == 0:
                raise HTTPException(400, "authenticated collection intents require project accounts")
        if body.intent_kind == "explore" and body.auth_scope is None:
            body.auth_scope = "anonymous" if project["auth_mode"] == "dual" else project["auth_mode"]
        if (
            body.intent_kind == "explore"
            and body.task_mode == "validation"
            and project["project_kind"] == "vuln"
            and project["auth_mode"] != "dual"
            and body.auth_scope != project["auth_mode"]
        ):
            raise HTTPException(400, "vuln intent auth_scope must match project auth_mode")
        if body.intent_kind == "report":
            if project["project_kind"] != "vuln":
                raise HTTPException(400, "report intents are only supported for vuln projects")
            if not body.finding_id:
                raise HTTPException(400, "finding_id is required for report intents")
            get_finding_or_404(conn, project_id, body.finding_id)
            body.auth_scope = None
        check_duplicate_intent(conn, project_id, body.from_, body.description, body.auth_scope)
        now = utcnow()
        iid = next_intent_id(conn, project_id)
        claimed = body.worker is not None
        conn.execute(
            """
            INSERT INTO intents (
                id,
                project_id,
                to_fact_id,
                description,
                creator,
                worker,
                last_heartbeat_at,
                created_at,
                concluded_at,
                intent_kind,
                task_mode,
                finding_id,
                auth_scope
            ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)
            """,
            (
                iid,
                project_id,
                body.description,
                body.creator,
                body.worker,
                now if claimed else None,
                now,
                body.intent_kind,
                body.task_mode,
                body.finding_id,
                body.auth_scope,
            ),
        )
        for fid in body.from_:
            conn.execute(
                "INSERT INTO intent_sources (intent_id, project_id, fact_id) VALUES (?, ?, ?)",
                (iid, project_id, fid),
            )

        return Intent(
            id=iid,
            **{"from": body.from_},
            to=None,
            description=body.description,
            creator=body.creator,
            worker=body.worker,
            last_heartbeat_at=now if claimed else None,
            created_at=now,
            concluded_at=None,
            intent_kind=body.intent_kind,
            task_mode=body.task_mode,
            finding_id=body.finding_id,
            auth_scope=body.auth_scope,
        )


@router.post(
    "/projects/{project_id}/intents/{intent_id}/heartbeat",
    response_model=Intent,
)
def heartbeat(project_id: str, intent_id: str, body: HeartbeatRequest):
    with get_conn() as conn:
        check_project_active(conn, project_id)
        get_claimable_open_intent_or_404(conn, project_id, intent_id, body.worker)

        now = utcnow()
        conn.execute(
            "UPDATE intents SET worker = ?, last_heartbeat_at = ? WHERE id = ? AND project_id = ?",
            (body.worker, now, intent_id, project_id),
        )

        updated = conn.execute(
            "SELECT * FROM intents WHERE id = ? AND project_id = ?",
            (intent_id, project_id),
        ).fetchone()
        return intent_to_model(conn, updated, project_id)


@router.post(
    "/projects/{project_id}/intents/{intent_id}/release",
    response_model=Intent,
)
def release(project_id: str, intent_id: str, body: HeartbeatRequest):
    with get_conn() as conn:
        check_project_active(conn, project_id)
        row = get_releasable_open_intent_or_404(conn, project_id, intent_id, body.worker)

        if row["worker"] == body.worker:
            conn.execute(
                "UPDATE intents SET worker = NULL WHERE id = ? AND project_id = ?",
                (intent_id, project_id),
            )
            row = conn.execute(
                "SELECT * FROM intents WHERE id = ? AND project_id = ?",
                (intent_id, project_id),
            ).fetchone()

        return intent_to_model(conn, row, project_id)


@router.post(
    "/projects/{project_id}/intents/{intent_id}/conclude",
    response_model=ConcludeResponse,
)
def conclude(project_id: str, intent_id: str, body: ConcludeRequest):
    with get_conn() as conn:
        check_project_active(conn, project_id)
        intent = get_claimable_open_intent_or_404(conn, project_id, intent_id, body.worker)
        project = conn.execute(
            "SELECT project_kind FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        if project is None:
            raise HTTPException(404, "Project not found")
        if intent["intent_kind"] == "report":
            raise HTTPException(400, "Report intents must be concluded through the report endpoint")
        if intent["task_mode"] == "collection" and body.findings:
            raise HTTPException(400, "collection intents cannot write findings")

        now = utcnow()
        fid = next_fact_id(conn, project_id)

        conn.execute(
            """
            INSERT INTO facts (
                id,
                project_id,
                description,
                fact_type,
                title,
                summary,
                details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fid,
                project_id,
                body.description,
                body.fact_type,
                body.title,
                body.summary,
                json.dumps(body.details, ensure_ascii=False),
            ),
        )
        conn.execute(
            "UPDATE intents SET to_fact_id = ?, worker = ?, last_heartbeat_at = ?, concluded_at = ? WHERE id = ? AND project_id = ?",
            (fid, body.worker, now, now, intent_id, project_id),
        )
        mark_reason_pending_if_reason_running(conn, project_id)
        findings = []
        for finding in body.findings or []:
            finding_id = next_finding_id(conn, project_id)
            conn.execute(
                """
                INSERT INTO findings (
                    id,
                    project_id,
                    fact_id,
                    intent_id,
                    title,
                    vulnerability_type,
                    severity,
                    target,
                    location,
                    impact,
                    evidence,
                    reproduction,
                    remediation,
                    status,
                    research_value,
                    next_action,
                    followup_reason,
                    followup_intent_description,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    finding_id,
                    project_id,
                    fid,
                    intent_id,
                    finding.title,
                    finding.vulnerability_type,
                    finding.severity,
                    finding.target,
                    finding.location,
                    finding.impact,
                    finding.evidence,
                    finding.reproduction,
                    finding.remediation,
                    finding.status,
                    finding.research_value,
                    finding.next_action,
                    finding.followup_reason,
                    finding.followup_intent_description,
                    now,
                ),
            )
            if finding.next_action == "follow_up":
                followup_intent_id = next_intent_id(conn, project_id)
                conn.execute(
                    """
                    INSERT INTO intents (
                        id,
                        project_id,
                        to_fact_id,
                        description,
                        creator,
                        worker,
                        last_heartbeat_at,
                        created_at,
                        concluded_at,
                        intent_kind,
                        task_mode,
                        finding_id,
                        auth_scope
                    ) VALUES (?, ?, NULL, ?, 'dispatcher.finding_followup', NULL, NULL, ?, NULL, 'explore', 'validation', ?, ?)
                    """,
                    (
                        followup_intent_id,
                        project_id,
                        finding.followup_intent_description,
                        now,
                        finding_id,
                        intent["auth_scope"],
                    ),
                )
                conn.execute(
                    "INSERT INTO intent_sources (intent_id, project_id, fact_id) VALUES (?, ?, ?)",
                    (followup_intent_id, project_id, fid),
                )
                conn.execute(
                    "UPDATE findings SET followup_intent_id = ? WHERE id = ? AND project_id = ?",
                    (followup_intent_id, finding_id, project_id),
                )
            if finding.next_action == "report":
                report_intent_id = next_intent_id(conn, project_id)
                conn.execute(
                    """
                    INSERT INTO intents (
                        id,
                        project_id,
                        to_fact_id,
                        description,
                        creator,
                        worker,
                        last_heartbeat_at,
                        created_at,
                        concluded_at,
                        intent_kind,
                        task_mode,
                        finding_id,
                        auth_scope
                    ) VALUES (?, ?, NULL, ?, 'dispatcher.finding_report', NULL, NULL, ?, NULL, 'report', 'report', ?, NULL)
                    """,
                    (
                        report_intent_id,
                        project_id,
                        f"Produce SRC submission report for finding {finding_id}: {finding.title}",
                        now,
                        finding_id,
                    ),
                )
                conn.execute(
                    "INSERT INTO intent_sources (intent_id, project_id, fact_id) VALUES (?, ?, ?)",
                    (report_intent_id, project_id, fid),
                )
                conn.execute(
                    "UPDATE findings SET report_intent_id = ?, report_status = 'queued' WHERE id = ? AND project_id = ?",
                    (report_intent_id, finding_id, project_id),
                )
            row = conn.execute(
                "SELECT * FROM findings WHERE id = ? AND project_id = ?",
                (finding_id, project_id),
            ).fetchone()
            findings.append(finding_to_model(row))

        if intent["task_mode"] == "collection":
            increment_collection_explore_round(conn, project_id)

        updated = conn.execute(
            "SELECT * FROM intents WHERE id = ? AND project_id = ?",
            (intent_id, project_id),
        ).fetchone()
        fact_row = conn.execute(
            "SELECT * FROM facts WHERE id = ? AND project_id = ?",
            (fid, project_id),
        ).fetchone()

        return ConcludeResponse(
            fact=fact_from_row(fact_row),
            intent=intent_to_model(conn, updated, project_id),
            findings=findings,
        )


@router.post(
    "/projects/{project_id}/intents/{intent_id}/report",
    response_model=FindingReport,
)
def conclude_report(project_id: str, intent_id: str, body: ReportConcludeRequest):
    with get_conn() as conn:
        project = check_project_active(conn, project_id)
        if project["project_kind"] != "vuln":
            raise HTTPException(400, "reports are only supported for vuln projects")
        intent = get_claimable_open_intent_or_404(conn, project_id, intent_id, body.worker)
        if intent["intent_kind"] != "report" or intent["task_mode"] != "report":
            raise HTTPException(400, "Intent is not a report intent")
        if not intent["finding_id"]:
            raise HTTPException(409, "Report intent is missing finding_id")
        get_finding_or_404(conn, project_id, intent["finding_id"])
        now = utcnow()
        report_id = next_report_id(conn, project_id)
        conn.execute(
            """
            INSERT INTO finding_reports (
                id,
                project_id,
                finding_id,
                intent_id,
                report_markdown,
                report_json,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report_id,
                project_id,
                intent["finding_id"],
                intent_id,
                body.report_markdown,
                json.dumps(body.report_json, ensure_ascii=False),
                now,
            ),
        )
        conn.execute(
            """
            UPDATE intents
            SET to_fact_id = ?,
                worker = ?,
                last_heartbeat_at = ?,
                concluded_at = ?
            WHERE id = ? AND project_id = ?
            """,
            (intent["finding_id"], body.worker, now, now, intent_id, project_id),
        )
        conn.execute(
            "UPDATE findings SET report_status = 'drafted' WHERE id = ? AND project_id = ?",
            (intent["finding_id"], project_id),
        )
        row = conn.execute(
            "SELECT * FROM finding_reports WHERE id = ? AND project_id = ?",
            (report_id, project_id),
        ).fetchone()
        return finding_report_to_model(row)
