from __future__ import annotations

from importlib import resources

from cairn.dispatcher.config import validate_prompt_resources
from cairn.dispatcher.prompting import load_prompt


def _read_default_prompt(task_mode: str, name: str) -> str:
    return (
        resources.files("cairn.dispatcher.prompts")
        .joinpath("default")
        .joinpath(task_mode)
        .joinpath(name)
        .read_text(encoding="utf-8")
    )


def _read_mock_prompt(name: str) -> str:
    return resources.files("cairn.dispatcher.prompts").joinpath("mock").joinpath(name).read_text(encoding="utf-8")


def _read_default_collection_prompt(name: str) -> str:
    return _read_default_prompt("collection", name)


def _read_default_vulnerability_prompt(name: str) -> str:
    return _read_default_prompt("vulnerability", name)


def _without_markdown_code_ticks(text: str) -> str:
    return text.replace("`", "")


def test_default_prompts_have_required_placeholders() -> None:
    validate_prompt_resources("default")


def test_mock_reason_prompt_uses_task_mode_not_project_kind() -> None:
    prompt = _read_mock_prompt("reason.md")

    assert '"task_mode": "{task_mode}"' in prompt
    assert "project_kind" not in prompt


def test_collection_reason_prompt_mentions_auth_scope_mapping_and_no_complete() -> None:
    prompt = _read_default_collection_prompt("reason.md")
    plain_prompt = _without_markdown_code_ticks(prompt)

    assert "collection R worker" in prompt
    assert "recon 项目" not in prompt
    assert "auth_scope" in prompt
    assert "anonymous" in prompt
    assert "authenticated" in prompt
    assert "不要输出 complete" in plain_prompt or "Reason validator 会拒绝任何 complete payload" in plain_prompt
    assert "功能地图" in prompt
    assert "[feature_mapping]" in prompt
    assert "route/API" in prompt
    assert "snapshot" not in prompt.lower()
    assert "tasks" in prompt


def test_collection_explore_prompt_forbids_findings_and_report() -> None:
    prompt = _without_markdown_code_ticks(_read_default_collection_prompt("explore.md"))

    assert "不包含 findings" in prompt or "不要包含 findings" in prompt or "不要输出 findings" in prompt
    assert "不要输出 report" in prompt or "不要包含 report" in prompt
    assert "evidence" in prompt
    assert "schema_version" in prompt
    assert "reproduce" in prompt


def test_vulnerability_reason_prompt_mentions_collection_facts_and_task_contract() -> None:
    prompt = _read_default_vulnerability_prompt("reason.md")

    assert "vulnerability R worker" in prompt
    assert "collection" in prompt
    assert "vulnerability_task" in prompt
    assert "recon snapshot" not in prompt
    assert "decision=noop" in prompt
    assert "tasks" in prompt


def test_vulnerability_explore_prompt_allows_findings_and_requires_evidence() -> None:
    prompt = _read_default_vulnerability_prompt("explore.md")

    assert "findings" in prompt
    assert "evidence" in prompt
    assert "可提交" in prompt


def test_report_prompt_lives_under_report_mode() -> None:
    prompt = load_prompt("default", "report.md", "report")

    assert prompt == _read_default_prompt("report", "report.md")
    assert '"report"' in prompt
    assert "report_markdown" not in prompt


def test_default_prompts_prefer_chinese_readable_output_without_protocol_translation() -> None:
    prompt_names = {
        "collection": ("reason.md", "explore.md", "explore_conclude.md"),
        "vulnerability": ("reason.md", "explore.md", "explore_conclude.md"),
    }

    for task_mode, names in prompt_names.items():
        for name in names:
            prompt = _read_default_prompt(task_mode, name)

            assert "accepted" in prompt
            assert "data" in prompt
            assert "JSON" in prompt
            assert "intents" not in prompt
            assert "validation" not in prompt
