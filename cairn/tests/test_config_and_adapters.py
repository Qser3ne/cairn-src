from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest
from pydantic import ValidationError

from cairn.dispatcher.config import DispatchConfig, WorkerConfig, validate_prompt_resources
from cairn.dispatcher.workers.adapters.codex import CodexDriver
from cairn.dispatcher.workers.adapters.mock import MockDriver
from cairn.dispatcher.workers.adapters.pi import PiDriver

from conftest import make_config


def test_dispatch_config_merges_common_env_with_worker_override() -> None:
    payload = make_config().model_dump()
    payload["common_env"] = {"SHARED": "common", "OVERRIDE": "common"}
    payload["workers"][0]["env"] = {"OVERRIDE": "worker"}

    config = DispatchConfig.model_validate(payload)

    assert config.workers[0].env["SHARED"] == "common"
    assert config.workers[0].env["OVERRIDE"] == "worker"


def test_dispatch_config_defaults_worker_healthcheck_and_rejects_unknown_mode() -> None:
    payload = make_config().model_dump()
    payload["runtime"].pop("worker_healthcheck")

    assert DispatchConfig.model_validate(payload).runtime.worker_healthcheck == "startup_only"

    payload["runtime"]["worker_healthcheck"] = "sometimes"
    with pytest.raises(ValidationError):
        DispatchConfig.model_validate(payload)


def test_dispatch_config_defaults_container_init_and_allows_disable() -> None:
    payload = make_config().model_dump()
    payload["container"].pop("init", None)

    assert DispatchConfig.model_validate(payload).container.init is True

    payload["container"]["init"] = False
    assert DispatchConfig.model_validate(payload).container.init is False


@pytest.mark.parametrize(
    ("section", "field"),
    [
        ("runtime", "surprise"),
        ("container", "surprise"),
    ],
)
def test_dispatch_config_rejects_unknown_top_level_nested_fields(section: str, field: str) -> None:
    payload = make_config().model_dump()
    payload[section][field] = True

    with pytest.raises(ValidationError):
        DispatchConfig.model_validate(payload)


@pytest.mark.parametrize(
    ("task", "field"),
    [
        ("reason", "surprise"),
        ("explore", "surprise"),
        ("judge", "surprise"),
        ("report", "surprise"),
        ("fork_seed", "surprise"),
    ],
)
def test_dispatch_config_rejects_unknown_task_fields(task: str, field: str) -> None:
    payload = make_config().model_dump()
    payload["tasks"][task][field] = True

    with pytest.raises(ValidationError):
        DispatchConfig.model_validate(payload)


def test_dispatch_config_rejects_duplicate_workers_and_excess_project_parallelism() -> None:
    payload = make_config().model_dump()
    payload["workers"].append(dict(payload["workers"][0]))
    with pytest.raises(ValidationError, match="worker names must be unique"):
        DispatchConfig.model_validate(payload)

    payload = make_config().model_dump()
    payload["runtime"]["max_project_workers"] = 3
    with pytest.raises(ValidationError, match="max_project_workers cannot exceed max_workers"):
        DispatchConfig.model_validate(payload)


def test_pi_worker_rejects_invalid_context_window() -> None:
    with pytest.raises(ValidationError, match="PI_MODEL_CONTEXT_WINDOW must be greater than 0"):
        WorkerConfig.model_validate(
            {
                "name": "pi",
                "type": "pi",
                "task_types": ["validation_explore"],
                "max_running": 1,
                "priority": 0,
                "env": {
                    "PI_MODEL": "model",
                    "PI_BASE_URL": "http://api",
                    "PI_API_KEY": "secret",
                    "PI_PROVIDER_API": "openai-completions",
                    "PI_MODEL_CONTEXT_WINDOW": "0",
                },
            }
        )


def test_mock_worker_rejects_unknown_phase_configuration() -> None:
    with pytest.raises(ValidationError, match="unsupported mock env keys"):
        WorkerConfig.model_validate(
            {
                "name": "mock",
                "type": "mock",
                "task_types": ["validation_explore"],
                "max_running": 1,
                "priority": 0,
                "env": {"MOCK_UNKNOWN": "{}"},
            }
        )


def test_worker_accepts_mode_specific_task_types() -> None:
    worker = WorkerConfig.model_validate(
        {
            "name": "mock",
            "type": "mock",
            "task_types": [
                "collection_reason",
                "collection_explore",
                "validation_reason",
                "validation_explore",
                "report",
            ],
            "max_running": 1,
            "priority": 0,
        }
    )

    assert worker.task_types == [
        "collection_reason",
        "collection_explore",
        "validation_reason",
        "validation_explore",
        "report",
    ]


@pytest.mark.parametrize("legacy_task_type", ["reason", "explore", "judge", "fork_seed"])
def test_worker_rejects_legacy_task_types(legacy_task_type: str) -> None:
    with pytest.raises(ValidationError):
        WorkerConfig.model_validate(
            {
                "name": "mock",
                "type": "mock",
                "task_types": [legacy_task_type],
                "max_running": 1,
                "priority": 0,
            }
        )


def test_dispatch_example_yaml_matches_current_schema() -> None:
    config_path = Path(__file__).resolve().parents[2] / "dispatch.example.yaml"

    config = DispatchConfig.load(config_path)

    assert config.workers


def test_mock_worker_accepts_collection_and_validation_phase_configuration() -> None:
    worker = WorkerConfig.model_validate(
        {
            "name": "mock",
            "type": "mock",
            "task_types": ["collection_reason", "validation_reason"],
            "max_running": 1,
            "priority": 0,
            "env": {
                "MOCK_COLLECTION_REASON": json.dumps({"delay": [0, 0], "outcomes": {"intent": "1.0"}}),
                "MOCK_VALIDATION_REASON": json.dumps({"delay": [0, 0], "outcomes": {"intent": "0.0", "stable": "1.0"}}),
            },
        }
    )

    assert worker.env["MOCK_COLLECTION_REASON"]
    assert worker.env["MOCK_VALIDATION_REASON"]


def _run_mock_worker(worker: WorkerConfig, prompt: dict) -> subprocess.CompletedProcess[str]:
    command = MockDriver().build_execute(worker, json.dumps(prompt), None)
    return subprocess.run(command.argv, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def test_mock_driver_uses_collection_reason_config_at_runtime() -> None:
    worker = WorkerConfig.model_validate(
        {
            "name": "mock",
            "type": "mock",
            "task_types": ["collection_reason"],
            "max_running": 1,
            "priority": 0,
            "env": {
                "MOCK_REASON": json.dumps({"delay": [0, 0], "outcomes": {"intent": "0.0", "noop": "1.0"}}),
                "MOCK_COLLECTION_REASON": json.dumps({"delay": [0, 0], "outcomes": {"intent": "1.0"}}),
            },
        }
    )

    result = _run_mock_worker(
        worker,
        {
            "phase": "reason",
            "task_mode": "collection",
            "fact_ids": ["origin"],
            "open_intents": [{"id": "i001"}],
            "max_intents": 1,
        },
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["data"]["intents"]


def test_mock_driver_uses_validation_reason_config_at_runtime() -> None:
    worker = WorkerConfig.model_validate(
        {
            "name": "mock",
            "type": "mock",
            "task_types": ["validation_reason"],
            "max_running": 1,
            "priority": 0,
            "env": {
                "MOCK_REASON": json.dumps({"delay": [0, 0], "outcomes": {"intent": "0.0", "noop": "1.0"}}),
                "MOCK_VALIDATION_REASON": json.dumps({"delay": [0, 0], "outcomes": {"intent": "0.0", "stable": "1.0"}}),
            },
        }
    )

    result = _run_mock_worker(
        worker,
        {
            "phase": "reason",
            "task_mode": "validation",
            "fact_ids": ["f001"],
            "open_intents": [{"id": "i001"}],
            "max_intents": 1,
        },
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["data"] == {"decision": "no_new_high_value", "intents": []}


@pytest.mark.parametrize(
    ("task_mode", "phase", "intent_id", "expected_description"),
    [
        ("collection", "explore_execute", "i001", "Collection fact from i001: mapped feature surface, API route, and auth boundary."),
        ("validation", "explore_execute", "i002", "Validation fact from i002: confirmed reportable authorization weakness."),
        ("collection", "explore_conclude", "i003", "Collection fact from i003: mapped feature surface, API route, and auth boundary."),
        ("validation", "explore_conclude", "i004", "Validation fact from i004: confirmed reportable authorization weakness."),
    ],
)
def test_mock_driver_uses_task_mode_explore_configs_at_runtime(
    task_mode: str,
    phase: str,
    intent_id: str,
    expected_description: str,
) -> None:
    worker = WorkerConfig.model_validate(
        {
            "name": "mock",
            "type": "mock",
            "task_types": ["collection_explore", "validation_explore"],
            "max_running": 1,
            "priority": 0,
            "env": {
                "MOCK_EXPLORE_EXECUTE": json.dumps({"delay": [0, 0], "outcomes": {"fact": "0.0", "command_fail": "1.0"}}),
                "MOCK_COLLECTION_EXPLORE_EXECUTE": json.dumps({"delay": [0, 0], "outcomes": {"fact": "1.0"}}),
                "MOCK_VALIDATION_EXPLORE_EXECUTE": json.dumps({"delay": [0, 0], "outcomes": {"fact": "1.0"}}),
                "MOCK_EXPLORE_CONCLUDE": json.dumps({"delay": [0, 0], "outcomes": {"fact": "0.0", "command_fail": "1.0"}}),
                "MOCK_COLLECTION_EXPLORE_CONCLUDE": json.dumps({"delay": [0, 0], "outcomes": {"fact": "1.0"}}),
                "MOCK_VALIDATION_EXPLORE_CONCLUDE": json.dumps({"delay": [0, 0], "outcomes": {"fact": "1.0"}}),
            },
        }
    )

    result = _run_mock_worker(worker, {"phase": phase, "task_mode": task_mode, "intent_id": intent_id})

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["data"]["description"] == expected_description


def test_bundled_prompt_groups_have_required_placeholders() -> None:
    validate_prompt_resources("default")
    validate_prompt_resources("mock")


def test_pi_driver_models_json_and_execute_argv_include_context_window_and_tools() -> None:
    worker = WorkerConfig.model_validate(
        {
            "name": "pi-worker",
            "type": "pi",
            "task_types": ["validation_explore"],
            "max_running": 1,
            "priority": 0,
            "env": {
                "PI_MODEL": "model",
                "PI_BASE_URL": "http://api",
                "PI_API_KEY": "secret",
                "PI_PROVIDER_API": "openai-completions",
                "PI_MODEL_CONTEXT_WINDOW": "131072",
            },
        }
    )

    result = PiDriver().build_execute(worker, "prompt", None)
    models = json.loads(result.argv[5])

    assert models["providers"]["cairn"]["models"][0]["contextWindow"] == 131072
    assert "--tools" in result.argv
    assert result.argv[-2:] == ["-p", "prompt"]


def test_codex_driver_execute_argv_passes_model_endpoint_and_prompt() -> None:
    worker = WorkerConfig.model_validate(
        {
            "name": "codex",
            "type": "codex",
            "task_types": ["validation_reason"],
            "max_running": 1,
            "priority": 0,
            "env": {
                "CODEX_MODEL": "gpt-test",
                "CODEX_BASE_URL": "http://api/v1",
                "OPENAI_API_KEY": "secret",
            },
        }
    )

    argv = CodexDriver().build_execute(worker, "prompt", None).argv

    assert "--model" in argv
    assert "gpt-test" in argv
    assert 'model_providers.cairn.base_url="http://api/v1"' in argv
    assert argv[-2:] == ["--", "prompt"]
