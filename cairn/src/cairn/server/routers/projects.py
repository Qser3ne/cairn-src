import json

from fastapi import APIRouter, HTTPException
import yaml

from cairn.server.db import get_conn
from cairn.server.models import (
    CreateProjectRequest,
    EphemeralJob,
    EphemeralJobClaimRequest,
    EphemeralJobFailRequest,
    EphemeralJobFinishRequest,
    Fact,
    ForkSeedFinishRequest,
    ForkSeedJobCreateResponse,
    ForkVulnSeedJobRequest,
    ForkVulnRequest,
    HeartbeatRequest,
    Hint,
    JudgementCreateResponse,
    JudgementResult,
    ProjectDetail,
    ProjectMeta,
    ProjectSnapshot,
    ProjectSnapshotCreate,
    ProjectSummary,
    CollectionReasonRoundRequest,
    ReasonClaimRequest,
    ReasonHeartbeatRequest,
    UpdateProjectStatusRequest,
    UpdateProjectTitleRequest,
)
from cairn.server.services import (
    build_findings,
    build_intents,
    build_project_reasons,
    build_project_accounts,
    check_project_active,
    check_project_kind,
    clear_reason_pending,
    clear_project_reason,
    ephemeral_job_to_model,
    expire_reason_leases,
    expire_workers,
    fact_from_row,
    get_ephemeral_job_or_404,
    get_project_or_404,
    get_snapshot_or_404,
    increment_collection_explore_round,
    increment_collection_reason_round,
    next_account_id,
    next_fact_id,
    next_hint_id,
    next_project_id,
    project_meta_from_row,
    project_reason_from_row,
    safe_json_object,
    snapshot_to_model,
    utcnow,
)

router = APIRouter(tags=["projects"])
RETIRED_EPHEMERAL_JOB_TYPES = {"judge", "fork_seed"}


def _seed_fact_description(seed_fact) -> str:
    return (
        f"seed_title: {seed_fact.title}\n"
        f"seed_type: {seed_fact.candidate_type}\n"
        f"auth_scope: {seed_fact.auth_scope}\n"
        f"derived_from: {', '.join(seed_fact.derived_from)}\n\n"
        f"{seed_fact.description}"
    )


def _seed_fact_details(seed_fact) -> dict:
    return {
        "seed_title": seed_fact.title,
        "seed_type": seed_fact.candidate_type,
        "auth_scope": seed_fact.auth_scope,
        "derived_from": seed_fact.derived_from,
        "feature_summary": seed_fact.feature_summary,
        "user_actions": seed_fact.user_actions,
        "routes": seed_fact.routes,
        "apis": seed_fact.apis,
        "vuln_validation_focus": seed_fact.vuln_validation_focus,
        "known_constraints": seed_fact.known_constraints,
        "evidence_refs": seed_fact.evidence_refs,
    }


def _seed_fact_summary(seed_fact) -> str | None:
    if seed_fact.feature_summary:
        return seed_fact.feature_summary
    return seed_fact.description.splitlines()[0] if seed_fact.description else None


def _insert_project_accounts(conn, project_id: str, accounts, now: str) -> list[dict]:
    inserted = []
    for index, account in enumerate(accounts or [], start=1):
        account_id = next_account_id(conn, project_id)
        label = account.label or f"account-{index}"
        cookies_json = json.dumps(
            [cookie.model_dump() for cookie in account.cookies],
            ensure_ascii=False,
        )
        conn.execute(
            "INSERT INTO project_accounts (id, project_id, label, cookies_json) VALUES (?, ?, ?, ?)",
            (account_id, project_id, label, cookies_json),
        )
        inserted.append(
            {
                "id": account_id,
                "label": label,
                "cookies": [cookie.model_dump() for cookie in account.cookies],
            }
        )
    return inserted


def _create_seeded_vuln_project(conn, parent_project_id: str, snapshot_id: str, title: str, auth_mode: str, accounts, seed_facts) -> ProjectDetail:
    parent = check_project_kind(conn, parent_project_id, "vuln")
    snapshot = get_snapshot_or_404(conn, parent_project_id, snapshot_id)
    try:
        snapshot_data = yaml.safe_load(snapshot["summary_yaml"]) or {}
    except yaml.YAMLError as exc:
        raise HTTPException(422, "Snapshot summary_yaml is invalid") from exc
    parent_fact_ids = {
        fact["id"]
        for fact in snapshot_data.get("facts", [])
        if isinstance(fact, dict) and isinstance(fact.get("id"), str)
    }
    for seed_fact in seed_facts:
        missing = [fact_id for fact_id in seed_fact.derived_from if fact_id not in parent_fact_ids]
        if missing:
            raise HTTPException(422, f"seed fact references unknown parent facts: {', '.join(missing)}")

    pid = next_project_id(conn)
    now = utcnow()
    conn.execute(
        """
        INSERT INTO projects (
            id,
            title,
            status,
            project_kind,
            auth_mode,
            parent_project_id,
            parent_snapshot_id,
            created_at
        ) VALUES (?, ?, 'active', 'vuln', ?, ?, ?, ?)
        """,
        (pid, title, auth_mode, parent_project_id, snapshot_id, now),
    )
    origin = conn.execute(
        "SELECT description FROM facts WHERE id = 'origin' AND project_id = ?",
        (parent_project_id,),
    ).fetchone()
    conn.execute(
        "INSERT INTO facts (id, project_id, description) VALUES ('origin', ?, ?)",
        (pid, origin["description"] if origin else parent["title"]),
    )
    conn.execute(
        "INSERT INTO facts (id, project_id, description) VALUES (?, ?, ?)",
        (
            "f001",
            pid,
            (
                f"recon_snapshot: {snapshot_id}\n"
                f"parent_project_id: {parent_project_id}\n"
                "source: ProjectSnapshot.summary_yaml"
            ),
        ),
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO scoped_counters (project_id, kind, value)
        VALUES (?, 'fact', 1)
        """,
        (pid,),
    )
    conn.execute(
        """
        UPDATE scoped_counters
        SET value = MAX(value, 1)
        WHERE project_id = ? AND kind = 'fact'
        """,
        (pid,),
    )
    for seed_fact in seed_facts:
        child_fact_id = next_fact_id(conn, pid)
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
                child_fact_id,
                pid,
                _seed_fact_description(seed_fact),
                "feature_surface" if seed_fact.candidate_type == "feature_surface" else "observation",
                seed_fact.title,
                _seed_fact_summary(seed_fact),
                json.dumps(_seed_fact_details(seed_fact), ensure_ascii=False),
            ),
        )
    inserted_accounts = _insert_project_accounts(conn, pid, accounts, now)
    facts = conn.execute("SELECT * FROM facts WHERE project_id = ? ORDER BY id", (pid,)).fetchall()
    return ProjectDetail(
        project=project_meta_from_row(get_project_or_404(conn, pid), conn),
        facts=[fact_from_row(f) for f in facts],
        intents=[],
        hints=[],
        findings=[],
        accounts=inserted_accounts,
    )


def _summary_from_row(row) -> ProjectSummary:
    return ProjectSummary(
        id=row["id"],
        title=row["title"],
        status=row["status"],
        project_kind=row["project_kind"],
        auth_mode=row["auth_mode"],
        parent_project_id=row["parent_project_id"],
        parent_snapshot_id=row["parent_snapshot_id"],
        created_at=row["created_at"],
        reason=project_reason_from_row(row),
        reason_pending=bool(row["reason_pending"]),
        collection_reason_rounds=row["collection_reason_rounds"],
        collection_explore_rounds=row["collection_explore_rounds"],
        collection_stable_rounds=row["collection_stable_rounds"],
        judge_status=row["judge_status"],
        judged_at=row["judged_at"],
        fact_count=row["fact_count"],
        intent_count=row["intent_count"],
        working_intent_count=row["working_intent_count"],
        unclaimed_intent_count=row["unclaimed_intent_count"],
        hint_count=row["hint_count"],
        finding_count=row["finding_count"],
    )


def _project_summary_rows(conn, where: str = "", params: tuple = ()):
    return conn.execute(
        f"""
        SELECT p.*,
            (SELECT COUNT(*) FROM facts WHERE project_id = p.id) AS fact_count,
            (SELECT COUNT(*) FROM intents WHERE project_id = p.id) AS intent_count,
            (SELECT COUNT(*) FROM intents WHERE project_id = p.id AND concluded_at IS NULL AND worker IS NOT NULL) AS working_intent_count,
            (SELECT COUNT(*) FROM intents WHERE project_id = p.id AND concluded_at IS NULL AND worker IS NULL) AS unclaimed_intent_count,
            (SELECT COUNT(*) FROM hints WHERE project_id = p.id) AS hint_count,
            (SELECT COUNT(*) FROM findings WHERE project_id = p.id) AS finding_count
        FROM projects p
        {where}
        ORDER BY p.created_at, p.id
        """,
        params,
    ).fetchall()


@router.get("/projects", response_model=list[ProjectSummary])
def list_projects():
    with get_conn() as conn:
        expire_workers(conn)
        expire_reason_leases(conn)
        return [_summary_from_row_with_conn(row, conn) for row in _project_summary_rows(conn)]


def _summary_from_row_with_conn(row, conn) -> ProjectSummary:
    summary = _summary_from_row(row)
    reasons = build_project_reasons(conn, row["id"])
    return summary.model_copy(
        update={
            "reason": next((lease for lease in reasons.values() if lease is not None), None) or summary.reason,
            "reasons": reasons,
        }
    )


@router.post("/projects", response_model=ProjectDetail, status_code=201)
def create_project(body: CreateProjectRequest):
    with get_conn() as conn:
        if body.parent_project_id is not None or body.parent_snapshot_id is not None:
            raise HTTPException(400, "snapshot-based project forking has been removed")

        pid = next_project_id(conn)
        now = utcnow()
        conn.execute(
            """
            INSERT INTO projects (
                id,
                title,
                status,
                project_kind,
                auth_mode,
                parent_project_id,
                parent_snapshot_id,
                created_at
            ) VALUES (?, ?, 'active', ?, ?, ?, ?, ?)
            """,
            (
                pid,
                body.title,
                "vuln",
                body.auth_mode,
                body.parent_project_id,
                body.parent_snapshot_id,
                now,
            ),
        )
        conn.execute(
            "INSERT INTO facts (id, project_id, description) VALUES (?, ?, ?)",
            ("origin", pid, body.origin),
        )

        hints = []
        for h in body.hints or []:
            hid = next_hint_id(conn, pid)
            conn.execute(
                "INSERT INTO hints (id, project_id, content, creator, created_at) VALUES (?, ?, ?, ?, ?)",
                (hid, pid, h.content, h.creator, now),
            )
            hints.append(Hint(id=hid, content=h.content, creator=h.creator, created_at=now))

        accounts = []
        for index, account in enumerate(body.accounts or [], start=1):
            account_id = next_account_id(conn, pid)
            label = account.label or f"account-{index}"
            cookies_json = json.dumps(
                [cookie.model_dump() for cookie in account.cookies],
                ensure_ascii=False,
            )
            conn.execute(
                "INSERT INTO project_accounts (id, project_id, label, cookies_json) VALUES (?, ?, ?, ?)",
                (account_id, pid, label, cookies_json),
            )
            accounts.append(
                {
                    "id": account_id,
                    "label": label,
                    "cookies": [cookie.model_dump() for cookie in account.cookies],
                }
            )

        project = project_meta_from_row(get_project_or_404(conn, pid), conn)
        return ProjectDetail(
            project=project,
            facts=[
                Fact(id="origin", description=body.origin),
            ],
            intents=[],
            hints=hints,
            findings=[],
            accounts=accounts,
        )


@router.get("/projects/{project_id}", response_model=ProjectDetail)
def get_project(project_id: str):
    with get_conn() as conn:
        expire_workers(conn, project_id)
        expire_reason_leases(conn, project_id)
        row = get_project_or_404(conn, project_id)
        facts = conn.execute(
            "SELECT * FROM facts WHERE project_id = ? ORDER BY CASE WHEN id = 'origin' THEN 0 ELSE 1 END, id",
            (project_id,),
        ).fetchall()
        hints = conn.execute(
            "SELECT * FROM hints WHERE project_id = ? ORDER BY created_at, id",
            (project_id,),
        ).fetchall()
        return ProjectDetail(
            project=project_meta_from_row(row, conn),
            facts=[fact_from_row(f) for f in facts],
            intents=build_intents(conn, project_id),
            hints=[Hint(**dict(h)) for h in hints],
            findings=build_findings(conn, project_id),
            accounts=build_project_accounts(conn, project_id),
        )


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: str):
    with get_conn() as conn:
        get_project_or_404(conn, project_id)
        child = conn.execute(
            "SELECT id FROM projects WHERE parent_project_id = ? LIMIT 1",
            (project_id,),
        ).fetchone()
        if child is not None:
            raise HTTPException(409, "Project has child vulnerability projects")
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))


@router.put("/projects/{project_id}/title", response_model=ProjectMeta)
def update_project_title(project_id: str, body: UpdateProjectTitleRequest):
    with get_conn() as conn:
        get_project_or_404(conn, project_id)
        conn.execute("UPDATE projects SET title = ? WHERE id = ?", (body.title, project_id))
        return project_meta_from_row(get_project_or_404(conn, project_id), conn)


@router.put("/projects/{project_id}/status", response_model=ProjectMeta)
def update_project_status(project_id: str, body: UpdateProjectStatusRequest):
    with get_conn() as conn:
        expire_reason_leases(conn, project_id)
        row = get_project_or_404(conn, project_id)
        current_status = row["status"]
        if current_status == "completed" and body.status != "completed":
            raise HTTPException(409, "Completed projects cannot change status")
        if current_status == body.status:
            return project_meta_from_row(row, conn)

        conn.execute("UPDATE projects SET status = ? WHERE id = ?", (body.status, project_id))
        if body.status in ("stopped", "completed"):
            conn.execute(
                "UPDATE intents SET worker = NULL WHERE project_id = ? AND concluded_at IS NULL",
                (project_id,),
            )
            clear_project_reason(conn, project_id)
            clear_reason_pending(conn, project_id)
        return project_meta_from_row(get_project_or_404(conn, project_id), conn)


@router.post("/projects/{project_id}/reason/claim", response_model=ProjectMeta)
def claim_project_reason(project_id: str, body: ReasonClaimRequest):
    with get_conn() as conn:
        check_project_active(conn, project_id)
        expire_reason_leases(conn, project_id)
        row = get_project_or_404(conn, project_id)
        current = conn.execute(
            "SELECT * FROM project_reason_leases WHERE project_id = ? AND task_mode = ?",
            (project_id, body.task_mode),
        ).fetchone()
        current_worker = current["worker"] if current is not None else None
        if current_worker is not None and current_worker != body.worker:
            raise HTTPException(409, f"Project reason is currently claimed by {current_worker}")
        if current_worker == body.worker:
            return project_meta_from_row(row, conn)

        now = utcnow()
        conn.execute(
            """
            INSERT OR REPLACE INTO project_reason_leases (
                project_id,
                task_mode,
                worker,
                trigger,
                started_at,
                last_heartbeat_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (project_id, body.task_mode, body.worker, body.trigger, now, now),
        )
        clear_reason_pending(conn, project_id)
        return project_meta_from_row(get_project_or_404(conn, project_id), conn)


@router.post("/projects/{project_id}/reason/heartbeat", response_model=ProjectMeta)
def heartbeat_project_reason(project_id: str, body: ReasonHeartbeatRequest):
    with get_conn() as conn:
        check_project_active(conn, project_id)
        expire_reason_leases(conn, project_id)
        row = get_project_or_404(conn, project_id)
        current = conn.execute(
            "SELECT * FROM project_reason_leases WHERE project_id = ? AND task_mode = ?",
            (project_id, body.task_mode),
        ).fetchone()
        current_worker = current["worker"] if current is not None else None
        if current_worker is None:
            raise HTTPException(409, "Project reason is not currently claimed")
        if current_worker != body.worker:
            raise HTTPException(409, f"Project reason is currently claimed by {current_worker}")

        conn.execute(
            "UPDATE project_reason_leases SET last_heartbeat_at = ? WHERE project_id = ? AND task_mode = ?",
            (utcnow(), project_id, body.task_mode),
        )
        return project_meta_from_row(get_project_or_404(conn, project_id), conn)


@router.post("/projects/{project_id}/reason/release", response_model=ProjectMeta)
def release_project_reason(project_id: str, body: ReasonHeartbeatRequest):
    with get_conn() as conn:
        check_project_active(conn, project_id)
        expire_reason_leases(conn, project_id)
        row = get_project_or_404(conn, project_id)
        current = conn.execute(
            "SELECT * FROM project_reason_leases WHERE project_id = ? AND task_mode = ?",
            (project_id, body.task_mode),
        ).fetchone()
        current_worker = current["worker"] if current is not None else None
        if current_worker is None:
            return project_meta_from_row(row, conn)
        if current_worker != body.worker:
            raise HTTPException(409, f"Project reason is currently claimed by {current_worker}")

        conn.execute(
            "DELETE FROM project_reason_leases WHERE project_id = ? AND task_mode = ?",
            (project_id, body.task_mode),
        )
        return project_meta_from_row(get_project_or_404(conn, project_id), conn)


@router.post("/projects/{project_id}/complete")
def complete_project(project_id: str):
    raise HTTPException(410, "Standard complete workflow has been removed in SRC-only mode")


@router.post("/projects/{project_id}/reopen")
def reopen_project(project_id: str):
    raise HTTPException(410, "Standard reopen workflow has been removed in SRC-only mode")


@router.post("/projects/{project_id}/recon/reason-round", response_model=ProjectMeta)
def record_collection_reason_round(project_id: str, body: CollectionReasonRoundRequest):
    with get_conn() as conn:
        return increment_collection_reason_round(conn, project_id, body.stable)


@router.post("/projects/{project_id}/recon/explore-round", response_model=ProjectMeta)
def record_collection_explore_round(project_id: str):
    with get_conn() as conn:
        return increment_collection_explore_round(conn, project_id)


@router.post("/projects/{project_id}/snapshots", response_model=ProjectSnapshot, status_code=201)
def create_project_snapshot(project_id: str, body: ProjectSnapshotCreate):
    raise HTTPException(410, "Recon snapshot creation has been removed")


@router.get("/projects/{project_id}/snapshots", response_model=list[ProjectSnapshot])
def list_project_snapshots(project_id: str):
    with get_conn() as conn:
        get_project_or_404(conn, project_id)
        rows = conn.execute(
            "SELECT * FROM project_snapshots WHERE project_id = ? ORDER BY created_at, id",
            (project_id,),
        ).fetchall()
        return [snapshot_to_model(row) for row in rows]


@router.post("/projects/{project_id}/fork-vuln", response_model=ProjectDetail, status_code=201)
def fork_vuln_project(project_id: str, body: ForkVulnRequest):
    raise HTTPException(410, "Forking vulnerability projects from recon snapshots has been removed")


@router.post("/projects/{project_id}/fork-vuln/seed-jobs", response_model=ForkSeedJobCreateResponse, status_code=201)
def create_fork_seed_job(project_id: str, body: ForkVulnSeedJobRequest):
    raise HTTPException(410, "Fork seed jobs have been removed")


@router.get("/projects/{project_id}/fork-vuln/seed-jobs", response_model=list[JudgementResult])
def list_fork_seed_jobs(project_id: str):
    raise HTTPException(410, "Fork seed jobs have been removed")


@router.get("/projects/{project_id}/children", response_model=list[ProjectSummary])
def list_project_children(project_id: str):
    with get_conn() as conn:
        get_project_or_404(conn, project_id)
        rows = _project_summary_rows(conn, "WHERE p.parent_project_id = ?", (project_id,))
        return [_summary_from_row_with_conn(row, conn) for row in rows]


@router.post("/projects/{project_id}/recon/judgements", response_model=JudgementCreateResponse, status_code=201)
def create_recon_judgement(project_id: str):
    raise HTTPException(410, "Recon judgement jobs have been removed")


@router.get("/projects/{project_id}/recon/judgements", response_model=list[JudgementResult])
def list_recon_judgements(project_id: str):
    raise HTTPException(410, "Recon judgement jobs have been removed")


@router.get("/projects/{project_id}/recon/judgements/{job_id}", response_model=EphemeralJob)
def get_recon_judgement(project_id: str, job_id: str):
    raise HTTPException(410, "Recon judgement jobs have been removed")


@router.get("/ephemeral-jobs/queued", response_model=list[EphemeralJob])
def list_queued_ephemeral_jobs(job_type: str = "judge"):
    with get_conn() as conn:
        now = utcnow()
        conn.execute(
            "UPDATE ephemeral_jobs SET status = 'expired', finished_at = ? WHERE status IN ('queued', 'running') AND expires_at < ?",
            (now, now),
        )
        if job_type in RETIRED_EPHEMERAL_JOB_TYPES:
            return []
        rows = conn.execute(
            "SELECT * FROM ephemeral_jobs WHERE status = 'queued' AND job_type = ? ORDER BY created_at, id",
            (job_type,),
        ).fetchall()
        return [ephemeral_job_to_model(row) for row in rows]


@router.post("/ephemeral-jobs/{job_id}/claim", response_model=EphemeralJob)
def claim_ephemeral_job(job_id: str, body: EphemeralJobClaimRequest):
    with get_conn() as conn:
        row = get_ephemeral_job_or_404(conn, job_id)
        if row["job_type"] in RETIRED_EPHEMERAL_JOB_TYPES:
            raise HTTPException(410, f"{row['job_type']} jobs have been retired")
        if row["status"] != "queued":
            raise HTTPException(409, f"Ephemeral job is {row['status']}")
        conn.execute(
            "UPDATE ephemeral_jobs SET status = 'running', worker = ?, started_at = ? WHERE id = ?",
            (body.worker, utcnow(), job_id),
        )
        return ephemeral_job_to_model(get_ephemeral_job_or_404(conn, job_id))


@router.post("/ephemeral-jobs/{job_id}/finish-fork-seed", response_model=EphemeralJob)
def finish_fork_seed_job(job_id: str, body: ForkSeedFinishRequest):
    with get_conn() as conn:
        row = get_ephemeral_job_or_404(conn, job_id)
        if row["job_type"] in RETIRED_EPHEMERAL_JOB_TYPES:
            raise HTTPException(410, f"{row['job_type']} jobs have been retired")
        if row["job_type"] != "fork_seed":
            raise HTTPException(400, "Ephemeral job is not a fork_seed job")
        if row["status"] != "running" or row["worker"] != body.worker:
            raise HTTPException(409, "Ephemeral job is not claimed by this worker")
        if not row["input_json"]:
            raise HTTPException(422, "Fork seed job is missing input_json")
        try:
            input_data = ForkVulnSeedJobRequest.model_validate(safe_json_object(row["input_json"]))
        except ValueError as exc:
            raise HTTPException(422, "Fork seed job input_json is invalid") from exc
        child = _create_seeded_vuln_project(
            conn,
            row["project_id"],
            input_data.snapshot_id,
            input_data.title,
            input_data.auth_mode,
            input_data.accounts,
            body.seed_facts,
        )
        result = {
            "child_project_id": child.project.id,
            "snapshot_id": input_data.snapshot_id,
            "seed_fact_ids": [fact.id for fact in child.facts if fact.id not in ("origin", "f001")],
            "seed_facts": [
                fact.model_dump(mode="json")
                for fact in child.facts
                if fact.id not in ("origin", "f001")
            ],
        }
        now = utcnow()
        conn.execute(
            """
            UPDATE ephemeral_jobs
            SET status = 'succeeded',
                result_json = ?,
                finished_at = ?
            WHERE id = ?
            """,
            (json.dumps(result, ensure_ascii=False), now, job_id),
        )
        return ephemeral_job_to_model(get_ephemeral_job_or_404(conn, job_id))


@router.post("/ephemeral-jobs/{job_id}/finish", response_model=EphemeralJob)
def finish_ephemeral_job(job_id: str, body: EphemeralJobFinishRequest):
    with get_conn() as conn:
        row = get_ephemeral_job_or_404(conn, job_id)
        if row["job_type"] in RETIRED_EPHEMERAL_JOB_TYPES:
            raise HTTPException(410, f"{row['job_type']} jobs have been retired")
        if row["job_type"] == "fork_seed":
            raise HTTPException(400, "Use finish-fork-seed for fork_seed jobs")
        if row["status"] != "running" or row["worker"] != body.worker:
            raise HTTPException(409, "Ephemeral job is not claimed by this worker")
        now = utcnow()
        conn.execute(
            """
            UPDATE ephemeral_jobs
            SET status = 'succeeded',
                result_json = ?,
                finished_at = ?
            WHERE id = ?
            """,
            (json.dumps(body.result, ensure_ascii=False), now, job_id),
        )
        if row["job_type"] == "judge":
            verdict = body.result.get("verdict")
            judge_status = verdict if verdict in ("ready", "not_ready", "blocked") else "not_judged"
            conn.execute(
                "UPDATE projects SET judge_status = ?, judged_at = ? WHERE id = ?",
                (judge_status, now, row["project_id"]),
            )
        return ephemeral_job_to_model(get_ephemeral_job_or_404(conn, job_id))


@router.post("/ephemeral-jobs/{job_id}/fail", response_model=EphemeralJob)
def fail_ephemeral_job(job_id: str, body: EphemeralJobFailRequest):
    with get_conn() as conn:
        row = get_ephemeral_job_or_404(conn, job_id)
        if row["status"] not in ("queued", "running") or (row["worker"] is not None and row["worker"] != body.worker):
            raise HTTPException(409, "Ephemeral job is not claimed by this worker")
        conn.execute(
            "UPDATE ephemeral_jobs SET status = 'failed', worker = ?, error = ?, finished_at = ? WHERE id = ?",
            (body.worker, body.error, utcnow(), job_id),
        )
        return ephemeral_job_to_model(get_ephemeral_job_or_404(conn, job_id))
