from __future__ import annotations

from pathlib import Path
import re

import yaml


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "prompts" / "collection"
FIXTURE_NAMES = ("initial_origin.yaml", "with_open_intents.yaml", "ready_for_judge.yaml")
SENSITIVE_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----"),
    re.compile(r"\b(?:password|token|secret|api[_-]?key)\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"\b[A-Za-z0-9._%+-]+@(?!example\.test\b)[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
)
URL_HOST_PATTERN = re.compile(r"https?://([A-Za-z0-9.-]+\.[A-Za-z]{2,})(?::\d+)?(?:/|\b)")


def _load_fixture(name: str) -> dict:
    path = FIXTURE_DIR / name
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _assert_common_collection_graph(data: dict) -> None:
    assert data["project"]["project_kind"] == "vuln"
    assert data["project"]["auth_mode"] == "dual"
    assert data["project"]["origin"].endswith(".example.test/")
    assert "recon" not in data
    assert data["collection"]["judge_status"] in {"not_judged", "ready", "not_ready", "blocked"}
    assert {"reason_rounds", "explore_rounds", "stable_rounds"} <= data["collection"].keys()
    assert "max_reason_rounds" not in data["collection"]
    assert isinstance(data["facts"], list)
    assert data["facts"][0]["id"] == "origin"
    assert "findings" not in data
    assert "accounts" not in data
    assert "reports" not in data


def test_collection_prompt_fixtures_are_valid_yaml_and_have_required_fields() -> None:
    for name in FIXTURE_NAMES:
        data = _load_fixture(name)
        _assert_common_collection_graph(data)


def test_initial_origin_fixture_contains_only_origin_fact_and_no_intents() -> None:
    data = _load_fixture("initial_origin.yaml")

    assert [fact["id"] for fact in data["facts"]] == ["origin"]
    assert "intents" not in data


def test_with_open_intents_fixture_has_anonymous_and_authenticated_open_intents() -> None:
    data = _load_fixture("with_open_intents.yaml")
    intents = data["intents"]

    assert {intent["auth_scope"] for intent in intents} == {"anonymous", "authenticated"}
    assert all(intent["to"] is None for intent in intents)
    assert all(intent["worker"] is None for intent in intents)
    assert all("auth_scope=" in intent["description"] for intent in intents)
    assert all(intent["task_mode"] == "collection" for intent in intents)


def test_ready_for_judge_fixture_has_judge_relevant_collection_coverage() -> None:
    data = _load_fixture("ready_for_judge.yaml")
    fact_text = "\n".join(fact["description"] for fact in data["facts"])

    assert len(data["facts"]) >= 5
    assert "asset" in fact_text.lower()
    assert "endpoint" in fact_text.lower()
    assert "auth" in fact_text.lower()
    assert "candidate" in fact_text.lower()
    assert {intent["auth_scope"] for intent in data["intents"]} == {"anonymous", "authenticated"}
    assert all(intent["to"] for intent in data["intents"])
    assert all(intent["task_mode"] == "collection" for intent in data["intents"])


def test_collection_prompt_fixtures_do_not_include_real_targets_or_secrets() -> None:
    for name in FIXTURE_NAMES:
        text = (FIXTURE_DIR / name).read_text(encoding="utf-8")
        for pattern in SENSITIVE_PATTERNS:
            assert not pattern.search(text), f"{name} contains sensitive-looking text"
        hosts = {match.group(1) for match in URL_HOST_PATTERN.finditer(text)}
        assert hosts
        assert all(host.endswith(".example.test") for host in hosts)
