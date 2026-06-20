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


def _create_project(client: TestClient) -> str:
    response = client.post(
        "/projects",
        json={
            "title": "test",
            "origin": "starting point",
            "goal": "finish",
            "hints": [{"content": "initial clue", "creator": "human"}],
        },
    )
    assert response.status_code == 201
    assert response.json()["project"]["bootstrap_enabled"] is True
    assert response.json()["project"]["mode"] == "standard"
    assert response.json()["project"]["auth_mode"] == "anonymous"
    assert response.json()["accounts"] == []
    return response.json()["project"]["id"]


def test_project_workflow_create_conclude_complete_and_reopen(client: TestClient) -> None:
    project_id = _create_project(client)

    response = client.post(
        f"/projects/{project_id}/intents",
        json={"from": ["origin"], "description": "investigate", "creator": "reasoner", "worker": None},
    )
    assert response.status_code == 201
    assert response.json()["id"] == "i001"
    assert "session_lock" not in response.json()

    response = client.post(
        f"/projects/{project_id}/intents/i001/heartbeat",
        json={"worker": "explorer"},
    )
    assert response.status_code == 200
    assert response.json()["worker"] == "explorer"

    response = client.post(
        f"/projects/{project_id}/intents/i001/conclude",
        json={"worker": "explorer", "description": "new fact"},
    )
    assert response.status_code == 200
    assert response.json()["fact"] == {"id": "f001", "description": "new fact"}

    response = client.post(
        f"/projects/{project_id}/complete",
        json={"from": ["f001"], "description": "solved", "worker": "reasoner"},
    )
    assert response.status_code == 200
    assert response.json()["to"] == "goal"

    response = client.post(
        f"/projects/{project_id}/reopen",
        json={"description": "human correction", "creator": "human"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["project"]["status"] == "active"
    assert payload["fact"] == {"id": "f002", "description": "human correction"}
    assert payload["intent"]["from"] == ["f001"]
    assert payload["intent"]["to"] == "f002"


def test_stopping_project_releases_claims_and_reason_but_keeps_hints_writable(client: TestClient) -> None:
    project_id = _create_project(client)
    client.post(
        f"/projects/{project_id}/intents",
        json={"from": ["origin"], "description": "work", "creator": "worker-a", "worker": "worker-a"},
    )
    client.post(
        f"/projects/{project_id}/reason/claim",
        json={"worker": "worker-b", "trigger": "facts:2->3"},
    )

    response = client.put(f"/projects/{project_id}/status", json={"status": "stopped"})
    assert response.status_code == 200
    assert response.json()["reason"] is None

    detail = client.get(f"/projects/{project_id}").json()
    assert detail["intents"][0]["worker"] is None
    assert client.post(
        f"/projects/{project_id}/hints",
        json={"content": "manual note", "creator": "human"},
    ).status_code == 201
    assert client.post(
        f"/projects/{project_id}/intents",
        json={"from": ["origin"], "description": "blocked", "creator": "reasoner", "worker": None},
    ).status_code == 403


def test_intent_creation_rejects_goal_source_and_mismatched_initial_worker(client: TestClient) -> None:
    project_id = _create_project(client)

    assert client.post(
        f"/projects/{project_id}/intents",
        json={"from": ["goal"], "description": "invalid", "creator": "reasoner", "worker": None},
    ).status_code == 400
    assert client.post(
        f"/projects/{project_id}/intents",
        json={"from": ["origin"], "description": "invalid", "creator": "reasoner", "worker": "explorer"},
    ).status_code == 400


def test_settings_and_export_are_backed_by_the_same_database(client: TestClient) -> None:
    project_id = _create_project(client)

    response = client.put("/settings", json={"intent_timeout": 30, "reason_timeout": 45})
    assert response.status_code == 200
    assert client.get("/settings").json() == {"intent_timeout": 30, "reason_timeout": 45}

    exported = client.get(f"/projects/{project_id}/export?format=yaml")
    assert exported.status_code == 200
    assert "origin: starting point" in exported.text
    assert "goal: finish" in exported.text
    assert "auth_mode: anonymous" in exported.text
    assert client.get(f"/projects/{project_id}/export?format=invalid").status_code == 400


def test_authenticated_src_project_requires_accounts(client: TestClient) -> None:
    response = client.post(
        "/projects",
        json={
            "title": "auth src",
            "origin": "https://target.test",
            "goal": "find authenticated issues",
            "mode": "src",
            "auth_mode": "authenticated",
        },
    )

    assert response.status_code == 422


def test_anonymous_project_rejects_accounts(client: TestClient) -> None:
    response = client.post(
        "/projects",
        json={
            "title": "anonymous",
            "origin": "start",
            "goal": "finish",
            "accounts": [{"username": "alice", "password": "secret"}],
        },
    )

    assert response.status_code == 422


def test_authenticated_src_project_persists_accounts_and_exports_plaintext(client: TestClient) -> None:
    response = client.post(
        "/projects",
        json={
            "title": "auth src",
            "origin": "https://target.test",
            "goal": "find authenticated issues",
            "mode": "src",
            "auth_mode": "authenticated",
            "accounts": [
                {"label": "alice", "username": "alice@example.test", "password": "secret-1"},
                {"username": "bob@example.test", "password": "secret-2"},
            ],
        },
    )

    assert response.status_code == 201
    payload = response.json()
    project_id = payload["project"]["id"]
    assert payload["project"]["auth_mode"] == "authenticated"
    assert payload["project"]["bootstrap_enabled"] is False
    assert payload["accounts"] == [
        {"id": "a001", "label": "alice", "username": "alice@example.test", "password": "secret-1"},
        {"id": "a002", "label": "account-2", "username": "bob@example.test", "password": "secret-2"},
    ]
    detail = client.get(f"/projects/{project_id}").json()
    assert detail["accounts"][1]["label"] == "account-2"
    assert client.get("/projects").json()[0]["auth_mode"] == "authenticated"
    exported = client.get(f"/projects/{project_id}/export?format=yaml")
    assert "auth_mode: authenticated" in exported.text
    assert "username: alice@example.test" in exported.text
    assert "password: secret-1" in exported.text


def test_expired_intent_and_reason_leases_can_be_reclaimed(client: TestClient) -> None:
    project_id = _create_project(client)
    client.post(
        f"/projects/{project_id}/intents",
        json={"from": ["origin"], "description": "work", "creator": "worker-a", "worker": "worker-a"},
    )
    client.post(
        f"/projects/{project_id}/reason/claim",
        json={"worker": "worker-a", "trigger": "bootstrap"},
    )
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE intents SET last_heartbeat_at = '2000-01-01T00:00:00Z' WHERE project_id = ?",
            (project_id,),
        )
        conn.execute(
            "UPDATE projects SET reason_last_heartbeat_at = '2000-01-01T00:00:00Z' WHERE id = ?",
            (project_id,),
        )

    response = client.post(
        f"/projects/{project_id}/intents/i001/heartbeat",
        json={"worker": "worker-b"},
    )
    assert response.status_code == 200
    assert response.json()["worker"] == "worker-b"

    response = client.post(
        f"/projects/{project_id}/reason/claim",
        json={"worker": "worker-b", "trigger": "facts:2->3"},
    )
    assert response.status_code == 200
    assert response.json()["reason"]["worker"] == "worker-b"


def test_live_reason_lease_rejects_competing_worker(client: TestClient) -> None:
    project_id = _create_project(client)
    assert client.post(
        f"/projects/{project_id}/reason/claim",
        json={"worker": "worker-a", "trigger": "bootstrap"},
    ).status_code == 200

    response = client.post(
        f"/projects/{project_id}/reason/claim",
        json={"worker": "worker-b", "trigger": "facts:2->3"},
    )

    assert response.status_code == 409
    assert "worker-a" in response.json()["detail"]


def test_project_creation_persists_disabled_bootstrap_and_exports_it(client: TestClient) -> None:
    response = client.post(
        "/projects",
        json={
            "title": "no bootstrap",
            "origin": "start",
            "goal": "finish",
            "bootstrap_enabled": False,
        },
    )

    assert response.status_code == 201
    project_id = response.json()["project"]["id"]
    assert client.get(f"/projects/{project_id}").json()["project"]["bootstrap_enabled"] is False
    assert "bootstrap_enabled: false" in client.get(f"/projects/{project_id}/export?format=yaml").text


def test_src_project_creation_defaults_bootstrap_off_and_exports_mode(client: TestClient) -> None:
    response = client.post(
        "/projects",
        json={
            "title": "src project",
            "origin": "target https://example.test",
            "goal": "find multiple vulnerabilities",
            "mode": "src",
        },
    )

    assert response.status_code == 201
    payload = response.json()
    project_id = payload["project"]["id"]
    assert payload["project"]["mode"] == "src"
    assert payload["project"]["bootstrap_enabled"] is False
    detail = client.get(f"/projects/{project_id}").json()
    assert detail["project"]["mode"] == "src"
    exported = client.get(f"/projects/{project_id}/export?format=yaml")
    assert "mode: src" in exported.text
    assert "bootstrap_enabled: false" in exported.text


def test_duplicate_intent_same_sources_and_description_returns_409(client: TestClient) -> None:
    project_id = _create_project(client)
    body = {"from": ["origin"], "description": "Check upload bypass", "creator": "reasoner", "worker": None}

    assert client.post(f"/projects/{project_id}/intents", json=body).status_code == 201
    response = client.post(
        f"/projects/{project_id}/intents",
        json={**body, "description": "  check   upload BYPASS  "},
    )

    assert response.status_code == 409


def test_conclude_can_write_findings(client: TestClient) -> None:
    project_id = _create_project(client)
    client.post(
        f"/projects/{project_id}/intents",
        json={"from": ["origin"], "description": "verify idor", "creator": "reasoner", "worker": None},
    )

    response = client.post(
        f"/projects/{project_id}/intents/i001/conclude",
        json={
            "worker": "explorer",
            "description": "confirmed idor in order detail",
            "findings": [
                {
                    "title": "Order detail IDOR",
                    "vulnerability_type": "idor",
                    "severity": "high",
                    "target": "https://example.test",
                    "location": "/api/orders/{id}",
                    "impact": "user can read another user's order",
                    "evidence": "request /api/orders/2 returned foreign data",
                    "reproduction": "login as user A and request order 2",
                    "remediation": "enforce ownership checks",
                    "status": "open",
                }
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["findings"][0]["id"] == "v001"
    assert response.json()["findings"][0]["fact_id"] == "f001"
    detail = client.get(f"/projects/{project_id}").json()
    assert detail["findings"][0]["title"] == "Order detail IDOR"
    assert client.get("/projects").json()[0]["finding_count"] == 1


def test_project_creation_rejects_invalid_bootstrap_enabled(client: TestClient) -> None:
    response = client.post(
        "/projects",
        json={
            "title": "invalid bootstrap",
            "origin": "start",
            "goal": "finish",
            "bootstrap_enabled": "sometimes",
        },
    )

    assert response.status_code == 422
