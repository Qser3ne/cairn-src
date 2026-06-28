from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from cairn.server.models import (
    EphemeralJob,
    Fact,
    Finding,
    Origin,
    ProjectAccount,
    ProjectMeta,
    ProjectReason,
    ProjectSnapshot,
    Task,
    TaskMode,
    empty_project_reasons,
)


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def safe_json_object(value: str | None) -> dict:
    try:
        data = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def safe_json_list(value: str | None) -> list:
    try:
        data = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


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
    return f"{prefix}{max_value + 1:03d}" if prefix.endswith("_") else f"{prefix}{max_value + 1}"


def next_project_id(conn: sqlite3.Connection) -> str:
    return _next_existing_numeric_id(conn, "projects", "id", "proj_")


def _next_scoped_id(conn: sqlite3.Connection, kind: str, prefix: str, project_id: str) -> str:
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
    return f"{prefix}{row['value']:03d}" if prefix.endswith("_") else f"{prefix}{row['value']}"


def next_task_id(conn: sqlite3.Connection, project_id: str) -> str:
    return _next_scoped_id(conn, "task", "t", project_id)


def next_fact_id(conn: sqlite3.Connection, project_id: str) -> str:
    return _next_scoped_id(conn, "fact", "f", project_id)


def next_finding_id(conn: sqlite3.Connection, project_id: str) -> str:
    return _next_scoped_id(conn, "finding", "F", project_id)


def next_hint_id(conn: sqlite3.Connection, project_id: str) -> str:
    return _next_scoped_id(conn, "hint", "h", project_id)


def next_account_id(conn: sqlite3.Connection, project_id: str) -> str:
    return _next_scoped_id(conn, "account", "a", project_id)


def next_snapshot_id(conn: sqlite3.Connection, project_id: str) -> str:
    return _next_scoped_id(conn, "snapshot", "snap_", project_id)


def next_ephemeral_job_id(conn: sqlite3.Connection, prefix: str = "judge_") -> str:
    return _next_existing_numeric_id(conn, "ephemeral_jobs", "id", prefix)


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


def validate_sources_exist(conn: sqlite3.Connection, project_id: str, source_ids: list[str]) -> None:
    for source_id in source_ids:
        if source_id == "origin":
            continue
        if source_id.startswith("f"):
            row = conn.execute(
                "SELECT 1 FROM facts WHERE id = ? AND project_id = ?",
                (source_id, project_id),
            ).fetchone()
        elif source_id.startswith("F"):
            row = conn.execute(
                "SELECT 1 FROM findings WHERE id = ? AND project_id = ?",
                (source_id, project_id),
            ).fetchone()
        else:
            raise HTTPException(400, f"Unsupported task source {source_id}")
        if row is None:
            raise HTTPException(404, f"Source {source_id} not found")


def get_task_or_404(conn: sqlite3.Connection, project_id: str, task_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM tasks WHERE id = ? AND project_id = ?",
        (task_id, project_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "Task not found")
    return row


def get_claimable_open_task_or_404(
    conn: sqlite3.Connection, project_id: str, task_id: str, worker: str
) -> sqlite3.Row:
    expire_workers(conn, project_id)
    row = get_task_or_404(conn, project_id, task_id)
    if row["completion_time"] is not None:
        raise HTTPException(409, "Task already concluded")
    if row["worker"] is not None and row["worker"] != worker:
        raise HTTPException(409, f"Task is currently claimed by {row['worker']}")
    return row


def get_releasable_open_task_or_404(
    conn: sqlite3.Connection, project_id: str, task_id: str, worker: str
) -> sqlite3.Row:
    expire_workers(conn, project_id)
    row = get_task_or_404(conn, project_id, task_id)
    if row["completion_time"] is not None:
        raise HTTPException(409, "Task already concluded")
    if row["worker"] is None:
        return row
    if row["worker"] != worker:
        raise HTTPException(409, f"Task is currently claimed by {row['worker']}")
    return row


def get_finding_or_404(conn: sqlite3.Connection, project_id: str, finding_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM findings WHERE id = ? AND project_id = ?",
        (finding_id, project_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "Finding not found")
    return row


def origin_from_project(row: sqlite3.Row) -> Origin:
    return Origin(description=row["origin"])


def task_sources(conn: sqlite3.Connection, project_id: str, task_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT source_id FROM task_sources WHERE task_id = ? AND project_id = ? ORDER BY rowid",
        (task_id, project_id),
    ).fetchall()
    return [row["source_id"] for row in rows]


def task_to_model(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    project_id: str,
    source_ids: list[str] | None = None,
) -> Task:
    if source_ids is None:
        source_ids = task_sources(conn, project_id, row["id"])
    return Task(
        id=row["id"],
        type=row["type"],
        description=row["description"],
        creation_time=row["creation_time"],
        completion_time=row["completion_time"],
        **{"from": source_ids},
        to=safe_json_list(row["to"]),
        worker=row["worker"],
        last_heartbeat_at=row["last_heartbeat_at"],
        auth_scope=row["auth_scope"],
    )


def build_tasks(conn: sqlite3.Connection, project_id: str) -> list[Task]:
    rows = conn.execute(
        "SELECT * FROM tasks WHERE project_id = ? ORDER BY creation_time, id",
        (project_id,),
    ).fetchall()
    sources_by_task = _task_sources_by_id(conn, project_id)
    return [task_to_model(conn, row, project_id, sources_by_task.get(row["id"], [])) for row in rows]


def _task_sources_by_id(conn: sqlite3.Connection, project_id: str) -> dict[str, list[str]]:
    rows = conn.execute(
        "SELECT task_id, source_id FROM task_sources WHERE project_id = ? ORDER BY task_id, rowid",
        (project_id,),
    ).fetchall()
    sources_by_task: dict[str, list[str]] = {}
    for row in rows:
        sources_by_task.setdefault(row["task_id"], []).append(row["source_id"])
    return sources_by_task


def fact_from_row(row: sqlite3.Row) -> Fact:
    return Fact(
        id=row["id"],
        type=row["type"],
        description=row["description"],
        creation_time=row["creation_time"],
        **{"from": safe_json_list(row["from"])},
        from_task=row["from_task"],
        to=safe_json_list(row["to"]),
        evidence=row["evidence"],
    )


def build_facts(conn: sqlite3.Connection, project_id: str) -> list[Fact]:
    rows = conn.execute(
        "SELECT * FROM facts WHERE project_id = ? ORDER BY creation_time, id",
        (project_id,),
    ).fetchall()
    return [fact_from_row(row) for row in rows]


def finding_to_model(row: sqlite3.Row) -> Finding:
    return Finding(
        id=row["id"],
        type=row["type"],
        description=row["description"],
        creation_time=row["creation_time"],
        **{"from": safe_json_list(row["from"])},
        from_task=row["from_task"],
        to=safe_json_list(row["to"]),
        report=row["report"],
    )


def build_findings(conn: sqlite3.Connection, project_id: str) -> list[Finding]:
    rows = conn.execute(
        "SELECT * FROM findings WHERE project_id = ? ORDER BY creation_time, id",
        (project_id,),
    ).fetchall()
    return [finding_to_model(row) for row in rows]


def account_to_model(row: sqlite3.Row) -> ProjectAccount:
    return ProjectAccount(
        id=row["id"],
        label=row["label"],
        cookies=safe_json_list(row["cookies_json"]),
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


def get_task_timeout(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT task_timeout FROM settings WHERE rowid = 1").fetchone()
    return row["task_timeout"]


def get_reason_timeout(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT reason_timeout FROM settings WHERE rowid = 1").fetchone()
    return row["reason_timeout"]


def project_reason_from_row(row: sqlite3.Row) -> ProjectReason | None:
    if "reason_worker" not in row.keys() or row["reason_worker"] is None:
        return None
    return ProjectReason(
        worker=row["reason_worker"],
        trigger=row["reason_trigger"],
        started_at=row["reason_started_at"],
        last_heartbeat_at=row["reason_last_heartbeat_at"],
    )


def project_reason_from_lease_row(row: sqlite3.Row) -> ProjectReason:
    return ProjectReason(
        worker=row["worker"],
        trigger=row["trigger"],
        started_at=row["started_at"],
        last_heartbeat_at=row["last_heartbeat_at"],
    )


def build_project_reasons(conn: sqlite3.Connection, project_id: str) -> dict[TaskMode, ProjectReason | None]:
    reasons = empty_project_reasons()
    rows = conn.execute(
        "SELECT * FROM project_reason_leases WHERE project_id = ?",
        (project_id,),
    ).fetchall()
    for lease in rows:
        task_mode = lease["task_mode"]
        if task_mode in reasons:
            reasons[task_mode] = project_reason_from_lease_row(lease)
    return reasons


def project_meta_from_row(row: sqlite3.Row, conn: sqlite3.Connection | None = None) -> ProjectMeta:
    reasons = build_project_reasons(conn, row["id"]) if conn is not None else empty_project_reasons()
    reason = next((lease for lease in reasons.values() if lease is not None), None) or project_reason_from_row(row)
    return ProjectMeta(
        id=row["id"],
        title=row["title"],
        status=row["status"],
        project_kind="vuln",
        auth_mode=row["auth_mode"],
        parent_project_id=row["parent_project_id"],
        parent_snapshot_id=row["parent_snapshot_id"],
        created_at=row["created_at"],
        reason=reason,
        reasons=reasons,
        reason_pending=bool(row["reason_pending"]),
        collection_reason_rounds=row["collection_reason_rounds"],
        collection_explore_rounds=row["collection_explore_rounds"],
        collection_stable_rounds=row["collection_stable_rounds"],
    )


def clear_project_reason(conn: sqlite3.Connection, project_id: str) -> None:
    conn.execute("DELETE FROM project_reason_leases WHERE project_id = ?", (project_id,))
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


def clear_reason_pending(conn: sqlite3.Connection, project_id: str) -> None:
    conn.execute("UPDATE projects SET reason_pending = 0 WHERE id = ?", (project_id,))


def mark_reason_pending_if_reason_running(conn: sqlite3.Connection, project_id: str) -> None:
    conn.execute(
        """
        UPDATE projects
        SET reason_pending = 1
        WHERE id = ?
          AND status = 'active'
          AND EXISTS (SELECT 1 FROM project_reason_leases WHERE project_id = projects.id)
        """,
        (project_id,),
    )


def expire_workers(conn: sqlite3.Connection, project_id: str | None = None) -> None:
    timeout = get_task_timeout(conn)
    now = utcnow()
    query = """
        UPDATE tasks
        SET worker = NULL
        WHERE completion_time IS NULL
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
        DELETE FROM project_reason_leases
        WHERE last_heartbeat_at IS NOT NULL
          AND (julianday(?) - julianday(last_heartbeat_at)) * 86400 > ?
    """
    params: tuple = (now, timeout)
    if project_id is not None:
        query = query.replace("WHERE ", "WHERE project_id = ? AND ", 1)
        params = (project_id, now, timeout)
    conn.execute(query, params)
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
    params = (now, timeout)
    if project_id is not None:
        query = query.replace("WHERE ", "WHERE id = ? AND ", 1)
        params = (project_id, now, timeout)
    conn.execute(query, params)


def append_task_output(conn: sqlite3.Connection, project_id: str, task_id: str, node_id: str) -> None:
    row = get_task_or_404(conn, project_id, task_id)
    outputs = safe_json_list(row["to"])
    if node_id not in outputs:
        outputs.append(node_id)
    conn.execute(
        "UPDATE tasks SET \"to\" = ? WHERE id = ? AND project_id = ?",
        (json.dumps(outputs, ensure_ascii=False), task_id, project_id),
    )


def append_node_downstream_task(conn: sqlite3.Connection, project_id: str, source_id: str, task_id: str) -> None:
    if source_id == "origin":
        return
    table = "facts" if source_id.startswith("f") else "findings"
    row = conn.execute(
        f"SELECT \"to\" FROM {table} WHERE id = ? AND project_id = ?",
        (source_id, project_id),
    ).fetchone()
    if row is None:
        return
    outputs = safe_json_list(row["to"])
    if task_id not in outputs:
        outputs.append(task_id)
    conn.execute(
        f"UPDATE {table} SET \"to\" = ? WHERE id = ? AND project_id = ?",
        (json.dumps(outputs, ensure_ascii=False), source_id, project_id),
    )


def increment_collection_reason_round(conn: sqlite3.Connection, project_id: str, stable: bool) -> ProjectMeta:
    row = check_project_kind(conn, project_id, "vuln")
    if row["status"] != "active":
        raise HTTPException(403, f"Project is {row['status']}")
    if stable:
        conn.execute(
            """
            UPDATE projects
            SET collection_reason_rounds = collection_reason_rounds + 1,
                collection_stable_rounds = collection_stable_rounds + 1
            WHERE id = ?
            """,
            (project_id,),
        )
    else:
        conn.execute(
            """
            UPDATE projects
            SET collection_reason_rounds = collection_reason_rounds + 1,
                collection_stable_rounds = 0
            WHERE id = ?
            """,
            (project_id,),
        )
    return project_meta_from_row(get_project_or_404(conn, project_id), conn)


def increment_collection_explore_round(conn: sqlite3.Connection, project_id: str) -> ProjectMeta:
    row = check_project_kind(conn, project_id, "vuln")
    if row["status"] != "active":
        raise HTTPException(403, f"Project is {row['status']}")
    conn.execute(
        "UPDATE projects SET collection_explore_rounds = collection_explore_rounds + 1 WHERE id = ?",
        (project_id,),
    )
    return project_meta_from_row(get_project_or_404(conn, project_id), conn)


def snapshot_to_model(row: sqlite3.Row) -> ProjectSnapshot:
    return ProjectSnapshot(
        id=row["id"],
        project_id=row["project_id"],
        snapshot_type=row["snapshot_type"],
        summary_yaml=row["summary_yaml"],
        selected_fact_ids=safe_json_list(row["selected_fact_ids_json"]),
        stats=safe_json_object(row["stats_json"]),
        created_at=row["created_at"],
    )


def get_snapshot_or_404(conn: sqlite3.Connection, project_id: str, snapshot_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM project_snapshots WHERE id = ? AND project_id = ?",
        (snapshot_id, project_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "Snapshot not found")
    return row


def ephemeral_job_to_model(row: sqlite3.Row) -> EphemeralJob:
    input_data = safe_json_object(row["input_json"]) if row["input_json"] else None
    result = safe_json_object(row["result_json"]) if row["result_json"] else None
    return EphemeralJob(
        id=row["id"],
        project_id=row["project_id"],
        job_type=row["job_type"],
        status=row["status"],
        input_snapshot_yaml=row["input_snapshot_yaml"],
        input=input_data,
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
