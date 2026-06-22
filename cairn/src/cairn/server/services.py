from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
import json

from fastapi import HTTPException

from cairn.server.models import (
    EphemeralJob,
    Finding,
    FindingReport,
    Intent,
    ProjectAccount,
    ProjectMeta,
    ProjectReason,
    ProjectSnapshot,
)

def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _next_existing_numeric_id(conn: sqlite3.Connection, table: str, column: str, prefix: str) -> str:
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    rows = conn.execute(f"SELECT {column} FROM {table} WHERE {column} GLOB ?", (f"{prefix}*",)).fetchall()
    max_value = 0
    for row in rows:
        value = row[0]
        if not isinstance(value, str) or not value.startswith(prefix):
            continue
        suffix = value[len(prefix) :]
        if suffix.isdigit():
            max_value = max(max_value, int(suffix))
    return f"{prefix}{max_value + 1:03d}"


def next_project_id(conn: sqlite3.Connection) -> str:
    return _next_existing_numeric_id(conn, "projects", "id", "proj_")


def _next_scoped_id(
    conn: sqlite3.Connection, kind: str, prefix: str, project_id: str
) -> str:
    conn.execute(
        "INSERT OR IGNORE INTO scoped_counters (project_id, kind, value) VALUES (?, ?, 0)",
        (project_id, kind),
    )
    conn.execute(
        "UPDATE scoped_counters SET value = value + 1 WHERE project_id = ? AND kind = ?",
        (project_id, kind),
    )
    row = conn.execute(
        "SELECT value FROM scoped_counters WHERE project_id = ? AND kind = ?",
        (project_id, kind),
    ).fetchone()
    assert row is not None
    return f"{prefix}{row['value']:03d}"


def next_fact_id(conn: sqlite3.Connection, project_id: str) -> str:
    return _next_scoped_id(conn, "fact", "f", project_id)


def next_intent_id(conn: sqlite3.Connection, project_id: str) -> str:
    return _next_scoped_id(conn, "intent", "i", project_id)


def next_hint_id(conn: sqlite3.Connection, project_id: str) -> str:
    return _next_scoped_id(conn, "hint", "h", project_id)


def next_finding_id(conn: sqlite3.Connection, project_id: str) -> str:
    return _next_scoped_id(conn, "finding", "v", project_id)


def next_account_id(conn: sqlite3.Connection, project_id: str) -> str:
    return _next_scoped_id(conn, "account", "a", project_id)


def next_snapshot_id(conn: sqlite3.Connection, project_id: str) -> str:
    return _next_scoped_id(conn, "snapshot", "snap_", project_id)


def next_ephemeral_job_id(conn: sqlite3.Connection) -> str:
    return _next_existing_numeric_id(conn, "ephemeral_jobs", "id", "judge_")


def next_report_id(conn: sqlite3.Connection, project_id: str) -> str:
    return _next_scoped_id(conn, "report", "r", project_id)


def get_project_or_404(conn: sqlite3.Connection, project_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Project not found")
    return row


def check_project_active(conn: sqlite3.Connection, project_id: str) -> sqlite3.Row:
    row = get_project_or_404(conn, project_id)
    if row["status"] != "active":
        raise HTTPException(403, f"Project is {row['status']}")
    return row


def check_project_hint_writable(conn: sqlite3.Connection, project_id: str) -> sqlite3.Row:
    row = get_project_or_404(conn, project_id)
    if row["status"] not in ("active", "stopped", "completed"):
        raise HTTPException(403, f"Project is {row['status']}")
    return row


def check_project_kind(conn: sqlite3.Connection, project_id: str, kind: str) -> sqlite3.Row:
    row = get_project_or_404(conn, project_id)
    if row["project_kind"] != kind:
        raise HTTPException(400, f"Project must be {kind}")
    return row


def validate_facts_exist(
    conn: sqlite3.Connection, project_id: str, fact_ids: list[str]
) -> None:
    for fid in fact_ids:
        row = conn.execute(
            "SELECT 1 FROM facts WHERE id = ? AND project_id = ?", (fid, project_id)
        ).fetchone()
        if row is None:
            raise HTTPException(404, f"Fact {fid} not found")


def validate_intent_creator_worker(creator: str, worker: str | None) -> None:
    if worker is not None and worker != creator:
        raise HTTPException(400, "worker must be null or equal to creator")


def normalize_intent_description(description: str) -> str:
    return " ".join(description.strip().casefold().split())


def check_duplicate_intent(
    conn: sqlite3.Connection,
    project_id: str,
    fact_ids: list[str],
    description: str,
    auth_scope: str | None,
) -> None:
    normalized_sources = sorted(fact_ids)
    normalized_description = normalize_intent_description(description)
    rows = conn.execute(
        "SELECT id, description, auth_scope FROM intents WHERE project_id = ?",
        (project_id,),
    ).fetchall()
    for row in rows:
        if row["auth_scope"] != auth_scope:
            continue
        if normalize_intent_description(row["description"]) != normalized_description:
            continue
        sources = conn.execute(
            "SELECT fact_id FROM intent_sources WHERE intent_id = ? AND project_id = ?",
            (row["id"], project_id),
        ).fetchall()
        if sorted(source["fact_id"] for source in sources) == normalized_sources:
            raise HTTPException(409, "Duplicate intent")


def get_intent_or_404(
    conn: sqlite3.Connection, project_id: str, intent_id: str
) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM intents WHERE id = ? AND project_id = ?",
        (intent_id, project_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "Intent not found")
    return row


def get_claimable_open_intent_or_404(
    conn: sqlite3.Connection, project_id: str, intent_id: str, worker: str
) -> sqlite3.Row:
    expire_workers(conn, project_id)
    row = get_intent_or_404(conn, project_id, intent_id)
    if row["to_fact_id"] is not None:
        raise HTTPException(409, "Intent already concluded")
    if row["worker"] is not None and row["worker"] != worker:
        raise HTTPException(409, f"Intent is currently claimed by {row['worker']}")
    return row


def get_releasable_open_intent_or_404(
    conn: sqlite3.Connection, project_id: str, intent_id: str, worker: str
) -> sqlite3.Row:
    expire_workers(conn, project_id)
    row = get_intent_or_404(conn, project_id, intent_id)
    if row["to_fact_id"] is not None:
        raise HTTPException(409, "Intent already concluded")
    if row["worker"] is None:
        return row
    if row["worker"] != worker:
        raise HTTPException(409, f"Intent is currently claimed by {row['worker']}")
    return row


def get_finding_or_404(
    conn: sqlite3.Connection, project_id: str, finding_id: str
) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM findings WHERE id = ? AND project_id = ?",
        (finding_id, project_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "Finding not found")
    return row


def intent_to_model(conn: sqlite3.Connection, row: sqlite3.Row, project_id: str) -> Intent:
    sources = conn.execute(
        "SELECT fact_id FROM intent_sources WHERE intent_id = ? AND project_id = ? ORDER BY rowid",
        (row["id"], project_id),
    ).fetchall()
    return Intent(
        id=row["id"],
        **{"from": [s["fact_id"] for s in sources]},
        to=row["to_fact_id"],
        description=row["description"],
        creator=row["creator"],
        worker=row["worker"],
        last_heartbeat_at=row["last_heartbeat_at"],
        created_at=row["created_at"],
        concluded_at=row["concluded_at"],
        intent_kind=row["intent_kind"],
        finding_id=row["finding_id"],
        auth_scope=row["auth_scope"],
    )


def build_intents(conn: sqlite3.Connection, project_id: str) -> list[Intent]:
    rows = conn.execute(
        "SELECT * FROM intents WHERE project_id = ? ORDER BY created_at",
        (project_id,),
    ).fetchall()
    return [intent_to_model(conn, r, project_id) for r in rows]


def finding_to_model(row: sqlite3.Row) -> Finding:
    return Finding(
        id=row["id"],
        title=row["title"],
        vulnerability_type=row["vulnerability_type"],
        severity=row["severity"],
        target=row["target"],
        location=row["location"],
        impact=row["impact"],
        evidence=row["evidence"],
        reproduction=row["reproduction"],
        remediation=row["remediation"],
        status=row["status"],
        research_value=row["research_value"],
        next_action=row["next_action"],
        followup_reason=row["followup_reason"],
        followup_intent_description=row["followup_intent_description"],
        followup_intent_id=row["followup_intent_id"],
        report_status=row["report_status"],
        report_intent_id=row["report_intent_id"],
        triaged_at=row["triaged_at"],
        fact_id=row["fact_id"],
        intent_id=row["intent_id"],
        created_at=row["created_at"],
    )


def build_findings(conn: sqlite3.Connection, project_id: str) -> list[Finding]:
    rows = conn.execute(
        "SELECT * FROM findings WHERE project_id = ? ORDER BY created_at, id",
        (project_id,),
    ).fetchall()
    return [finding_to_model(row) for row in rows]


def account_to_model(row: sqlite3.Row) -> ProjectAccount:
    cookies = json.loads(row["cookies_json"] or "[]")
    return ProjectAccount(
        id=row["id"],
        label=row["label"],
        cookies=cookies,
    )


def build_project_accounts(conn: sqlite3.Connection, project_id: str) -> list[ProjectAccount]:
    rows = conn.execute(
        "SELECT * FROM project_accounts WHERE project_id = ? ORDER BY id",
        (project_id,),
    ).fetchall()
    accounts = []
    for row in rows:
        try:
            accounts.append(account_to_model(row))
        except (ValueError, TypeError, json.JSONDecodeError):
            continue
    return accounts


def get_intent_timeout(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT intent_timeout FROM settings WHERE rowid = 1").fetchone()
    return row["intent_timeout"]


def get_reason_timeout(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT reason_timeout FROM settings WHERE rowid = 1").fetchone()
    return row["reason_timeout"]


def project_reason_from_row(row: sqlite3.Row) -> ProjectReason | None:
    if row["reason_worker"] is None:
        return None
    return ProjectReason(
        worker=row["reason_worker"],
        trigger=row["reason_trigger"],
        started_at=row["reason_started_at"],
        last_heartbeat_at=row["reason_last_heartbeat_at"],
    )


def project_meta_from_row(row: sqlite3.Row) -> ProjectMeta:
    return ProjectMeta(
        id=row["id"],
        title=row["title"],
        status=row["status"],
        project_kind=row["project_kind"],
        auth_mode=row["auth_mode"],
        parent_project_id=row["parent_project_id"],
        parent_snapshot_id=row["parent_snapshot_id"],
        created_at=row["created_at"],
        reason=project_reason_from_row(row),
        recon_max_reason_rounds=row["recon_max_reason_rounds"],
        recon_reason_rounds=row["recon_reason_rounds"],
        recon_explore_rounds=row["recon_explore_rounds"],
        recon_stable_rounds=row["recon_stable_rounds"],
        judge_status=row["judge_status"],
        judged_at=row["judged_at"],
    )


def clear_project_reason(conn: sqlite3.Connection, project_id: str) -> None:
    conn.execute(
        """
        UPDATE projects
        SET reason_worker = NULL,
            reason_trigger = NULL,
            reason_started_at = NULL,
            reason_last_heartbeat_at = NULL
        WHERE id = ?
        """,
        (project_id,),
    )


def expire_workers(conn: sqlite3.Connection, project_id: str | None = None) -> None:
    timeout = get_intent_timeout(conn)
    now = utcnow()
    query = """
        UPDATE intents
        SET worker = NULL
        WHERE to_fact_id IS NULL
          AND worker IS NOT NULL
          AND last_heartbeat_at IS NOT NULL
          AND (julianday(?) - julianday(last_heartbeat_at)) * 86400 > ?
    """
    params: tuple = (now, timeout)
    if project_id is not None:
        query = query.replace("WHERE ", "WHERE project_id = ? AND ", 1)
        params = (project_id, now, timeout)
    conn.execute(query, params)


def expire_reason_leases(conn: sqlite3.Connection, project_id: str | None = None) -> None:
    timeout = get_reason_timeout(conn)
    now = utcnow()
    query = """
        UPDATE projects
        SET reason_worker = NULL,
            reason_trigger = NULL,
            reason_started_at = NULL,
            reason_last_heartbeat_at = NULL
        WHERE reason_worker IS NOT NULL
          AND reason_last_heartbeat_at IS NOT NULL
          AND (julianday(?) - julianday(reason_last_heartbeat_at)) * 86400 > ?
    """
    params: tuple = (now, timeout)
    if project_id is not None:
        query = query.replace("WHERE ", "WHERE id = ? AND ", 1)
        params = (project_id, now, timeout)
    conn.execute(query, params)


def maybe_stop_recon_by_round_limit(conn: sqlite3.Connection, project_id: str) -> None:
    row = get_project_or_404(conn, project_id)
    if row["project_kind"] != "recon":
        return
    if row["recon_max_reason_rounds"] is None:
        return
    if row["recon_reason_rounds"] < row["recon_max_reason_rounds"]:
        return
    conn.execute(
        """
        UPDATE projects
        SET status = 'stopped',
            reason_worker = NULL,
            reason_trigger = NULL,
            reason_started_at = NULL,
            reason_last_heartbeat_at = NULL
        WHERE id = ?
        """,
        (project_id,),
    )
    conn.execute(
        "UPDATE intents SET worker = NULL WHERE project_id = ? AND concluded_at IS NULL",
        (project_id,),
    )


def increment_recon_reason_round(conn: sqlite3.Connection, project_id: str, stable: bool) -> ProjectMeta:
    row = check_project_kind(conn, project_id, "recon")
    if row["status"] != "active":
        raise HTTPException(403, f"Project is {row['status']}")
    if stable:
        conn.execute(
            """
            UPDATE projects
            SET recon_reason_rounds = recon_reason_rounds + 1,
                recon_stable_rounds = recon_stable_rounds + 1
            WHERE id = ?
            """,
            (project_id,),
        )
    else:
        conn.execute(
            """
            UPDATE projects
            SET recon_reason_rounds = recon_reason_rounds + 1,
                recon_stable_rounds = 0
            WHERE id = ?
            """,
            (project_id,),
        )
    maybe_stop_recon_by_round_limit(conn, project_id)
    return project_meta_from_row(get_project_or_404(conn, project_id))


def increment_recon_explore_round(conn: sqlite3.Connection, project_id: str) -> ProjectMeta:
    row = check_project_kind(conn, project_id, "recon")
    if row["status"] != "active":
        raise HTTPException(403, f"Project is {row['status']}")
    conn.execute(
        "UPDATE projects SET recon_explore_rounds = recon_explore_rounds + 1 WHERE id = ?",
        (project_id,),
    )
    return project_meta_from_row(get_project_or_404(conn, project_id))


def snapshot_to_model(row: sqlite3.Row) -> ProjectSnapshot:
    return ProjectSnapshot(
        id=row["id"],
        project_id=row["project_id"],
        snapshot_type=row["snapshot_type"],
        summary_yaml=row["summary_yaml"],
        selected_fact_ids=json.loads(row["selected_fact_ids_json"] or "[]"),
        stats=json.loads(row["stats_json"] or "{}"),
        created_at=row["created_at"],
    )


def get_snapshot_or_404(
    conn: sqlite3.Connection, project_id: str, snapshot_id: str
) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM project_snapshots WHERE id = ? AND project_id = ?",
        (snapshot_id, project_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "Snapshot not found")
    return row


def ephemeral_job_to_model(row: sqlite3.Row) -> EphemeralJob:
    result = None
    if row["result_json"]:
        result = json.loads(row["result_json"])
    return EphemeralJob(
        id=row["id"],
        project_id=row["project_id"],
        job_type=row["job_type"],
        status=row["status"],
        input_snapshot_yaml=row["input_snapshot_yaml"],
        result=result,
        error=row["error"],
        worker=row["worker"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        expires_at=row["expires_at"],
    )


def get_ephemeral_job_or_404(conn: sqlite3.Connection, job_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM ephemeral_jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Ephemeral job not found")
    return row


def finding_report_to_model(row: sqlite3.Row) -> FindingReport:
    return FindingReport(
        id=row["id"],
        project_id=row["project_id"],
        finding_id=row["finding_id"],
        intent_id=row["intent_id"],
        report_markdown=row["report_markdown"],
        report_json=json.loads(row["report_json"] or "{}"),
        created_at=row["created_at"],
    )
