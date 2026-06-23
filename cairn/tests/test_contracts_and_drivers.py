from __future__ import annotations

import json

import pytest

from cairn.dispatcher.contracts import (
    parse_json_output,
    validate_explore_payload,
    validate_fork_seed_payload,
    validate_reason_payload,
)
from cairn.dispatcher.runtime.process import ManagedProcess
from cairn.dispatcher.workers.adapters.pi import PiDriver


def test_parse_json_output_extracts_object_from_markdown_noise() -> None:
    assert parse_json_output('result:\n```json\n{"accepted": true, "data": {}}\n```') == {
        "accepted": True,
        "data": {},
    }


def test_reason_payload_limits_number_of_intents() -> None:
    kind, intents = validate_reason_payload(
        {
            "accepted": True,
            "data": {
                "intents": [
                    {"from": ["f001"], "description": "one"},
                    {"from": ["f001"], "description": "two"},
                ]
            },
        },
        open_intents_empty=True,
        max_intents=1,
    )

    assert kind == "intents"
    assert intents == [{"from": ["f001"], "description": "one"}]


def test_recon_reason_payload_requires_auth_scope() -> None:
    with pytest.raises(ValueError, match="auth_scope is required"):
        validate_reason_payload(
            {
                "accepted": True,
                "data": {"intents": [{"from": ["origin"], "description": "baseline"}]},
            },
            open_intents_empty=True,
            max_intents=2,
            require_auth_scope=True,
        )


def test_vuln_reason_payload_allows_missing_auth_scope() -> None:
    kind, intents = validate_reason_payload(
        {
            "accepted": True,
            "data": {"intents": [{"from": ["f001"], "description": "verify upload"}]},
        },
        open_intents_empty=True,
        max_intents=2,
    )

    assert kind == "intents"
    assert intents == [{"from": ["f001"], "description": "verify upload"}]


def test_fork_seed_payload_requires_existing_source_fact() -> None:
    graph_yaml = """
project:
  project_kind: recon
facts:
- id: origin
  description: https://target.test
- id: f001
  description: upload endpoint
"""
    kind, data = validate_fork_seed_payload(
        {
            "accepted": True,
            "data": {
                "seed_facts": [
                    {
                        "title": "Upload surface",
                        "auth_scope": "anonymous",
                        "candidate_type": "api_surface",
                        "derived_from": ["f001"],
                        "feature_summary": "上传图片功能",
                        "user_actions": ["选择图片", "提交上传"],
                        "routes": ["/upload"],
                        "apis": ["POST /api/upload"],
                        "vuln_validation_focus": ["文件类型校验"],
                        "known_constraints": ["anonymous only"],
                        "evidence_refs": ["/tmp/evidence/upload.png"],
                        "description": "candidate_summary:\n- upload endpoint",
                    }
                ]
            },
        },
        graph_yaml,
    )

    assert kind == "fork_seed"
    assert data == {
        "seed_facts": [
            {
                "title": "Upload surface",
                "auth_scope": "anonymous",
                "candidate_type": "api_surface",
                "derived_from": ["f001"],
                "description": "candidate_summary:\n- upload endpoint",
                "feature_summary": "上传图片功能",
                "user_actions": ["选择图片", "提交上传"],
                "routes": ["/upload"],
                "apis": ["POST /api/upload"],
                "vuln_validation_focus": ["文件类型校验"],
                "known_constraints": ["anonymous only"],
                "evidence_refs": ["/tmp/evidence/upload.png"],
            }
        ]
    }


def test_fork_seed_payload_rejects_unknown_source_fact() -> None:
    with pytest.raises(ValueError, match="unknown source fact missing"):
        validate_fork_seed_payload(
            {
                "accepted": True,
                "data": {
                    "seed_facts": [
                        {
                            "title": "Missing",
                            "auth_scope": "anonymous",
                            "candidate_type": "api_surface",
                            "derived_from": ["missing"],
                            "description": "invalid",
                        }
                    ]
                },
            },
            "facts:\n- id: f001\n  description: known\n",
        )


def test_reason_payload_requires_intent_when_none_are_open() -> None:
    with pytest.raises(ValueError, match="intents is required"):
        validate_reason_payload(
            {"accepted": True, "data": {}},
            open_intents_empty=True,
            max_intents=3,
        )


def test_explore_payload_rejects_planning_text() -> None:
    with pytest.raises(ValueError):
        validate_explore_payload(parse_json_output("Need inspect files and keep working."))


def test_explore_payload_accepts_feature_surface_metadata() -> None:
    kind, data = validate_explore_payload(
        {
            "accepted": True,
            "data": {
                "description": "intent_summary: map upload feature",
                "fact_type": "feature_surface",
                "title": "Upload page",
                "summary": "用户可以选择图片并提交上传",
                "details": {
                    "user_actions": ["选择图片", "提交上传"],
                    "routes": ["/upload"],
                    "apis": ["POST /api/upload"],
                },
                "findings": [{"title": "Potential upload issue"}],
            },
        }
    )

    assert kind == "fact"
    assert data == {
        "description": "intent_summary: map upload feature",
        "fact_type": "feature_surface",
        "title": "Upload page",
        "summary": "用户可以选择图片并提交上传",
        "details": {
            "user_actions": ["选择图片", "提交上传"],
            "routes": ["/upload"],
            "apis": ["POST /api/upload"],
        },
    }


def test_pi_driver_extracts_session_and_last_assistant_text() -> None:
    driver = PiDriver()
    stdout = "\n".join(
        [
            json.dumps({"type": "session", "id": "session-123"}),
            json.dumps(
                {
                    "type": "turn_end",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": '{"accepted":true,"data":{}}'}],
                    },
                }
            ),
        ]
    )

    assert driver.extract_session(None, stdout, "") == "session-123"
    assert driver.extract_response_text(stdout, "") == '{"accepted":true,"data":{}}'


def test_close_stream_closes_response_even_when_stream_close_fails() -> None:
    class Response:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    class Stream:
        def __init__(self) -> None:
            self._response = Response()

        def close(self) -> None:
            raise ValueError("already closed")

    stream = Stream()
    ManagedProcess._close_stream(stream)

    assert stream._response.closed
