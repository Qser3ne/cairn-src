from __future__ import annotations

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


def _create_project(client: TestClient) -> dict:
    response = client.post(
        "/projects",
        json={"title": "blackboard", "origin": "https://target.test"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_project_detail_exposes_blackboard_nodes_not_intents(client: TestClient) -> None:
    payload = _create_project(client)

    assert payload["origin"] == {"id": "origin", "description": "https://target.test"}
    assert payload["tasks"] == []
    assert payload["facts"] == []
    assert payload["findings"] == []
    assert "intents" not in payload


def test_task_fact_findings_and_report_path_flow(client: TestClient) -> None:
    project_id = _create_project(client)["project"]["id"]

    created_task = client.post(
        f"/projects/{project_id}/tasks",
        json={
            "type": "collection_task",
            "from": ["origin"],
            "description": "Collect attack surface",
        },
    )
    assert created_task.status_code == 201, created_task.text
    assert created_task.json()["id"] == "t1"
    assert created_task.json()["from"] == ["origin"]
    assert created_task.json()["to"] == []

    collection_findings = client.post(
        f"/projects/{project_id}/tasks/t1/conclude",
        json={
            "worker": "collectionE",
            "description": "Orders API exposes numeric ids.",
            "evidence": "/tmp/cairn/evidence/t1.json",
            "findings": [{"description": "collection must not write findings"}],
        },
    )
    assert collection_findings.status_code == 400

    concluded_collection = client.post(
        f"/projects/{project_id}/tasks/t1/conclude",
        json={
            "worker": "collectionE",
            "description": "Orders API exposes numeric ids.",
            "evidence": "/tmp/cairn/evidence/t1.json",
        },
    )
    assert concluded_collection.status_code == 200, concluded_collection.text
    fact = concluded_collection.json()["fact"]
    assert fact == {
        "id": "f1",
        "type": "collection_fact",
        "description": "Orders API exposes numeric ids.",
        "creation_time": fact["creation_time"],
        "from": ["origin"],
        "from_task": "t1",
        "to": [],
        "evidence": "/tmp/cairn/evidence/t1.json",
    }

    vuln_task = client.post(
        f"/projects/{project_id}/tasks",
        json={
            "type": "vulnerability_task",
            "from": ["f1"],
            "description": "Check order authorization",
        },
    )
    assert vuln_task.status_code == 201, vuln_task.text
    assert vuln_task.json()["id"] == "t2"

    concluded_vuln = client.post(
        f"/projects/{project_id}/tasks/t2/conclude",
        json={
            "worker": "vulnerabilityE",
            "description": "Changing the order id returned another user order.",
            "evidence": "/tmp/cairn/evidence/t2.json",
            "findings": [{"description": "IDOR allows reading other users' orders."}],
        },
    )
    assert concluded_vuln.status_code == 200, concluded_vuln.text
    body = concluded_vuln.json()
    assert body["fact"]["id"] == "f2"
    assert body["fact"]["type"] == "vulnerability_fact"
    assert body["findings"][0]["id"] == "F1"
    assert body["findings"][0]["type"] == "findings"
    assert body["findings"][0]["from"] == ["f1"]
    assert body["findings"][0]["from_task"] == "t2"
    assert body["findings"][0]["report"] is None

    report = client.post(
        f"/projects/{project_id}/findings/F1/report",
        json={"worker": "report_worker", "report": "/home/kali/reports/F1.md"},
    )
    assert report.status_code == 200, report.text
    assert report.json()["report"] == "/home/kali/reports/F1.md"

    detail = client.get(f"/projects/{project_id}").json()
    assert [task["id"] for task in detail["tasks"]] == ["t1", "t2"]
    assert detail["tasks"][0]["to"] == ["f1"]
    assert detail["facts"][0]["to"] == ["t2"]
    assert detail["findings"][0]["report"] == "/home/kali/reports/F1.md"
    assert "intents" not in detail

    exported = client.get(f"/projects/{project_id}/export?format=yaml")
    assert exported.status_code == 200
    data = yaml.safe_load(exported.text)
    assert "tasks" in data
    assert "intents" not in data
    assert "reports" not in data


def test_legacy_intent_api_is_removed(client: TestClient) -> None:
    project_id = _create_project(client)["project"]["id"]

    response = client.post(
        f"/projects/{project_id}/intents",
        json={"from": ["origin"], "description": "old", "creator": "tester"},
    )

    assert response.status_code == 404
