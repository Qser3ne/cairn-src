from __future__ import annotations

import json

import pytest

from cairn.dispatcher.config import DispatchConfig
from cairn.dispatcher.contracts import (
    parse_json_output,
    validate_explore_payload,
    validate_reason_payload,
    validate_report_payload,
)
from cairn.dispatcher.workers.adapters.mock import MockDriver


def test_parse_json_output_extracts_wrapped_object() -> None:
    assert parse_json_output('prefix {"accepted": true, "data": {"ok": 1}} suffix') == {
        "accepted": True,
        "data": {"ok": 1},
    }


def test_reason_payload_accepts_tasks_and_limits_count() -> None:
    kind, tasks = validate_reason_payload(
        {
            "accepted": True,
            "data": {
                "tasks": [
                    {"from": ["origin"], "type": "collection_task", "auth_scope": "anonymous", "description": "one"},
                    {"from": ["origin"], "type": "collection_task", "auth_scope": "authenticated", "description": "two"},
                ]
            },
        },
        open_tasks_empty=True,
        max_tasks=1,
        task_mode="collection",
        require_auth_scope=True,
    )

    assert kind == "tasks"
    assert tasks == [
        {"from": ["origin"], "type": "collection_task", "auth_scope": "anonymous", "description": "one"}
    ]


def test_reason_payload_rejects_legacy_intents_key() -> None:
    with pytest.raises(ValueError, match="intents output is no longer supported"):
        validate_reason_payload(
            {"accepted": True, "data": {"intents": [{"from": ["origin"], "description": "old"}]}},
            open_tasks_empty=True,
            max_tasks=1,
            task_mode="collection",
        )


def test_reason_payload_requires_collection_auth_scope() -> None:
    with pytest.raises(ValueError, match="auth_scope is required"):
        validate_reason_payload(
            {"accepted": True, "data": {"tasks": [{"from": ["origin"], "description": "baseline"}]}},
            open_tasks_empty=True,
            max_tasks=1,
            task_mode="collection",
            require_auth_scope=True,
        )


def test_reason_payload_accepts_vulnerability_seed_from_collection_reason() -> None:
    kind, tasks = validate_reason_payload(
        {
            "accepted": True,
            "data": {
                "tasks": [
                    {
                        "from": ["f1"],
                        "type": "vulnerability_task",
                        "description": "validate IDOR hypothesis on order API",
                    }
                ]
            },
        },
        open_tasks_empty=False,
        max_tasks=2,
        task_mode="collection",
    )

    assert kind == "tasks"
    assert tasks[0]["type"] == "vulnerability_task"


def test_reason_payload_stable_and_rejected() -> None:
    assert validate_reason_payload(
        {"accepted": True, "data": {"decision": "no_new_high_value", "tasks": []}},
        open_tasks_empty=True,
        max_tasks=1,
        task_mode="vulnerability",
    ) == ("stable", None)
    assert validate_reason_payload(
        {"accepted": False, "reason": "no"},
        open_tasks_empty=True,
        max_tasks=1,
        task_mode="vulnerability",
    ) == ("rejected", None)


def test_explore_payload_requires_evidence_and_allows_vulnerability_findings() -> None:
    kind, data = validate_explore_payload(
        {
            "accepted": True,
            "data": {
                "description": "IDOR confirmed",
                "evidence": "/tmp/evidence/t2.json",
                "findings": [{"description": "Users can read other users' orders."}],
            },
        },
        task_mode="vulnerability",
    )

    assert kind == "fact"
    assert data == {
        "description": "IDOR confirmed",
        "evidence": "/tmp/evidence/t2.json",
        "findings": [{"description": "Users can read other users' orders."}],
    }


def test_collection_explore_payload_rejects_findings() -> None:
    with pytest.raises(ValueError, match="collection task payload cannot include findings"):
        validate_explore_payload(
            {
                "accepted": True,
                "data": {
                    "description": "collection fact",
                    "evidence": "/tmp/evidence/t1.json",
                    "findings": [{"description": "not allowed"}],
                },
            },
            task_mode="collection",
        )


def test_explore_payload_rejects_legacy_validation_reason_fields() -> None:
    with pytest.raises(ValueError, match="evidence is required"):
        validate_explore_payload(
            {"accepted": True, "data": {"description": "old validation_reason output"}},
            task_mode="vulnerability",
        )


def test_report_payload_accepts_report_path_only() -> None:
    assert validate_report_payload(
        {"accepted": True, "data": {"report": "/home/kali/reports/F1.md"}}
    ) == ("report", {"report": "/home/kali/reports/F1.md"})
    with pytest.raises(ValueError, match="report is required"):
        validate_report_payload({"accepted": True, "data": {"report_markdown": "# old"}})


def test_dispatch_config_accepts_vulnerability_worker_types() -> None:
    config = DispatchConfig.model_validate(
        {
            "server": "http://127.0.0.1:8000",
            "runtime": {
                "interval": 60,
                "max_workers": 2,
                "max_running_projects": 1,
                "max_project_workers": 2,
                "healthcheck_timeout": 5,
                "prompt_group": "default",
            },
            "tasks": {
                "reason": {"timeout": 10, "max_tasks": 3},
                "explore": {"timeout": 10, "conclude_timeout": 5},
                "report": {"timeout": 10},
            },
            "container": {"image": "test-image", "network_mode": "host", "completed_action": "stop"},
            "workers": [
                {
                    "name": "mock-worker",
                    "type": "mock",
                    "task_types": [
                        "collection_reason",
                        "collection_explore",
                        "vulnerability_reason",
                        "vulnerability_explore",
                        "report",
                    ],
                    "max_running": 1,
                    "priority": 0,
                }
            ],
        }
    )

    assert config.workers[0].task_types == [
        "collection_reason",
        "collection_explore",
        "vulnerability_reason",
        "vulnerability_explore",
        "report",
    ]


def test_mock_driver_emits_new_task_contract() -> None:
    worker = DispatchConfig.model_validate(
        {
            "server": "http://127.0.0.1:8000",
            "runtime": {
                "interval": 60,
                "max_workers": 1,
                "max_running_projects": 1,
                "max_project_workers": 1,
                "healthcheck_timeout": 5,
                "prompt_group": "mock",
            },
            "tasks": {
                "reason": {"timeout": 10, "max_tasks": 1},
                "explore": {"timeout": 10, "conclude_timeout": 5},
            },
            "container": {"image": "test", "network_mode": "host", "completed_action": "stop"},
            "workers": [
                {
                    "name": "mock",
                    "type": "mock",
                    "task_types": ["collection_reason"],
                    "max_running": 1,
                    "priority": 0,
                }
            ],
        }
    ).workers[0]

    prompt = json.dumps(
        {
            "phase": "reason",
            "task_mode": "collection",
            "fact_ids": ["origin"],
            "open_tasks": [],
            "max_tasks": 1,
        }
    )
    argv = MockDriver().build_execute(worker, prompt, None).argv

    assert argv[:2] == ["python3", "-c"]
