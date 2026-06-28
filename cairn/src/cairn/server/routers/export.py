from __future__ import annotations

from datetime import datetime
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
import yaml

from cairn.server.db import get_conn
from cairn.server.services import (
    expire_reason_leases,
    expire_workers,
    get_project_or_404,
    safe_json_list,
)

router = APIRouter(tags=["export"])


def format_export_timestamp(value: str | None) -> str | None:
    if not value:
        return value
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _account_cookies(account) -> list[dict]:
    try:
        cookies = json.loads(account["cookies_json"] or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(cookies, list):
        return []
    return [cookie for cookie in cookies if isinstance(cookie, dict)]


def _load_project_data(conn, project_id: str):
    expire_workers(conn, project_id)
    expire_reason_leases(conn, project_id)
    project = get_project_or_404(conn, project_id)
    tasks = conn.execute(
        "SELECT * FROM tasks WHERE project_id = ? ORDER BY creation_time, id",
        (project_id,),
    ).fetchall()
    task_sources = conn.execute(
        "SELECT task_id, source_id FROM task_sources WHERE project_id = ? ORDER BY task_id, rowid",
        (project_id,),
    ).fetchall()
    facts = conn.execute(
        "SELECT * FROM facts WHERE project_id = ? ORDER BY creation_time, id",
        (project_id,),
    ).fetchall()
    findings = conn.execute(
        "SELECT * FROM findings WHERE project_id = ? ORDER BY creation_time, id",
        (project_id,),
    ).fetchall()
    hints = conn.execute(
        "SELECT id, content, creator, created_at FROM hints WHERE project_id = ? ORDER BY created_at, id",
        (project_id,),
    ).fetchall()
    accounts = conn.execute(
        "SELECT * FROM project_accounts WHERE project_id = ? ORDER BY id",
        (project_id,),
    ).fetchall()

    sources_by_task = {}
    for row in task_sources:
        sources_by_task.setdefault(row["task_id"], []).append(row["source_id"])

    return project, tasks, sources_by_task, facts, findings, hints, accounts


def _task_entry(task, sources_by_task: dict[str, list[str]]) -> dict:
    return {
        "id": task["id"],
        "type": task["type"],
        "description": task["description"],
        "creation_time": format_export_timestamp(task["creation_time"]),
        "completion_time": format_export_timestamp(task["completion_time"]),
        "from": sources_by_task.get(task["id"], []),
        "to": safe_json_list(task["to"]),
        "worker": task["worker"],
        "auth_scope": task["auth_scope"],
    }


def _fact_entry(fact) -> dict:
    return {
        "id": fact["id"],
        "type": fact["type"],
        "description": fact["description"],
        "creation_time": format_export_timestamp(fact["creation_time"]),
        "from": safe_json_list(fact["from"]),
        "from_task": fact["from_task"],
        "to": safe_json_list(fact["to"]),
        "evidence": fact["evidence"],
    }


def _finding_entry(finding) -> dict:
    return {
        "id": finding["id"],
        "type": finding["type"],
        "description": finding["description"],
        "creation_time": format_export_timestamp(finding["creation_time"]),
        "from": safe_json_list(finding["from"]),
        "from_task": finding["from_task"],
        "to": safe_json_list(finding["to"]),
        "report": finding["report"],
    }


def _export_yaml(conn, project_id: str) -> str:
    project, tasks, sources_by_task, facts, findings, hints, accounts = _load_project_data(conn, project_id)
    data: dict = {
        "project": {
            "id": project["id"],
            "title": project["title"],
            "project_kind": project["project_kind"],
            "auth_mode": project["auth_mode"],
            "parent_project_id": project["parent_project_id"],
            "parent_snapshot_id": project["parent_snapshot_id"],
        },
        "origin": {
            "id": "origin",
            "description": project["origin"],
        },
        "tasks": [_task_entry(task, sources_by_task) for task in tasks],
        "facts": [_fact_entry(fact) for fact in facts],
        "findings": [_finding_entry(finding) for finding in findings],
    }
    if hints:
        data["hints"] = [
            {
                "content": hint["content"],
                "creator": hint["creator"],
                "created_at": format_export_timestamp(hint["created_at"]),
            }
            for hint in hints
        ]
    if accounts:
        data["accounts"] = [
            {"id": account["id"], "label": account["label"], "cookies": cookies}
            for account in accounts
            if (cookies := _account_cookies(account))
        ]
    return yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False)


def _export_timeline(conn, project_id: str) -> str:
    project, tasks, sources_by_task, facts, findings, hints, accounts = _load_project_data(conn, project_id)
    facts_by_id = {fact["id"]: fact["description"] for fact in facts}
    findings_by_id = {finding["id"]: finding["description"] for finding in findings}
    events: list[tuple[str, int, str]] = []
    order = 0

    ts = format_export_timestamp(project["created_at"]) or ""
    events.append(
        (
            project["created_at"] or "",
            order,
            f"[{ts}] PROJECT CREATED\n  kind: {project['project_kind']}\n  auth: {project['auth_mode']}\n  accounts: {len(accounts)}\n  origin: {project['origin']}",
        )
    )
    order += 1

    for hint in hints:
        ts = format_export_timestamp(hint["created_at"]) or ""
        events.append((hint["created_at"] or "", order, f"[{ts}] HINT by {hint['creator']}\n  {hint['content']}"))
        order += 1

    for task in tasks:
        ts = format_export_timestamp(task["creation_time"]) or ""
        source_text = ", ".join(sources_by_task.get(task["id"], []))
        block = (
            f"[{ts}] TASK CREATED {task['id']}\n"
            f"  type: {task['type']}\n"
            f"  from: {source_text}\n"
            f"  {task['description']}"
        )
        events.append((task["creation_time"] or "", order, block))
        order += 1
        if task["completion_time"]:
            produced = safe_json_list(task["to"])
            produced_text = ", ".join(produced)
            desc = "\n".join(
                f"  {node_id}: {facts_by_id.get(node_id) or findings_by_id.get(node_id) or ''}"
                for node_id in produced
            )
            ts = format_export_timestamp(task["completion_time"]) or ""
            events.append(
                (
                    task["completion_time"] or "",
                    order,
                    f"[{ts}] TASK CONCLUDED {task['id']}\n  produced: {produced_text}\n{desc}",
                )
            )
            order += 1

    for finding in findings:
        if finding["report"]:
            ts = format_export_timestamp(finding["creation_time"]) or ""
            events.append(
                (
                    finding["creation_time"] or "",
                    order,
                    f"[{ts}] REPORT PATH {finding['id']}\n  {finding['report']}",
                )
            )
            order += 1

    events.sort(key=lambda event: (event[0], event[1]))
    return "\n\n".join(event[2] for event in events) + "\n"


@router.get("/projects/{project_id}/export")
def export_project(project_id: str, format: str = "yaml"):
    if format not in ("yaml", "timeline"):
        raise HTTPException(400, "Supported formats: yaml, timeline")
    with get_conn() as conn:
        text = _export_timeline(conn, project_id) if format == "timeline" else _export_yaml(conn, project_id)
        return Response(content=text, media_type="text/plain")
