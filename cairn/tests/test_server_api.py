from __future__ import annotations

import json

from fastapi.testclient import TestClient
import pytest
import yaml

from cairn.server import db
from cairn.server.app import app


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setattr(db, "_db_path", None)
    db.configure(tmp_path / "cairn.db")
    with TestClient(app) as test_client:
        yield test_client


def _create_project(client: TestClient, **overrides) -> dict:
    body = {
        "title": "blackboard",
        "origin": "https://target.test",
        "hints": [{"content": "initial clue", "creator": "human"}],
    }
    body.update(overrides)
    response = client.post("/projects", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _insert_legacy_snapshot(project_id: str, summary_yaml: str = "project: {}\n") -> dict:
    with db.get_conn() as conn:
        snapshot_id = "snap_001"
        conn.execute(
            """
            INSERT INTO project_snapshots (
                id, project_id, snapshot_type, summary_yaml, selected_fact_ids_json, stats_json, created_at
            ) VALUES (?, ?, 'legacy_recon_fork', ?, '[]', '{}', '2026-01-01T00:00:00Z')
            """,
            (snapshot_id, project_id, summary_yaml),
        )
    return {"id": snapshot_id, "project_id": project_id}


def _insert_legacy_child_project(parent_project_id: str, parent_snapshot_id: str) -> str:
    child_id = "proj_legacy_child"
    with db.get_conn() as conn:
        conn.execute(
            """
            INSERT INTO projects (
                id, title, origin, status, project_kind, auth_mode, parent_project_id, parent_snapshot_id, created_at
            ) VALUES (?, 'legacy child', 'https://target.test/child', 'active', 'vuln', 'anonymous', ?, ?, '2026-01-01T00:00:00Z')
            """,
            (child_id, parent_project_id, parent_snapshot_id),
        )
    return child_id


def _insert_ephemeral_job(project_id: str, job_id: str, job_type: str) -> None:
    with db.get_conn() as conn:
        conn.execute(
            """
            INSERT INTO ephemeral_jobs (
                id, project_id, job_type, status, input_snapshot_yaml, created_at, expires_at
            ) VALUES (?, ?, ?, 'queued', 'project: {}', '2026-01-01T00:00:00Z', '2999-01-02T00:00:00Z')
            """,
            (job_id, project_id, job_type),
        )


def _create_task(client: TestClient, project_id: str, **overrides) -> dict:
    body = {
        "type": "collection_task",
        "from": ["origin"],
        "description": "Collect attack surface",
    }
    body.update(overrides)
    response = client.post(f"/projects/{project_id}/tasks", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _conclude_task(client: TestClient, project_id: str, task_id: str, **overrides) -> dict:
    body = {
        "worker": "worker",
        "description": "Observed API surface.",
        "evidence": f"/tmp/cairn/evidence/{task_id}.json",
    }
    body.update(overrides)
    response = client.post(f"/projects/{project_id}/tasks/{task_id}/conclude", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_create_project_defaults_to_vuln_blackboard_and_forbids_old_fields(client: TestClient) -> None:
    payload = _create_project(client)

    assert payload["project"]["project_kind"] == "vuln"
    assert payload["project"]["auth_mode"] == "anonymous"
    assert payload["origin"] == {"id": "origin", "description": "https://target.test"}
    assert payload["tasks"] == []
    assert payload["facts"] == []
    assert "intents" not in payload
    assert "collection_max_reason_rounds" not in payload["project"]

    for field, value in (
        ("mode", "src"),
        ("bootstrap_enabled", False),
        ("goal", "finish"),
        ("recon_max_reason_rounds", 8),
        ("collection_max_reason_rounds", 8),
        ("project_kind", "recon"),
    ):
        response = client.post(
            "/projects",
            json={"title": "legacy", "origin": "start", field: value},
        )
        assert response.status_code == 422


def test_write_requests_reject_unknown_fields(client: TestClient) -> None:
    project_id = _create_project(client)["project"]["id"]
    nested_cookie = client.post(
        "/projects",
        json={
            "title": "nested cookie extra",
            "origin": "start",
            "auth_mode": "authenticated",
            "accounts": [{"cookies": [{"name": "sessionid", "value": "secret", "surprise": True}]}],
        },
    )
    hint = client.post(
        f"/projects/{project_id}/hints",
        json={"content": "new clue", "creator": "human", "surprise": True},
    )
    task = client.post(
        f"/projects/{project_id}/tasks",
        json={"from": ["origin"], "type": "collection_task", "description": "work", "surprise": True},
    )
    status = client.put(
        f"/projects/{project_id}/status",
        json={"status": "stopped", "surprise": True},
    )
    settings = client.put(
        "/settings",
        json={
            "task_timeout": 10,
            "reason_timeout": 10,
            "initial_collection_rounds": 5,
            "collection_worker_limit": 1,
            "surprise": True,
        },
    )

    assert nested_cookie.status_code == 422
    assert hint.status_code == 422
    assert task.status_code == 422
    assert status.status_code == 422
    assert settings.status_code == 422


def test_settings_api_exposes_collection_scheduling_defaults_and_updates(client: TestClient) -> None:
    defaults = client.get("/settings")

    assert defaults.status_code == 200
    assert defaults.json() == {
        "task_timeout": 15,
        "reason_timeout": 15,
        "initial_collection_rounds": 5,
        "collection_worker_limit": 1,
    }

    updated = client.put(
        "/settings",
        json={
            "task_timeout": 20,
            "reason_timeout": 21,
            "initial_collection_rounds": 3,
            "collection_worker_limit": 2,
        },
    )

    assert updated.status_code == 200, updated.text
    assert client.get("/settings").json() == updated.json()


def test_settings_api_validates_collection_scheduling_bounds(client: TestClient) -> None:
    invalid_rounds = client.put(
        "/settings",
        json={
            "task_timeout": 10,
            "reason_timeout": 10,
            "initial_collection_rounds": -1,
            "collection_worker_limit": 1,
        },
    )
    invalid_limit = client.put(
        "/settings",
        json={
            "task_timeout": 10,
            "reason_timeout": 10,
            "initial_collection_rounds": 0,
            "collection_worker_limit": 0,
        },
    )

    assert invalid_rounds.status_code == 422
    assert invalid_limit.status_code == 422


def test_project_ids_follow_current_existing_max_after_deletes(client: TestClient) -> None:
    first = _create_project(client, title="one")
    second = _create_project(client, title="two")

    assert first["project"]["id"] == "proj_001"
    assert second["project"]["id"] == "proj_002"

    assert client.delete("/projects/proj_002").status_code == 204
    third = _create_project(client, title="three")

    assert third["project"]["id"] == "proj_002"


def test_authenticated_projects_require_accounts_and_persist_account_pool(client: TestClient) -> None:
    missing = client.post(
        "/projects",
        json={"title": "auth", "origin": "https://target.test", "auth_mode": "authenticated"},
    )
    assert missing.status_code == 422

    payload = _create_project(
        client,
        title="auth",
        auth_mode="authenticated",
        accounts=[
            {
                "label": "alice",
                "cookies": [{"name": "sessionid", "value": "secret-1"}],
            }
        ],
        hints=None,
    )
    project_id = payload["project"]["id"]

    assert payload["project"]["auth_mode"] == "authenticated"
    assert payload["accounts"][0]["id"] == "a1"
    assert client.get(f"/projects/{project_id}").json()["accounts"] == payload["accounts"]


def test_task_sources_validate_and_ids_are_scoped(client: TestClient) -> None:
    project_id = _create_project(client)["project"]["id"]

    missing = client.post(
        f"/projects/{project_id}/tasks",
        json={"type": "collection_task", "from": ["f404"], "description": "bad source"},
    )
    assert missing.status_code == 404

    first = _create_task(client, project_id)
    second = _create_task(client, project_id, description="Collect more")
    assert first["id"] == "t1"
    assert second["id"] == "t2"


def test_task_claim_release_conclude_and_graph_edges(client: TestClient) -> None:
    project_id = _create_project(client)["project"]["id"]
    task = _create_task(client, project_id)

    heartbeat = client.post(f"/projects/{project_id}/tasks/{task['id']}/heartbeat", json={"worker": "worker-a"})
    assert heartbeat.status_code == 200
    assert heartbeat.json()["worker"] == "worker-a"

    conflict = client.post(f"/projects/{project_id}/tasks/{task['id']}/heartbeat", json={"worker": "worker-b"})
    assert conflict.status_code == 409

    released = client.post(f"/projects/{project_id}/tasks/{task['id']}/release", json={"worker": "worker-a"})
    assert released.status_code == 200
    assert released.json()["worker"] is None

    response = _conclude_task(client, project_id, task["id"], worker="worker-b")
    assert response["fact"]["id"] == "f1"
    assert response["fact"]["type"] == "collection_fact"
    assert response["task"]["to"] == ["f1"]

    vulnerability_task = _create_task(
        client,
        project_id,
        type="vulnerability_task",
        **{"from": ["f1"]},
        description="Validate IDOR",
    )
    vuln_response = _conclude_task(
        client,
        project_id,
        vulnerability_task["id"],
        worker="worker-c",
        findings=[{"description": "IDOR confirmed"}],
    )
    assert vuln_response["fact"]["id"] == "f2"
    assert vuln_response["fact"]["type"] == "vulnerability_fact"
    assert vuln_response["findings"][0]["id"] == "F1"

    detail = client.get(f"/projects/{project_id}").json()
    assert detail["facts"][0]["to"] == ["t2"]
    assert detail["tasks"][1]["to"] == ["f2", "F1"]


def test_collection_conclude_rejects_findings_without_closing_task(client: TestClient) -> None:
    project_id = _create_project(client)["project"]["id"]
    task = _create_task(client, project_id)

    response = client.post(
        f"/projects/{project_id}/tasks/{task['id']}/conclude",
        json={
            "worker": "worker",
            "description": "collection fact",
            "evidence": "/tmp/evidence.json",
            "findings": [{"description": "collection cannot write this"}],
        },
    )

    assert response.status_code == 400
    detail = client.get(f"/projects/{project_id}").json()
    assert detail["facts"] == []
    assert detail["findings"] == []
    assert detail["tasks"][0]["completion_time"] is None


def test_finding_report_path_updates_finding_without_report_node(client: TestClient) -> None:
    project_id = _create_project(client)["project"]["id"]
    collection = _create_task(client, project_id)
    _conclude_task(client, project_id, collection["id"])
    vulnerability = _create_task(
        client,
        project_id,
        type="vulnerability_task",
        **{"from": ["f1"]},
        description="Validate IDOR",
    )
    _conclude_task(
        client,
        project_id,
        vulnerability["id"],
        findings=[{"description": "IDOR confirmed"}],
    )

    response = client.post(
        f"/projects/{project_id}/findings/F1/report",
        json={"worker": "reporter", "report": "/home/kali/reports/F1.md"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["report"] == "/home/kali/reports/F1.md"

    exported = yaml.safe_load(client.get(f"/projects/{project_id}/export?format=yaml").text)
    assert exported["findings"][0]["report"] == "/home/kali/reports/F1.md"
    assert "reports" not in exported


def test_status_completed_is_terminal_and_clears_claims(client: TestClient) -> None:
    project_id = _create_project(client)["project"]["id"]
    task = _create_task(client, project_id, worker="worker-a")
    claim = client.post(
        f"/projects/{project_id}/reason/claim",
        json={"worker": "reasoner", "trigger": "facts:0->1", "task_mode": "collection"},
    )
    assert claim.status_code == 200

    completed = client.put(f"/projects/{project_id}/status", json={"status": "completed"})
    assert completed.status_code == 200
    detail = client.get(f"/projects/{project_id}").json()
    assert detail["project"]["status"] == "completed"
    assert detail["tasks"][0]["id"] == task["id"]
    assert detail["tasks"][0]["worker"] is None
    assert detail["project"]["reason"] is None

    reopen = client.put(f"/projects/{project_id}/status", json={"status": "active"})
    assert reopen.status_code == 409


def test_reason_pending_is_coalesced_while_reason_is_running(client: TestClient) -> None:
    project_id = _create_project(client)["project"]["id"]
    task = _create_task(client, project_id)
    claim = client.post(
        f"/projects/{project_id}/reason/claim",
        json={"worker": "reasoner", "trigger": "initial", "task_mode": "collection"},
    )
    assert claim.status_code == 200

    _conclude_task(client, project_id, task["id"])

    detail = client.get(f"/projects/{project_id}").json()
    assert detail["project"]["reason_pending"] is True

    release = client.post(
        f"/projects/{project_id}/reason/release",
        json={"worker": "reasoner", "task_mode": "collection"},
    )
    assert release.status_code == 200


def test_project_detail_orders_tasks_by_creation_time_then_id(client: TestClient) -> None:
    project_id = _create_project(client)["project"]["id"]
    with db.get_conn() as conn:
        conn.execute(
            """
            INSERT INTO tasks (id, project_id, type, description, creation_time)
            VALUES ('t10', ?, 'collection_task', 'later id', '2026-01-01T00:00:02Z')
            """,
            (project_id,),
        )
        conn.execute(
            """
            INSERT INTO tasks (id, project_id, type, description, creation_time)
            VALUES ('t2', ?, 'collection_task', 'earlier id', '2026-01-01T00:00:01Z')
            """,
            (project_id,),
        )

    detail = client.get(f"/projects/{project_id}")
    assert [task["id"] for task in detail.json()["tasks"]] == ["t2", "t10"]


def test_legacy_snapshot_read_bad_json_fields_fall_back_to_empty_defaults(client: TestClient) -> None:
    project_id = _create_project(client)["project"]["id"]
    snapshot = _insert_legacy_snapshot(project_id)
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE project_snapshots SET selected_fact_ids_json = 'not-json', stats_json = 'not-json' WHERE id = ? AND project_id = ?",
            (snapshot["id"], project_id),
        )

    snapshots = client.get(f"/projects/{project_id}/snapshots")

    assert snapshots.status_code == 200
    assert snapshots.json()[0]["selected_fact_ids"] == []
    assert snapshots.json()[0]["stats"] == {}


def test_project_detail_bad_account_json_fields_fall_back_to_empty_defaults(client: TestClient) -> None:
    project_id = _create_project(
        client,
        auth_mode="authenticated",
        accounts=[{"cookies": [{"name": "sid", "value": "secret"}]}],
        hints=None,
    )["project"]["id"]
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE project_accounts SET cookies_json = 'not-json' WHERE project_id = ?",
            (project_id,),
        )

    detail = client.get(f"/projects/{project_id}")

    assert detail.status_code == 200
    assert detail.json()["accounts"] == []


def test_export_yaml_contains_blackboard_graph_and_timeline(client: TestClient) -> None:
    project_id = _create_project(client)["project"]["id"]
    task = _create_task(client, project_id)
    _conclude_task(client, project_id, task["id"])

    yaml_response = client.get(f"/projects/{project_id}/export?format=yaml")
    timeline_response = client.get(f"/projects/{project_id}/export?format=timeline")

    assert yaml_response.status_code == 200
    exported = yaml.safe_load(yaml_response.text)
    assert exported["origin"]["id"] == "origin"
    assert exported["tasks"][0]["id"] == "t1"
    assert exported["facts"][0]["id"] == "f1"
    assert "intents" not in exported
    assert "finding_reports" not in exported
    assert "TASK CREATED t1" in timeline_response.text
    assert "TASK CONCLUDED t1" in timeline_response.text


def test_hint_without_running_reason_does_not_mark_reason_pending(client: TestClient) -> None:
    project_id = _create_project(client)["project"]["id"]

    response = client.post(
        f"/projects/{project_id}/hints",
        json={"content": "new clue", "creator": "human"},
    )

    assert response.status_code == 201
    assert client.get(f"/projects/{project_id}").json()["project"]["reason_pending"] is False


def test_collection_rounds_do_not_stop_project_at_reason_limit(client: TestClient) -> None:
    project_id = _create_project(client)["project"]["id"]

    assert client.post(f"/projects/{project_id}/recon/reason-round", json={"stable": True}).json()["status"] == "active"
    response = client.post(f"/projects/{project_id}/recon/reason-round", json={"stable": False})

    assert response.status_code == 200
    assert response.json()["status"] == "active"
    assert response.json()["collection_reason_rounds"] == 2
    assert response.json()["collection_stable_rounds"] == 0


def test_legacy_child_projects_still_protect_parent_deletion(client: TestClient) -> None:
    parent_id = _create_project(client)["project"]["id"]
    snapshot = _insert_legacy_snapshot(parent_id)
    child_id = _insert_legacy_child_project(parent_id, snapshot["id"])

    response = client.delete(f"/projects/{parent_id}")

    assert response.status_code == 409
    assert client.get(f"/projects/{child_id}").status_code == 200


def test_retired_write_apis_are_gone(client: TestClient) -> None:
    project_id = _create_project(client)["project"]["id"]
    snapshot = _insert_legacy_snapshot(project_id)

    assert client.post(f"/projects/{project_id}/snapshots", json={"selected_fact_ids": []}).status_code == 410
    assert client.post(
        f"/projects/{project_id}/fork-vuln",
        json={"title": "legacy fork", "snapshot_id": snapshot["id"]},
    ).status_code == 410
    assert client.post(
        f"/projects/{project_id}/fork-vuln/seed-jobs",
        json={"title": "legacy seed", "snapshot_id": snapshot["id"]},
    ).status_code == 410
    assert client.get(f"/projects/{project_id}/fork-vuln/seed-jobs").status_code == 410
    assert client.post(f"/projects/{project_id}/recon/judgements").status_code == 410
    assert client.get(f"/projects/{project_id}/recon/judgements").status_code == 410
    assert client.get(f"/projects/{project_id}/recon/judgements/judge_001").status_code == 410


def test_legacy_ephemeral_jobs_are_hidden_from_dispatch_queue_and_cannot_be_claimed(client: TestClient) -> None:
    project_id = _create_project(client)["project"]["id"]
    _insert_ephemeral_job(project_id, "judge_001", "judge")
    _insert_ephemeral_job(project_id, "fork_001", "fork_seed")

    assert client.get("/ephemeral-jobs/queued?job_type=judge").json() == []
    assert client.get("/ephemeral-jobs/queued?job_type=fork_seed").json() == []
    assert client.post("/ephemeral-jobs/judge_001/claim", json={"worker": "worker"}).status_code == 410
    assert client.post("/ephemeral-jobs/fork_001/claim", json={"worker": "worker"}).status_code == 410

    with db.get_conn() as conn:
        rows = conn.execute("SELECT id, status FROM ephemeral_jobs ORDER BY id").fetchall()
    assert [(row["id"], row["status"]) for row in rows] == [("fork_001", "queued"), ("judge_001", "queued")]


def test_legacy_intent_api_is_not_registered(client: TestClient) -> None:
    project_id = _create_project(client)["project"]["id"]

    response = client.post(
        f"/projects/{project_id}/intents",
        json={"from": ["origin"], "description": "old", "creator": "tester"},
    )

    assert response.status_code == 404
