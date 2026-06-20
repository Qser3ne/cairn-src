from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from datetime import datetime
import json
import yaml

from cairn.server.db import get_conn
from cairn.server.services import expire_reason_leases, expire_workers, get_project_or_404

router = APIRouter(tags=["export"])


def format_export_timestamp(value: str | None) -> str | None:
    if not value:
        return value
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _load_project_data(conn, project_id: str):
    expire_workers(conn, project_id)
    expire_reason_leases(conn, project_id)
    proj = get_project_or_404(conn, project_id)

    facts = conn.execute(
        "SELECT id, description FROM facts WHERE project_id = ?", (project_id,)
    ).fetchall()
    hints = conn.execute(
        "SELECT content, creator, created_at FROM hints WHERE project_id = ? ORDER BY created_at",
        (project_id,),
    ).fetchall()
    findings = conn.execute(
        "SELECT * FROM findings WHERE project_id = ? ORDER BY created_at, id",
        (project_id,),
    ).fetchall()
    accounts = conn.execute(
        "SELECT * FROM project_accounts WHERE project_id = ? ORDER BY id",
        (project_id,),
    ).fetchall()
    intents = conn.execute(
        "SELECT * FROM intents WHERE project_id = ? ORDER BY created_at",
        (project_id,),
    ).fetchall()
    reports = conn.execute(
        "SELECT * FROM finding_reports WHERE project_id = ? ORDER BY created_at, id",
        (project_id,),
    ).fetchall()

    sources_by_intent = {}
    for i in intents:
        rows = conn.execute(
            "SELECT fact_id FROM intent_sources WHERE intent_id = ? AND project_id = ? ORDER BY rowid",
            (i["id"], project_id),
        ).fetchall()
        sources_by_intent[i["id"]] = [r["fact_id"] for r in rows]

    return proj, facts, hints, findings, accounts, intents, sources_by_intent, reports


def _export_yaml(conn, project_id: str) -> str:
    proj, facts, hints, findings, accounts, intents, sources_by_intent, reports = _load_project_data(conn, project_id)

    origin_desc = ""
    for f in facts:
        if f["id"] == "origin":
            origin_desc = f["description"]

    data: dict = {
        "project": {
            "title": proj["title"],
            "origin": origin_desc,
            "project_kind": proj["project_kind"],
            "auth_mode": proj["auth_mode"],
            "parent_project_id": proj["parent_project_id"],
            "parent_snapshot_id": proj["parent_snapshot_id"],
        }
    }

    if proj["project_kind"] == "recon":
        data["recon"] = {
            "max_reason_rounds": proj["recon_max_reason_rounds"],
            "reason_rounds": proj["recon_reason_rounds"],
            "explore_rounds": proj["recon_explore_rounds"],
            "stable_rounds": proj["recon_stable_rounds"],
            "judge_status": proj["judge_status"],
            "judged_at": format_export_timestamp(proj["judged_at"]),
        }

    if hints:
        data["hints"] = [
            {
                "content": h["content"],
                "creator": h["creator"],
                "created_at": format_export_timestamp(h["created_at"]),
            }
            for h in hints
        ]

    data["facts"] = [{"id": f["id"], "description": f["description"]} for f in facts]

    if findings:
        data["findings"] = [
            {
                "id": f["id"],
                "title": f["title"],
                "vulnerability_type": f["vulnerability_type"],
                "severity": f["severity"],
                "target": f["target"],
                "location": f["location"],
                "impact": f["impact"],
                "evidence": f["evidence"],
                "reproduction": f["reproduction"],
                "remediation": f["remediation"],
                "status": f["status"],
                "research_value": f["research_value"],
                "next_action": f["next_action"],
                "followup_reason": f["followup_reason"],
                "followup_intent_description": f["followup_intent_description"],
                "followup_intent_id": f["followup_intent_id"],
                "report_status": f["report_status"],
                "report_intent_id": f["report_intent_id"],
                "triaged_at": format_export_timestamp(f["triaged_at"]),
                "fact_id": f["fact_id"],
                "intent_id": f["intent_id"],
                "created_at": format_export_timestamp(f["created_at"]),
            }
            for f in findings
        ]

    if accounts:
        data["accounts"] = [
            {
                "id": account["id"],
                "label": account["label"],
                "username": account["username"],
                "password": account["password"],
            }
            for account in accounts
        ]

    intent_list = []
    for i in intents:
        entry: dict = {
            "from": sources_by_intent.get(i["id"], []),
            "to": i["to_fact_id"],
            "description": i["description"],
            "creator": i["creator"],
            "worker": i["worker"],
            "created_at": format_export_timestamp(i["created_at"]),
            "concluded_at": format_export_timestamp(i["concluded_at"]),
            "intent_kind": i["intent_kind"],
            "finding_id": i["finding_id"],
        }
        intent_list.append(entry)

    if intent_list:
        data["intents"] = intent_list

    if reports:
        data["reports"] = [
            {
                "id": report["id"],
                "finding_id": report["finding_id"],
                "intent_id": report["intent_id"],
                "report_markdown": report["report_markdown"],
                "report_json": json.loads(report["report_json"] or "{}"),
                "created_at": format_export_timestamp(report["created_at"]),
            }
            for report in reports
        ]

    return yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False)


def _export_timeline(conn, project_id: str) -> str:
    proj, facts, hints, findings, accounts, intents, sources_by_intent, reports = _load_project_data(conn, project_id)

    facts_by_id = {f["id"]: f["description"] for f in facts}

    events: list[tuple[str, int, str]] = []  # (timestamp, order, text)
    order = 0

    origin_desc = facts_by_id.get("origin", "")
    ts = format_export_timestamp(proj["created_at"]) or ""
    block = (
        f"[{ts}] PROJECT CREATED\n"
        f"  kind: {proj['project_kind']}\n"
        f"  auth: {proj['auth_mode']}\n"
        f"  accounts: {len(accounts)}\n"
        f"  origin: {origin_desc}"
    )
    events.append((proj["created_at"] or "", order, block))
    order += 1

    for h in hints:
        ts = format_export_timestamp(h["created_at"]) or ""
        block = f"[{ts}] HINT by {h['creator']}\n  {h['content']}"
        events.append((h["created_at"] or "", order, block))
        order += 1

    for i in intents:
        src = sources_by_intent.get(i["id"], [])
        from_str = ", ".join(src)

        ts = format_export_timestamp(i["created_at"]) or ""
        meta = f"  from: {from_str}"
        meta += f"\n  kind: {i['intent_kind']}"
        if i["worker"] and not i["concluded_at"]:
            meta += f"\n  worker: {i['worker']} (in progress)"
        block = f"[{ts}] INTENT DECLARED {i['id']} by {i['creator']}\n{meta}\n  {i['description']}"
        events.append((i["created_at"] or "", order, block))
        order += 1

        if not i["concluded_at"] or not i["to_fact_id"]:
            continue

        ts = format_export_timestamp(i["concluded_at"]) or ""
        actor = i["worker"] or i["creator"]

        fact_desc = facts_by_id.get(i["to_fact_id"], "")
        block = f"[{ts}] INTENT CONCLUDED {i['id']} by {actor}\n  from: {from_str}\n  produced: {i['to_fact_id']}\n  {fact_desc}"

        events.append((i["concluded_at"] or "", order, block))
        order += 1

    for f in findings:
        ts = format_export_timestamp(f["created_at"]) or ""
        block = (
            f"[{ts}] FINDING {f['id']} [{f['severity']}] {f['title']}\n"
            f"  type: {f['vulnerability_type']}\n"
            f"  target: {f['target']}\n"
            f"  location: {f['location']}\n"
            f"  fact: {f['fact_id']}\n"
            f"  intent: {f['intent_id']}\n"
            f"  status: {f['status']}\n"
            f"  research_value: {f['research_value']}\n"
            f"  next_action: {f['next_action']}\n"
            f"  report_status: {f['report_status']}\n"
            f"  impact: {f['impact']}\n"
            f"  evidence: {f['evidence']}"
        )
        events.append((f["created_at"] or "", order, block))
        order += 1

    for report in reports:
        ts = format_export_timestamp(report["created_at"]) or ""
        block = (
            f"[{ts}] REPORT {report['id']} for {report['finding_id']}\n"
            f"  intent: {report['intent_id']}\n"
            f"  {report['report_markdown']}"
        )
        events.append((report["created_at"] or "", order, block))
        order += 1

    events.sort(key=lambda e: (e[0], e[1]))

    return "\n\n".join(e[2] for e in events) + "\n"


@router.get("/projects/{project_id}/export")
def export_project(project_id: str, format: str = "yaml"):
    if format not in ("yaml", "timeline"):
        raise HTTPException(400, "Supported formats: yaml, timeline")

    with get_conn() as conn:
        if format == "timeline":
            text = _export_timeline(conn, project_id)
        else:
            text = _export_yaml(conn, project_id)

        return Response(content=text, media_type="text/plain")
