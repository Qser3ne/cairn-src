from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from cairn.server import db
from cairn.server.app import app


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setattr(db, "_db_path", None)
    db.configure(tmp_path / "cairn.db")
    with TestClient(app) as test_client:
        yield test_client


def _create_recon(client: TestClient, **overrides) -> dict:
    body = {
        "title": "recon",
        "origin": "https://target.test",
        "accounts": [
            {
                "label": "alice",
                "cookies": [{"name": "sessionid", "value": "secret-1"}],
            }
        ],
        "hints": [{"content": "initial clue", "creator": "human"}],
    }
    body.update(overrides)
    response = client.post("/projects", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _create_snapshot(client: TestClient, project_id: str, selected_fact_ids: list[str] | None = None) -> dict:
    response = client.post(
        f"/projects/{project_id}/snapshots",
        json={"snapshot_type": "recon_fork", "selected_fact_ids": selected_fact_ids or []},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_create_project_defaults_to_recon_and_forbids_old_fields(client: TestClient) -> None:
    payload = _create_recon(client)

    assert payload["project"]["project_kind"] == "recon"
    assert payload["project"]["auth_mode"] == "dual"
    assert payload["project"]["recon_max_reason_rounds"] == 8
    assert "mode" not in payload["project"]
    assert "bootstrap_enabled" not in payload["project"]

    for field, value in (("mode", "src"), ("bootstrap_enabled", False), ("goal", "finish")):
        response = client.post(
            "/projects",
            json={
                "title": "legacy",
                "origin": "start",
                field: value,
            },
        )
        assert response.status_code == 422


def test_project_ids_follow_current_existing_max_after_deletes(client: TestClient) -> None:
    first = _create_recon(client, title="first")["project"]["id"]
    second = _create_recon(client, title="second")["project"]["id"]
    third = _create_recon(client, title="third")["project"]["id"]
    assert [first, second, third] == ["proj_001", "proj_002", "proj_003"]

    assert client.delete(f"/projects/{second}").status_code == 204
    fourth = _create_recon(client, title="fourth")["project"]["id"]
    assert fourth == "proj_004"

    assert client.delete(f"/projects/{fourth}").status_code == 204
    reused_max = _create_recon(client, title="reused max")["project"]["id"]
    assert reused_max == "proj_004"


def test_project_id_restarts_when_no_projects_remain(client: TestClient) -> None:
    project_id = _create_recon(client)["project"]["id"]
    assert project_id == "proj_001"

    assert client.delete(f"/projects/{project_id}").status_code == 204
    replacement_id = _create_recon(client, title="replacement")["project"]["id"]
    assert replacement_id == "proj_001"


def test_authenticated_projects_require_accounts_and_persist_account_pool(client: TestClient) -> None:
    response = client.post(
        "/projects",
        json={
            "title": "recon without accounts",
            "origin": "start",
        },
    )
    assert response.status_code == 422

    for auth_mode in ("anonymous", "authenticated"):
        response = client.post(
            "/projects",
            json={
                "title": "recon explicit mode",
                "origin": "start",
                "auth_mode": auth_mode,
                "accounts": [{"cookies": [{"name": "sessionid", "value": "secret-1"}]}],
            },
        )
        assert response.status_code == 422

    duplicate_cookie = client.post(
        "/projects",
        json={
            "title": "duplicate cookies",
            "origin": "start",
            "accounts": [
                {
                    "cookies": [
                        {"name": "sessionid", "value": "one"},
                        {"name": "sessionid", "value": "two"},
                    ]
                }
            ],
        },
    )
    assert duplicate_cookie.status_code == 422

    payload = _create_recon(
        client,
        accounts=[
            {
                "label": "alice",
                "cookies": [
                    {"name": "sessionid", "value": "secret-1"},
                    {"name": "csrf", "value": "csrf-1"},
                ],
            },
            {"cookies": [{"name": "sessionid", "value": "secret-2"}]},
        ],
    )
    project_id = payload["project"]["id"]
    assert payload["accounts"] == [
        {
            "id": "a001",
            "label": "alice",
            "cookies": [
                {"name": "sessionid", "value": "secret-1"},
                {"name": "csrf", "value": "csrf-1"},
            ],
        },
        {"id": "a002", "label": "account-2", "cookies": [{"name": "sessionid", "value": "secret-2"}]},
    ]
    detail = client.get(f"/projects/{project_id}").json()
    assert detail["accounts"][1]["label"] == "account-2"
    exported = client.get(f"/projects/{project_id}/export?format=yaml").text
    assert "auth_mode: dual" in exported
    assert "name: sessionid" in exported
    assert "value: secret-1" in exported
    assert "username:" not in exported
    assert "password:" not in exported


def test_vuln_authenticated_projects_require_accounts(client: TestClient) -> None:
    parent = _create_recon(client)["project"]["id"]
    snapshot = _create_snapshot(client, parent)

    anonymous = client.post(
        "/projects",
        json={
            "title": "vuln anon",
            "origin": "start",
            "project_kind": "vuln",
            "auth_mode": "anonymous",
            "parent_project_id": parent,
            "parent_snapshot_id": snapshot["id"],
        },
    )
    assert anonymous.status_code == 201
    assert anonymous.json()["accounts"] == []

    missing = client.post(
        "/projects",
        json={
            "title": "vuln auth",
            "origin": "start",
            "project_kind": "vuln",
            "auth_mode": "authenticated",
            "parent_project_id": parent,
            "parent_snapshot_id": snapshot["id"],
        },
    )
    assert missing.status_code == 422

    authenticated = client.post(
        "/projects",
        json={
            "title": "vuln auth",
            "origin": "start",
            "project_kind": "vuln",
            "auth_mode": "authenticated",
            "parent_project_id": parent,
            "parent_snapshot_id": snapshot["id"],
            "accounts": [{"cookies": [{"name": "sessionid", "value": "secret-1"}]}],
        },
    )
    assert authenticated.status_code == 201
    assert authenticated.json()["project"]["auth_mode"] == "authenticated"
    assert authenticated.json()["accounts"][0]["id"] == "a001"


def test_new_vuln_project_requires_parent_snapshot(client: TestClient) -> None:
    response = client.post(
        "/projects",
        json={
            "title": "vuln",
            "origin": "start",
            "project_kind": "vuln",
        },
    )
    assert response.status_code == 422

    parent = _create_recon(client)["project"]["id"]
    snapshot = _create_snapshot(client, parent)
    response = client.post(
        "/projects",
        json={
            "title": "vuln",
            "origin": "start",
            "project_kind": "vuln",
            "parent_project_id": parent,
            "parent_snapshot_id": snapshot["id"],
        },
    )
    assert response.status_code == 201
    project = response.json()["project"]
    assert project["project_kind"] == "vuln"
    assert project["parent_project_id"] == parent
    assert project["parent_snapshot_id"] == snapshot["id"]


def test_complete_and_reopen_routes_are_gone(client: TestClient) -> None:
    project_id = _create_recon(client)["project"]["id"]

    assert client.post(f"/projects/{project_id}/complete").status_code == 410
    assert client.post(f"/projects/{project_id}/reopen").status_code == 410


def test_status_completed_is_terminal_and_clears_claims(client: TestClient) -> None:
    project_id = _create_recon(client)["project"]["id"]
    client.post(
        f"/projects/{project_id}/intents",
        json={"from": ["origin"], "description": "work", "creator": "worker-a", "worker": "worker-a"},
    )
    client.post(
        f"/projects/{project_id}/reason/claim",
        json={"worker": "worker-b", "trigger": "facts:2->3"},
    )

    response = client.put(f"/projects/{project_id}/status", json={"status": "completed"})
    assert response.status_code == 200
    assert response.json()["reason"] is None
    assert client.put(f"/projects/{project_id}/status", json={"status": "active"}).status_code == 409
    detail = client.get(f"/projects/{project_id}").json()
    assert detail["intents"][0]["worker"] is None


def test_recon_rounds_stop_project_at_reason_limit(client: TestClient) -> None:
    project_id = _create_recon(client, recon_max_reason_rounds=2)["project"]["id"]
    assert client.post(f"/projects/{project_id}/recon/reason-round", json={"stable": True}).json()["status"] == "active"
    response = client.post(f"/projects/{project_id}/recon/reason-round", json={"stable": False})
    assert response.status_code == 200
    payload = response.json()
    assert payload["recon_reason_rounds"] == 2
    assert payload["recon_stable_rounds"] == 0
    assert payload["status"] == "stopped"


def test_snapshot_fork_children_and_delete_guard(client: TestClient) -> None:
    parent = _create_recon(client)
    parent_id = parent["project"]["id"]
    client.post(
        f"/projects/{parent_id}/intents",
        json={"from": ["origin"], "description": "map upload", "creator": "reasoner", "worker": None, "auth_scope": "anonymous"},
    )
    client.post(
        f"/projects/{parent_id}/intents/i001/conclude",
        json={"worker": "explorer", "description": "upload endpoint accepts images"},
    )
    snapshot = _create_snapshot(client, parent_id, ["f001"])

    response = client.post(
        f"/projects/{parent_id}/fork-vuln",
        json={
            "title": "validate upload",
            "snapshot_id": snapshot["id"],
            "auth_mode": "anonymous",
        },
    )
    assert response.status_code == 201
    child = response.json()
    child_id = child["project"]["id"]
    assert child["project"]["project_kind"] == "vuln"
    assert child["project"]["parent_project_id"] == parent_id
    assert any("recon_snapshot" in fact["description"] for fact in child["facts"])
    assert client.get(f"/projects/{parent_id}/children").json()[0]["id"] == child_id
    assert client.delete(f"/projects/{parent_id}").status_code == 409


def test_recon_judgement_job_lifecycle_updates_project_judge_status(client: TestClient) -> None:
    project_id = _create_recon(client)["project"]["id"]
    response = client.post(f"/projects/{project_id}/recon/judgements")
    assert response.status_code == 201
    job_id = response.json()["job_id"]

    queued = client.get("/ephemeral-jobs/queued?job_type=judge").json()
    assert queued[0]["id"] == job_id
    claimed = client.post(f"/ephemeral-jobs/{job_id}/claim", json={"worker": "judge"}).json()
    assert claimed["status"] == "running"
    finished = client.post(
        f"/ephemeral-jobs/{job_id}/finish",
        json={"worker": "judge", "result": {"verdict": "ready", "summary": "enough signal"}},
    ).json()
    assert finished["status"] == "succeeded"
    detail = client.get(f"/projects/{project_id}").json()
    assert detail["project"]["judge_status"] == "ready"
    assert detail["facts"] == [
        {"id": "origin", "description": "https://target.test"},
    ]


def test_recon_judgement_ids_follow_current_existing_max_after_project_delete(client: TestClient) -> None:
    first_project = _create_recon(client, title="first")["project"]["id"]
    second_project = _create_recon(client, title="second")["project"]["id"]
    third_project = _create_recon(client, title="third")["project"]["id"]

    first_job = client.post(f"/projects/{first_project}/recon/judgements").json()["job_id"]
    second_job = client.post(f"/projects/{second_project}/recon/judgements").json()["job_id"]
    third_job = client.post(f"/projects/{third_project}/recon/judgements").json()["job_id"]
    assert [first_job, second_job, third_job] == ["judge_001", "judge_002", "judge_003"]

    assert client.delete(f"/projects/{second_project}").status_code == 204
    fourth_job = client.post(f"/projects/{first_project}/recon/judgements").json()["job_id"]
    assert fourth_job == "judge_004"

    assert client.delete(f"/projects/{first_project}").status_code == 204
    reused_max_job = client.post(f"/projects/{third_project}/recon/judgements").json()["job_id"]
    assert reused_max_job == "judge_004"


def test_recon_judgement_id_restarts_when_no_existing_jobs_remain(client: TestClient) -> None:
    project_id = _create_recon(client)["project"]["id"]
    job_id = client.post(f"/projects/{project_id}/recon/judgements").json()["job_id"]
    assert job_id == "judge_001"

    assert client.delete(f"/projects/{project_id}").status_code == 204
    replacement_project_id = _create_recon(client, title="replacement")["project"]["id"]
    replacement_job_id = client.post(f"/projects/{replacement_project_id}/recon/judgements").json()["job_id"]
    assert replacement_job_id == "judge_001"


def test_recon_judgement_fail_marks_running_job_failed(client: TestClient) -> None:
    project_id = _create_recon(client)["project"]["id"]
    response = client.post(f"/projects/{project_id}/recon/judgements")
    assert response.status_code == 201
    job_id = response.json()["job_id"]
    assert client.post(f"/ephemeral-jobs/{job_id}/claim", json={"worker": "judge"}).status_code == 200

    failed = client.post(
        f"/ephemeral-jobs/{job_id}/fail",
        json={"worker": "judge", "error": "judge cancelled: stopped"},
    )

    assert failed.status_code == 200, failed.text
    payload = failed.json()
    assert payload["status"] == "failed"
    assert payload["error"] == "judge cancelled: stopped"
    assert payload["finished_at"] is not None
    assert client.get(f"/projects/{project_id}").json()["project"]["judge_status"] == "not_judged"


def test_recon_judgement_results_are_listed_without_snapshot_payload(client: TestClient) -> None:
    project_id = _create_recon(client)["project"]["id"]
    other_project_id = _create_recon(client, title="other")["project"]["id"]
    first_job_id = client.post(f"/projects/{project_id}/recon/judgements").json()["job_id"]
    second_job_id = client.post(f"/projects/{project_id}/recon/judgements").json()["job_id"]
    other_job_id = client.post(f"/projects/{other_project_id}/recon/judgements").json()["job_id"]

    client.post(f"/ephemeral-jobs/{first_job_id}/claim", json={"worker": "judge-a"})
    client.post(
        f"/ephemeral-jobs/{first_job_id}/finish",
        json={"worker": "judge-a", "result": {"verdict": "ready", "score": 88}},
    )
    client.post(f"/ephemeral-jobs/{second_job_id}/claim", json={"worker": "judge-b"})
    client.post(
        f"/ephemeral-jobs/{second_job_id}/finish",
        json={"worker": "judge-b", "result": {"verdict": "not_ready", "score": 61}},
    )

    results = client.get(f"/projects/{project_id}/recon/judgements").json()

    assert [item["id"] for item in results] == [second_job_id, first_job_id]
    assert results[0]["result"] == {"verdict": "not_ready", "score": 61}
    assert results[1]["result"] == {"verdict": "ready", "score": 88}
    assert "input_snapshot_yaml" not in results[0]
    assert other_job_id not in [item["id"] for item in results]


def test_conclude_finding_lifecycle_creates_followup_and_report_intents(client: TestClient) -> None:
    project_id = _create_recon(client)["project"]["id"]
    client.post(
        f"/projects/{project_id}/intents",
        json={"from": ["origin"], "description": "verify idor", "creator": "reasoner", "worker": None, "auth_scope": "authenticated"},
    )
    response = client.post(
        f"/projects/{project_id}/intents/i001/conclude",
        json={
            "worker": "explorer",
            "description": "confirmed idor",
            "findings": [
                {
                    "title": "Follow-up IDOR",
                    "next_action": "follow_up",
                    "followup_intent_description": "Check adjacent order APIs",
                },
                {
                    "title": "Reportable IDOR",
                    "next_action": "report",
                    "research_value": "high",
                },
            ],
        },
    )
    assert response.status_code == 200
    findings = response.json()["findings"]
    assert findings[0]["followup_intent_id"] == "i002"
    assert findings[1]["report_intent_id"] == "i003"
    assert findings[1]["report_status"] == "queued"
    detail = client.get(f"/projects/{project_id}").json()
    intent_kinds = {intent["id"]: intent["intent_kind"] for intent in detail["intents"]}
    assert intent_kinds["i002"] == "explore"
    assert intent_kinds["i003"] == "report"
    intent_scopes = {intent["id"]: intent["auth_scope"] for intent in detail["intents"]}
    assert intent_scopes["i001"] == "authenticated"
    assert intent_scopes["i002"] == "authenticated"
    assert intent_scopes["i003"] is None

    report = client.post(
        f"/projects/{project_id}/intents/i003/report",
        json={"worker": "reporter", "report_markdown": "# IDOR report", "report_json": {"severity": "high"}},
    )
    assert report.status_code == 200
    detail = client.get(f"/projects/{project_id}").json()
    report_finding = next(item for item in detail["findings"] if item["title"] == "Reportable IDOR")
    assert report_finding["report_status"] == "drafted"
