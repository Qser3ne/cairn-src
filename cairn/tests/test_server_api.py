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
        "title": "collection",
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


def _insert_legacy_snapshot(client: TestClient, project_id: str, selected_fact_ids: list[str] | None = None) -> dict:
    summary_yaml = client.get(f"/projects/{project_id}/export?format=yaml").text
    with db.get_conn() as conn:
        snapshot_id = "snap_001"
        conn.execute(
            """
            INSERT INTO project_snapshots (
                id, project_id, snapshot_type, summary_yaml, selected_fact_ids_json, stats_json, created_at
            ) VALUES (?, ?, 'legacy_recon_fork', ?, ?, ?, '2026-01-01T00:00:00Z')
            """,
            (
                snapshot_id,
                project_id,
                summary_yaml,
                json.dumps(selected_fact_ids or []),
                json.dumps({"selected_fact_count": len(selected_fact_ids or [])}),
            ),
        )
    return {"id": snapshot_id, "project_id": project_id}


def _insert_legacy_child_project(client: TestClient, parent_project_id: str, parent_snapshot_id: str) -> str:
    child_id = "proj_legacy_child"
    with db.get_conn() as conn:
        conn.execute(
            """
            INSERT INTO projects (
                id, title, status, project_kind, auth_mode, parent_project_id, parent_snapshot_id, created_at
            ) VALUES (?, 'legacy child', 'active', 'vuln', 'anonymous', ?, ?, '2026-01-01T00:00:00Z')
            """,
            (child_id, parent_project_id, parent_snapshot_id),
        )
        conn.execute(
            "INSERT INTO facts (id, project_id, description) VALUES ('origin', ?, 'https://target.test/child')",
            (child_id,),
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


def _create_authenticated_vuln(client: TestClient) -> dict:
    response = client.post(
        "/projects",
        json={
            "title": "vuln",
            "origin": "https://target.test",
            "auth_mode": "authenticated",
            "accounts": [{"cookies": [{"name": "sid", "value": "child"}]}],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_create_project_defaults_to_vuln_and_forbids_old_fields(client: TestClient) -> None:
    payload = _create_project(client)

    assert payload["project"]["project_kind"] == "vuln"
    assert payload["project"]["auth_mode"] == "dual"
    assert "collection_max_reason_rounds" not in payload["project"]
    assert "mode" not in payload["project"]
    assert "bootstrap_enabled" not in payload["project"]

    for field, value in (
        ("mode", "src"),
        ("bootstrap_enabled", False),
        ("goal", "finish"),
        ("recon_max_reason_rounds", 8),
        ("collection_max_reason_rounds", 8),
    ):
        response = client.post(
            "/projects",
            json={
                "title": "legacy",
                "origin": "start",
                field: value,
            },
        )
        assert response.status_code == 422


def test_create_project_defaults_to_vuln_project_kind_and_rejects_recon(client: TestClient) -> None:
    response = client.post(
        "/projects",
        json={"title": "collection", "origin": "https://target.test"},
    )

    assert response.status_code == 201, response.text
    assert response.json()["project"]["project_kind"] == "vuln"
    assert response.json()["project"]["auth_mode"] == "anonymous"

    legacy_recon = client.post(
        "/projects",
        json={
            "title": "legacy recon",
            "origin": "https://target.test",
            "project_kind": "recon",
            "accounts": [{"cookies": [{"name": "sessionid", "value": "secret-1"}]}],
        },
    )

    assert legacy_recon.status_code == 422


def test_write_requests_reject_unknown_fields(client: TestClient) -> None:
    project_id = _create_project(client)["project"]["id"]
    nested_cookie = client.post(
        "/projects",
        json={
            "title": "nested cookie extra",
            "origin": "start",
            "accounts": [{"cookies": [{"name": "sessionid", "value": "secret", "surprise": True}]}],
        },
    )

    hint = client.post(
        f"/projects/{project_id}/hints",
        json={"content": "new clue", "creator": "human", "surprise": True},
    )
    intent = client.post(
        f"/projects/{project_id}/intents",
        json={"from": ["origin"], "description": "work", "creator": "tester", "surprise": True},
    )
    status = client.put(
        f"/projects/{project_id}/status",
        json={"status": "stopped", "surprise": True},
    )
    settings = client.put(
        "/settings",
        json={"intent_timeout": 10, "reason_timeout": 10, "surprise": True},
    )

    assert nested_cookie.status_code == 422
    assert hint.status_code == 422
    assert intent.status_code == 422
    assert status.status_code == 422
    assert settings.status_code == 422


def test_static_ui_polling_avoids_fixed_interval_overlap(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "setInterval(async" not in response.text
    assert "resumePolling()" in response.text
    assert "this.resumePolling();" in response.text
    assert "pollGeneration: 0" in response.text
    assert "pausePolling()" in response.text
    assert "this.pollGeneration += 1;" in response.text
    assert "this.pausePolling();" in response.text
    assert "shouldApply: () => this.polling && this.selectedProjectId === projectId && this.view === 'graph'" in response.text
    assert "generation === this.pollGeneration" in response.text
    assert "if (updated) this.updateGraph();" in response.text
    assert "async pollOnce(generation = this.pollGeneration)" in response.text
    assert "setTimeout(() => this.pollOnce(generation), 5000)" in response.text
    assert "if (!projectId) {\n        this.resumePolling();\n        return;\n      }" in response.text

    open_project = response.text[
        response.text.index("async openProject(id)") : response.text.index("backToList(fromRoute)")
    ]
    assert open_project.index("this.selectedProjectId = id;") < open_project.index("this.resumePolling();")

    back_to_list = response.text[
        response.text.index("backToList(fromRoute)") : response.text.index("    startPolling()")
    ]
    assert back_to_list.index("this.selectedProjectId = '';") < back_to_list.index("this.resumePolling();")


def test_static_ui_uses_vuln_task_modes_without_recon_controls(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    ui = response.text
    for legacy_text in (
        "Evaluate Recon",
        "evaluateRecon()",
        "createReconSnapshot()",
        "prepareForkVulnFromSelectedProject()",
        "setNewProjectKind(",
        "Parent recon project",
        "Parent snapshot",
        "Fork Vuln",
        "/recon/judgements",
        "/fork-vuln/seed-jobs",
    ):
        assert legacy_text not in ui

    assert "Collection Coverage" in ui
    assert "Validation Work" in ui
    assert "Report Queue" in ui
    assert "Task Mode" in ui
    assert "intentTaskModeLabel(selectedIntentRecord().task_mode)" in ui
    assert "body.task_mode = this.intentForm.task_mode === 'collection' ? 'collection' : 'validation';" in ui


def test_static_ui_routes_report_intents_to_report_endpoint(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    ui = response.text
    assert "isReportIntent(intent)" in ui
    assert "concludeForm: { description: '', intentId: '', findingsJson: '', intentKind: '', taskMode: '' }" in ui
    assert "report_markdown: this.concludeForm.description" in ui
    assert "`/projects/${this.selectedProjectId}/intents/${this.concludeForm.intentId}/report`" in ui
    assert "`/projects/${this.selectedProjectId}/intents/${this.concludeForm.intentId}/conclude`" in ui


def test_static_ui_handles_completed_report_intents_without_fact_targets(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    ui = response.text
    assert "getFindingRecord(findingId)" in ui
    assert "const targetFact = intent.to ? this.getFactRecord(intent.to) : null;" in ui
    assert "if (targetFact) {" in ui
    assert "const reportFinding = this.isReportIntent(intent) ? this.getFindingRecord(intent.to) : null;" in ui
    assert "reportedFindingId: intent.to" in ui
    assert "targetType: 'intent'" in ui
    assert "`reported ${intent.to}`" in ui
    assert "if (entry.type === 'intent_concluded' && entry.reportedFindingId) return 'Report';" in ui
    assert "if (entry.type === 'intent_concluded' && entry.reportedFindingId) return 'bg-violet-50 text-violet-700';" in ui


def test_static_ui_uses_task_mode_placeholder_labels(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    graph_styles = response.text[
        response.text.index("graphStyles()") : response.text.index("    layoutOpts(")
    ]
    assert "node[nodeType=\"in_progress\"]" in graph_styles
    assert "node[nodeType=\"unclaimed\"]" in graph_styles
    assert "label:'?'" not in graph_styles
    assert graph_styles.count("label:'data(label)'") >= 2


def test_project_ids_follow_current_existing_max_after_deletes(client: TestClient) -> None:
    first = _create_project(client, title="first")["project"]["id"]
    second = _create_project(client, title="second")["project"]["id"]
    third = _create_project(client, title="third")["project"]["id"]
    assert [first, second, third] == ["proj_001", "proj_002", "proj_003"]

    assert client.delete(f"/projects/{second}").status_code == 204
    fourth = _create_project(client, title="fourth")["project"]["id"]
    assert fourth == "proj_004"

    assert client.delete(f"/projects/{fourth}").status_code == 204
    reused_max = _create_project(client, title="reused max")["project"]["id"]
    assert reused_max == "proj_004"


def test_project_id_restarts_when_no_projects_remain(client: TestClient) -> None:
    project_id = _create_project(client)["project"]["id"]
    assert project_id == "proj_001"

    assert client.delete(f"/projects/{project_id}").status_code == 204
    replacement_id = _create_project(client, title="replacement")["project"]["id"]
    assert replacement_id == "proj_001"


def test_authenticated_projects_require_accounts_and_persist_account_pool(client: TestClient) -> None:
    response = client.post(
        "/projects",
        json={
            "title": "collection without accounts",
            "origin": "start",
        },
    )
    assert response.status_code == 201
    assert response.json()["project"]["auth_mode"] == "anonymous"

    anonymous_with_accounts = client.post(
        "/projects",
        json={
            "title": "anonymous with accounts",
            "origin": "start",
            "auth_mode": "anonymous",
            "accounts": [{"cookies": [{"name": "sessionid", "value": "secret-1"}]}],
        },
    )
    assert anonymous_with_accounts.status_code == 422

    authenticated_with_accounts = client.post(
        "/projects",
        json={
            "title": "authenticated with accounts",
            "origin": "start",
            "auth_mode": "authenticated",
            "accounts": [{"cookies": [{"name": "sessionid", "value": "secret-1"}]}],
        },
    )
    assert authenticated_with_accounts.status_code == 201

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

    payload = _create_project(
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
    anonymous = client.post(
        "/projects",
        json={
            "title": "vuln anon",
            "origin": "start",
            "project_kind": "vuln",
            "auth_mode": "anonymous",
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
            "accounts": [{"cookies": [{"name": "sessionid", "value": "secret-1"}]}],
        },
    )
    assert authenticated.status_code == 201
    assert authenticated.json()["project"]["auth_mode"] == "authenticated"
    assert authenticated.json()["accounts"][0]["id"] == "a001"


def test_new_vuln_project_is_parentless_and_rejects_snapshot_fork_fields(client: TestClient) -> None:
    response = client.post(
        "/projects",
        json={
            "title": "vuln",
            "origin": "start",
            "project_kind": "vuln",
        },
    )
    assert response.status_code == 201
    assert response.json()["project"]["parent_project_id"] is None
    assert response.json()["project"]["parent_snapshot_id"] is None

    parent = _create_project(client)["project"]["id"]
    snapshot = _insert_legacy_snapshot(client, parent)
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
    assert response.status_code == 400
    assert "snapshot-based project forking has been removed" in response.text


def test_complete_and_reopen_routes_are_gone(client: TestClient) -> None:
    project_id = _create_project(client)["project"]["id"]

    assert client.post(f"/projects/{project_id}/complete").status_code == 410
    assert client.post(f"/projects/{project_id}/reopen").status_code == 410


def test_status_completed_is_terminal_and_clears_claims(client: TestClient) -> None:
    project_id = _create_project(client)["project"]["id"]
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


def test_reason_pending_is_coalesced_while_reason_is_running(client: TestClient) -> None:
    project_id = _create_project(client)["project"]["id"]
    client.post(
        f"/projects/{project_id}/intents",
        json={"from": ["origin"], "description": "work", "creator": "explorer", "worker": "explorer"},
    )
    claim = client.post(
        f"/projects/{project_id}/reason/claim",
        json={"worker": "reasoner", "trigger": "facts:1->2", "task_mode": "collection"},
    )
    assert claim.status_code == 200
    assert claim.json()["reason_pending"] is False

    concluded = client.post(
        f"/projects/{project_id}/intents/i001/conclude",
        json={"worker": "explorer", "description": "new fact"},
    )
    assert concluded.status_code == 200
    assert client.get(f"/projects/{project_id}").json()["project"]["reason_pending"] is True

    hinted = client.post(
        f"/projects/{project_id}/hints",
        json={"content": "new clue", "creator": "human"},
    )
    assert hinted.status_code == 201
    assert client.get(f"/projects/{project_id}").json()["project"]["reason_pending"] is True

    released = client.post(
        f"/projects/{project_id}/reason/release",
        json={"worker": "reasoner", "task_mode": "collection"},
    )
    assert released.status_code == 200
    assert released.json()["reason"] is None
    assert released.json()["reason_pending"] is True

    next_claim = client.post(
        f"/projects/{project_id}/reason/claim",
        json={"worker": "reasoner", "trigger": "pending", "task_mode": "collection"},
    )
    assert next_claim.status_code == 200
    assert next_claim.json()["reason_pending"] is False


def test_project_detail_orders_intents_by_created_at_then_id(client: TestClient) -> None:
    project_id = _create_project(client)["project"]["id"]
    with db.get_conn() as conn:
        conn.execute(
            """
            INSERT INTO intents (id, project_id, description, creator, created_at, intent_kind, auth_scope)
            VALUES ('i010', ?, 'late id inserted first', 'tester', '2026-01-01T00:00:02Z', 'explore', 'anonymous')
            """,
            (project_id,),
        )
        conn.execute(
            """
            INSERT INTO intents (id, project_id, description, creator, created_at, intent_kind, auth_scope)
            VALUES ('i002', ?, 'early id inserted second', 'tester', '2026-01-01T00:00:02Z', 'explore', 'anonymous')
            """,
            (project_id,),
        )

    detail = client.get(f"/projects/{project_id}")

    assert detail.status_code == 200
    assert [intent["id"] for intent in detail.json()["intents"]] == ["i002", "i010"]


def test_legacy_snapshot_read_bad_json_fields_fall_back_to_empty_defaults(client: TestClient) -> None:
    project_id = _create_project(client)["project"]["id"]
    snapshot = _insert_legacy_snapshot(client, project_id)
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE project_snapshots SET selected_fact_ids_json = 'not-json', stats_json = 'not-json' WHERE id = ? AND project_id = ?",
            (snapshot["id"], project_id),
        )

    snapshots = client.get(f"/projects/{project_id}/snapshots")

    assert snapshots.status_code == 200
    assert snapshots.json()[0]["selected_fact_ids"] == []
    assert snapshots.json()[0]["stats"] == {}


def test_project_detail_bad_fact_and_account_json_fields_fall_back_to_empty_defaults(client: TestClient) -> None:
    project_id = _create_project(client)["project"]["id"]
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE facts SET details_json = 'not-json' WHERE id = 'origin' AND project_id = ?",
            (project_id,),
        )
        conn.execute(
            "UPDATE project_accounts SET cookies_json = 'not-json' WHERE id = 'a001' AND project_id = ?",
            (project_id,),
        )

    detail = client.get(f"/projects/{project_id}")

    assert detail.status_code == 200
    assert detail.json()["facts"][0]["details"] == {}
    assert detail.json()["accounts"] == []


def test_export_bad_report_json_falls_back_to_empty_object(client: TestClient) -> None:
    project_id = _create_project(client)["project"]["id"]
    with db.get_conn() as conn:
        conn.execute(
            """
            INSERT INTO intents (id, project_id, to_fact_id, description, creator, created_at, concluded_at, intent_kind)
            VALUES ('i900', ?, 'origin', 'report finding', 'tester', '2026-01-01T00:00:02Z', '2026-01-01T00:00:03Z', 'report')
            """,
            (project_id,),
        )
        conn.execute(
            """
            INSERT INTO findings (
                id, project_id, fact_id, intent_id, title, vulnerability_type, severity,
                target, location, impact, evidence, reproduction, remediation, status,
                research_value, next_action, created_at
            ) VALUES (
                'v900', ?, 'origin', 'i900', 'Finding', 'idor', 'medium',
                'https://target.test', 'GET /api', 'impact', 'evidence', 'steps', 'fix', 'candidate',
                'medium', 'triage', '2026-01-01T00:00:03Z'
            )
            """,
            (project_id,),
        )
        conn.execute(
            """
            INSERT INTO finding_reports (id, project_id, finding_id, intent_id, report_markdown, report_json, created_at)
            VALUES ('r900', ?, 'v900', 'i900', '# Report', 'not-json', '2026-01-01T00:00:04Z')
            """,
            (project_id,),
        )

    response = client.get(f"/projects/{project_id}/export?format=yaml")

    assert response.status_code == 200
    exported = yaml.safe_load(response.text)
    assert exported["reports"][0]["report_json"] == {}


def test_conclude_persists_structured_feature_fact_and_export(client: TestClient) -> None:
    project_id = _create_project(client)["project"]["id"]
    client.post(
        f"/projects/{project_id}/intents",
        json={"from": ["origin"], "description": "map upload page", "creator": "reasoner", "auth_scope": "anonymous"},
    )

    response = client.post(
        f"/projects/{project_id}/intents/i001/conclude",
        json={
            "worker": "explorer",
            "description": "intent_summary: mapped upload page",
            "fact_type": "feature_surface",
            "title": "Upload page",
            "summary": "用户可以选择图片并提交上传",
            "details": {
                "user_actions": ["选择图片", "提交上传"],
                "routes": ["/upload"],
                "apis": ["POST /api/upload"],
            },
        },
    )

    assert response.status_code == 200, response.text
    fact = response.json()["fact"]
    assert fact["fact_type"] == "feature_surface"
    assert fact["title"] == "Upload page"
    assert fact["details"]["apis"] == ["POST /api/upload"]

    detail = client.get(f"/projects/{project_id}").json()
    persisted = next(fact for fact in detail["facts"] if fact["id"] == "f001")
    assert persisted["summary"] == "用户可以选择图片并提交上传"
    exported = yaml.safe_load(client.get(f"/projects/{project_id}/export?format=yaml").text)
    exported_fact = next(fact for fact in exported["facts"] if fact["id"] == "f001")
    assert exported_fact["fact_type"] == "feature_surface"
    assert exported_fact["details"]["routes"] == ["/upload"]
    assert exported["intents"][0]["task_mode"] == "validation"
    assert "recon" not in exported


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
    payload = response.json()
    assert payload["collection_reason_rounds"] == 2
    assert payload["collection_stable_rounds"] == 0
    assert payload["status"] == "active"
    exported = yaml.safe_load(client.get(f"/projects/{project_id}/export?format=yaml").text)
    assert "max_reason_rounds" not in exported["collection"]
    assert exported["collection"]["reason_rounds"] == 2
    assert exported["collection"]["stable_rounds"] == 0


def test_legacy_child_projects_still_protect_parent_deletion(client: TestClient) -> None:
    parent = _create_project(client)
    parent_id = parent["project"]["id"]
    snapshot = _insert_legacy_snapshot(client, parent_id)
    child_id = _insert_legacy_child_project(client, parent_id, snapshot["id"])

    assert client.get(f"/projects/{parent_id}/children").json()[0]["id"] == child_id
    assert client.delete(f"/projects/{parent_id}").status_code == 409


def test_recon_specific_write_apis_are_gone(client: TestClient) -> None:
    parent_id = _create_project(client)["project"]["id"]
    snapshot = _insert_legacy_snapshot(client, parent_id)

    assert client.post(f"/projects/{parent_id}/snapshots", json={"selected_fact_ids": []}).status_code == 410
    assert client.post(
        f"/projects/{parent_id}/fork-vuln",
        json={"title": "legacy fork", "snapshot_id": snapshot["id"]},
    ).status_code == 410
    assert client.post(
        f"/projects/{parent_id}/fork-vuln/seed-jobs",
        json={"title": "legacy seed", "snapshot_id": snapshot["id"]},
    ).status_code == 410
    assert client.get(f"/projects/{parent_id}/fork-vuln/seed-jobs").status_code == 410
    assert client.post(f"/projects/{parent_id}/recon/judgements").status_code == 410
    assert client.get(f"/projects/{parent_id}/recon/judgements").status_code == 410
    assert client.get(f"/projects/{parent_id}/recon/judgements/judge_001").status_code == 410


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


def test_collection_conclude_rejects_findings(client: TestClient) -> None:
    project_id = _create_project(client)["project"]["id"]
    client.post(
        f"/projects/{project_id}/intents",
        json={
            "from": ["origin"],
            "description": "verify idor",
            "creator": "reasoner",
            "task_mode": "collection",
            "auth_scope": "anonymous",
        },
    )

    response = client.post(
        f"/projects/{project_id}/intents/i001/conclude",
        json={
            "worker": "explorer",
            "description": "collection fact",
            "findings": [
                {
                    "title": "collection should not write findings",
                    "vulnerability_type": "idor",
                    "severity": "medium",
                    "target": "https://target.test",
                    "location": "GET /api/orders/1",
                    "impact": "other users may read orders",
                    "evidence": "changed order id returned another record",
                    "reproduction": "request adjacent order id",
                    "remediation": "enforce object ownership checks",
                    "status": "candidate",
                    "research_value": "medium",
                    "next_action": "follow_up",
                    "followup_intent_description": "Validate adjacent order export IDOR",
                }
            ],
        },
    )

    assert response.status_code == 400
    detail = client.get(f"/projects/{project_id}").json()
    assert detail["findings"] == []
    assert detail["intents"][0]["to"] is None


def test_collection_task_mode_rejects_report_intents(client: TestClient) -> None:
    project_id = _create_project(client)["project"]["id"]

    response = client.post(
        f"/projects/{project_id}/intents",
        json={
            "from": ["origin"],
            "description": "report mode must use report intent kind",
            "creator": "reasoner",
            "task_mode": "report",
        },
    )

    assert response.status_code == 400


def test_collection_requires_auth_scope_and_supports_anonymous_and_authenticated_lines(client: TestClient) -> None:
    project_id = _create_authenticated_vuln(client)["project"]["id"]

    missing_scope = client.post(
        f"/projects/{project_id}/intents",
        json={"from": ["origin"], "description": "map public catalog", "creator": "reasoner", "task_mode": "collection"},
    )
    assert missing_scope.status_code == 400

    anonymous = client.post(
        f"/projects/{project_id}/intents",
        json={
            "from": ["origin"],
            "description": "map catalog anonymous",
            "creator": "reasoner",
            "task_mode": "collection",
            "auth_scope": "anonymous",
        },
    )
    authenticated = client.post(
        f"/projects/{project_id}/intents",
        json={
            "from": ["origin"],
            "description": "map catalog authenticated",
            "creator": "reasoner",
            "task_mode": "collection",
            "auth_scope": "authenticated",
        },
    )
    validation_anonymous = client.post(
        f"/projects/{project_id}/intents",
        json={
            "from": ["origin"],
            "description": "validate anonymous idor",
            "creator": "reasoner",
            "task_mode": "validation",
            "auth_scope": "anonymous",
        },
    )

    assert anonymous.status_code == 201, anonymous.text
    assert anonymous.json()["auth_scope"] == "anonymous"
    assert authenticated.status_code == 201, authenticated.text
    assert authenticated.json()["auth_scope"] == "authenticated"
    assert validation_anonymous.status_code == 400


def test_collection_rejects_authenticated_scope_without_accounts(client: TestClient) -> None:
    project_id = _create_project(client, accounts=None, hints=[])["project"]["id"]

    response = client.post(
        f"/projects/{project_id}/intents",
        json={
            "from": ["origin"],
            "description": "map logged-in catalog",
            "creator": "reasoner",
            "task_mode": "collection",
            "auth_scope": "authenticated",
        },
    )

    assert response.status_code == 400
    assert "require project accounts" in response.text


def test_conclude_finding_lifecycle_creates_followup_and_report_intents(client: TestClient) -> None:
    project_id = _create_authenticated_vuln(client)["project"]["id"]
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
    task_modes = {intent["id"]: intent["task_mode"] for intent in detail["intents"]}
    assert task_modes["i001"] == "validation"
    assert task_modes["i002"] == "validation"
    assert task_modes["i003"] == "report"
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


def test_report_intent_cannot_use_fact_conclude_endpoint(client: TestClient) -> None:
    project_id = _create_authenticated_vuln(client)["project"]["id"]
    client.post(
        f"/projects/{project_id}/intents",
        json={"from": ["origin"], "description": "verify idor", "creator": "reasoner", "auth_scope": "authenticated"},
    )
    client.post(
        f"/projects/{project_id}/intents/i001/conclude",
        json={
            "worker": "explorer",
            "description": "confirmed idor",
            "findings": [{"title": "Reportable IDOR", "next_action": "report"}],
        },
    )

    response = client.post(
        f"/projects/{project_id}/intents/i002/conclude",
        json={"worker": "reporter", "description": "report intent should not write facts"},
    )

    assert response.status_code == 400
    detail = client.get(f"/projects/{project_id}").json()
    report_intent = next(intent for intent in detail["intents"] if intent["id"] == "i002")
    assert report_intent["to"] is None
